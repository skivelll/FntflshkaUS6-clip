"""Exercise a RAM-backed LittleFS 2.11 model, never a device or user's files."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from fntflshka_us6_clip.core.capacity import LIBRARY_MAX, BLOCK_SIZE, MAX_SCENES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, help='Isolated pip --target directory')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.runtime:
        sys.path.insert(0, str(args.runtime.resolve()))
    from littlefs import LittleFS, UserContext, __LFS_VERSION__, __LFS_DISK_VERSION__
    if __LFS_VERSION__ != (2, 11) or __LFS_DISK_VERSION__ != (2, 1):
        raise RuntimeError('Model must match firmware LittleFS 2.11 / disk 2.1')
    total = 1984 * 1024
    geometry = dict(block_size=4096, block_count=total // 4096, read_size=128,
                    prog_size=128, cache_size=512, lookahead_size=128, block_cycles=512)
    report = dict(littlefs=list(__LFS_VERSION__), geometry=geometry, limit=LIBRARY_MAX,
                  scope='RAM model: not physical brownout or firmware execution', cases=[])

    class Context(UserContext):
        def __init__(self):
            super().__init__(buffsize=total)
            self.armed, self.writes, self.cuts = False, 0, []

        def prog(self, cfg, block, off, data):
            result = super().prog(cfg, block, off, data)
            if self.armed:
                self.writes += 1
                # Snapshots of actual persisted flash bytes, including incomplete commits.
                if self.writes <= 8 or self.writes % 50 == 0:
                    self.cuts.append(bytes(self.buffer))
            return result

    def blob(seed, length):
        block = hashlib.sha256(str(seed).encode()).digest()
        return (block * ((length + 31) // 32))[:length]

    for count in (1, 8, MAX_SCENES):
        context = Context()
        fs = LittleFS(context=context, mount=False, **geometry)
        fs.format(); fs.mount(); fs.mkdir('scenes')
        blocks, remainder = divmod(LIBRARY_MAX // BLOCK_SIZE, count)
        sizes = [(blocks + (i < remainder)) * BLOCK_SIZE for i in range(count)]
        records = []
        for i in range(1, count + 1):
            data = blob(i, sizes[i-1])
            name = f'scenes/{i:04}a.bin'
            with fs.open(name, 'wb') as file:
                file.write(data)
            records.append(dict(id=i, file=name, order=i-1, type=2, width=320, height=172,
                                checksum=0xffffffff, title='"\\' * 24))
        def manifest():
            return json.dumps(dict(version=1, active=1, scenes=records),
                              ensure_ascii=False, separators=(',', ':')).encode()
        old_json = manifest()
        with fs.open('manifest.json', 'wb') as file:
            file.write(old_json)
        # Keep playlist/temp worst-case overhead present throughout.
        with fs.open('playlist.bin', 'wb') as file:
            file.write(blob('playlist', 10 + MAX_SCENES * 4 + 4))
        baseline = fs.used_block_count * BLOCK_SIZE
        size = sizes[0]
        old_data, new_data = blob(1, size), blob('replacement', size)
        with fs.open('scenes/upload.tmp', 'wb', buffering=0) as file:
            file.write(new_data)
        peak = max(baseline, fs.used_block_count * BLOCK_SIZE)
        context.armed = True
        context.cuts.append(bytes(context.buffer))
        fs.rename('scenes/upload.tmp', 'scenes/0001b.bin')
        context.cuts.append(bytes(context.buffer))
        records[0]['file'] = 'scenes/0001b.bin'
        new_json = manifest()
        with fs.open('manifest.tmp', 'wb', buffering=0) as file:
            file.write(new_json)
        peak = max(peak, fs.used_block_count * BLOCK_SIZE)
        context.cuts.append(bytes(context.buffer))
        fs.rename('manifest.tmp', 'manifest.json')
        context.cuts.append(bytes(context.buffer))
        peak = max(peak, fs.used_block_count * BLOCK_SIZE)
        fs.remove('scenes/0001a.bin')
        context.cuts.append(bytes(context.buffer))
        context.armed = False
        # Each snapshot mounts independently; manifest may be old or new, never partial.
        for snapshot in context.cuts:
            recovered = LittleFS(context=UserContext(buffer=bytearray(snapshot)), mount=False, **geometry)
            recovered.mount()  # No format-on-mount-failure fallback in verification.
            with recovered.open('manifest.json', 'rb') as file:
                current = file.read()
            assert current in (old_json, new_json)
            scene = json.loads(current)['scenes'][0]
            with recovered.open(scene['file'], 'rb') as file:
                assert file.read() == (old_data if current == old_json else new_data)
            # All unrelated scenes survive every snapshot.
            for i in range(2, count + 1):
                with recovered.open(f'scenes/{i:04}a.bin', 'rb') as file:
                    assert file.read() == blob(i, sizes[i-1])
            recovered.unmount()
        assert peak < total
        case = dict(scenes=count, library_bytes=LIBRARY_MAX, asset_bytes=size,
                    manifest_bytes=len(new_json), baseline_used=baseline, peak_used=peak,
                    peak_free=total-peak, recovered_snapshots=len(context.cuts))
        report['cases'].append(case)
        print(json.dumps(case), flush=True)
        fs.unmount()
    report['ok'] = True
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
