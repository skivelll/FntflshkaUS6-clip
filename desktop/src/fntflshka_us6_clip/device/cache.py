"""Application-lifetime asset cache. Metadata changes never reload pixels."""


class SceneCache:
    def __init__(self):
        self.device_id = None
        self.assets = {}

    @staticmethod
    def key(snapshot, scene):
        return (snapshot.port, scene.id, scene.checksum, scene.width, scene.height)

    def synchronize(self, snapshot):
        identity = snapshot.device_id or snapshot.port
        changed_device = identity != self.device_id
        if changed_device:
            self.assets.clear()
            self.device_id = identity
        valid = {self.key(snapshot, s) for s in snapshot.scenes}
        self.assets = {key: data for key, data in self.assets.items() if key in valid}
        return changed_device

    def preload(self, client, snapshot, ready, progress, cancelled=lambda: False):
        self.synchronize(snapshot)
        count = len(snapshot.scenes)
        for index, scene in enumerate(snapshot.scenes):
            if cancelled():
                raise InterruptedError("Загрузка сцен отменена.")
            key = self.key(snapshot, scene)
            if key not in self.assets:
                data = client.read_scene(scene, snapshot.capabilities,
                    lambda done, total: progress(index, count, done, total), cancelled)
                self.assets[key] = data
                ready(key, data)
            progress(index + 1, count, 0, 0)
