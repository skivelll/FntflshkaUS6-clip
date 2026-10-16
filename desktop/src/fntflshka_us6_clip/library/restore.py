"""Explicit per-scene restore plans. No deletes or collection-level atomicity."""
from dataclasses import dataclass
from ..core.asset import parse_asset
from ..core.capacity import allocated_size
from .archive import validate


@dataclass(frozen=True)
class RestoreItem:
    source: object
    target_id: int
    previous_checksum: int | None


@dataclass(frozen=True)
class RestorePlan:
    baseline: object
    items: tuple[RestoreItem, ...]
    metadata: bool
    final_order: tuple[int, ...]
    active_id: int
    estimated_bytes: int


class RestoreFailure(Exception):
    def __init__(self, error, committed, attempted):
        self.error, self.committed, self.attempted = error, tuple(committed), attempted
        super().__init__(f"Файлы сцен с ACK: ID {list(committed)}; последняя операция у ID {attempted} не завершена. Проверь список сцен и metadata. Автоматического повтора нет.")


def make_plan(collection, snapshot, targets, metadata=True, activate=True):
    validate(collection)
    if len(targets) != len(collection.scenes):
        raise ValueError("Неверный план сопоставления сцен.")
    if metadata and not snapshot.capabilities.supports_scene_metadata:
        raise ValueError("Для восстановления имён и порядка нужна firmware 1.2.")
    existing = {s.id: s for s in snapshot.scenes}
    used = set(existing)
    selected, assigned = [], set()
    for source, target in zip(collection.scenes, targets):
        if target is None:  # Explicit skip.
            continue
        if target == "add":
            target = source.id if source.id <= snapshot.capabilities.id_limit and source.id not in used and source.id not in assigned else next((i for i in range(1, snapshot.capabilities.id_limit + 1) if i not in used and i not in assigned), None)
            if target is None:
                raise ValueError("Недостаточно свободных ID. Выбери замену конкретной сцены или пропусти её.")
        if type(target) is not int or not 1 <= target <= snapshot.capabilities.id_limit or target in assigned:
            raise ValueError("Целевая сцена назначена дважды или неверно.")
        info = parse_asset(source.asset)
        if info.get("type") == 2 and not snapshot.capabilities.supports_animation:
            raise ValueError("Для восстановления GIF нужна firmware 1.6.")
        caps = snapshot.capabilities
        if info.get('encoding') == 1 and not caps.supports_compression:
            raise ValueError('Для восстановления сжатой анимации нужна firmware 1.8.')
        if len(source.asset) > caps.max_asset_size or info["width"] > caps.display_width or info["height"] > caps.display_height:
            raise ValueError("Asset из коллекции не подходит подключённой заколке.")
        assigned.add(target)
        selected.append(RestoreItem(source, target, existing[target].checksum if target in existing else None))
    if not selected:
        raise ValueError("Выбери хотя бы одну сцену для восстановления.")
    if snapshot.capabilities.supports_library_budget:
        caps = snapshot.capabilities
        used, free, required = caps.library_used, caps.storage_free, caps.service_reserve
        count = len(existing)
        for item in selected:
            old = existing.get(item.target_id)
            size = allocated_size(len(item.source.asset))
            old_size = allocated_size(old.size) if old else 0
            projected = used - old_size + size
            if projected > caps.library_limit and not (used > caps.library_limit and projected < used):
                raise ValueError("Библиотека заполнена. Удали сцену или уменьши размер файла.")
            if size + caps.service_reserve > free:
                raise ValueError("Не хватает места для безопасной записи. Старая сцена сохранена.")
            if not old: count += 1
            if count > caps.scene_limit: raise ValueError("Достигнут безопасный бюджет записей сцен.")
            required = max(required, size + caps.service_reserve)
            used, free = projected, free - size + old_size
    else:
        # Old A/B slots are retained. Never count replacement/deletion credit in advance.
        required = sum(len(item.source.asset) + 4096 for item in selected) + 8192
        if required > snapshot.capabilities.storage_free:
            raise ValueError(f"Недостаточно памяти для плана с запасом: нужно ≈{required:,} Б, свободно {snapshot.capabilities.storage_free:,} Б.")
    final_order = tuple(s.id for s in snapshot.scenes if s.id not in assigned) + tuple(item.target_id for item in selected)
    previous_active = next((s.id for s in snapshot.scenes if s.active), None)
    mapped_active = next((item.target_id for item in selected if item.source.id == collection.active_id), None)
    active_id = mapped_active if activate and mapped_active else previous_active or selected[-1].target_id
    return RestorePlan(snapshot, tuple(selected), metadata, final_order, active_id, required)


def execute(client, plan, ready=lambda snapshot, scene, data: None,
            progress=lambda done, total: None, cancelled=lambda: False):
    committed, attempted = [], None
    try:
        if client.snapshot() != plan.baseline:
            raise ValueError("Подключение, список сцен или память изменились. Открой план заново.")
        total = sum(len(item.source.asset) for item in plan.items)
        sent = 0
        for item in plan.items:
            attempted = item.target_id
            if cancelled():
                raise InterruptedError("Восстановление остановлено.")
            current = client.snapshot()
            if (current.port, current.device_id) != (plan.baseline.port, plan.baseline.device_id):
                raise ValueError("Подключённое устройство изменилось.")
            target = next((s for s in current.scenes if s.id == item.target_id), None)
            if (target.checksum if target else None) != item.previous_checksum:
                raise ValueError("Целевая сцена изменилась. Создай новый план.")
            result = client.upload(item.source.asset,
                lambda done, size: progress(sent + done, total), cancelled,
                scene_id=item.target_id, expected_checksum=item.previous_checksum)
            committed.append(item.target_id)  # Asset ACK is independent of later metadata commits.
            after = result[2]
            scene = next(s for s in after.scenes if s.id == item.target_id)
            ready(after, scene, item.source.asset)
            sent += len(item.source.asset)
            if plan.metadata and scene.title != item.source.title:
                client.rename_scene(scene.id, scene.checksum, item.source.title)
        if cancelled():
            raise InterruptedError("Восстановление остановлено перед metadata commit.")
        after = client.snapshot()
        if plan.metadata:
            by_id = {s.id: s for s in after.scenes}
            if set(by_id) != set(plan.final_order):
                raise ValueError("Список сцен изменился во время восстановления.")
            client.reorder_scenes([(scene_id, by_id[scene_id].checksum) for scene_id in plan.final_order])
        after = client.snapshot()
        active = next(s for s in after.scenes if s.id == plan.active_id)
        if not active.active:
            client.change_scene("activate", active.id, active.checksum)
        final = client.snapshot()
        for item in plan.items:
            info = parse_asset(item.source.asset)
            scene = next(s for s in final.scenes if s.id == item.target_id)
            if (scene.checksum, scene.width, scene.height) != (info["payload_crc"], info["width"], info["height"]):
                raise ValueError("Итоговая сцена не совпадает с архивом.")
            if plan.metadata and scene.title != item.source.title.strip():
                raise ValueError("Итоговое название не совпадает с архивом.")
        if plan.metadata and tuple(s.id for s in final.scenes) != plan.final_order:
            raise ValueError("Итоговый порядок не совпадает с планом.")
        if not any(s.id == plan.active_id and s.active for s in final.scenes):
            raise ValueError("Активная сцена не совпадает с планом.")
        return final
    except Exception as error:
        raise RestoreFailure(error, committed, attempted) from error
