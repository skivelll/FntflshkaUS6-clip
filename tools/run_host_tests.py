"""Run existing firmware host tests with native GCC/Clang (no WSL required)."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", help="native GCC/Clang executable; default: CC, cc, gcc, clang")
    parser.add_argument("--cjson", type=Path, help="directory containing ESP-IDF cJSON.c and cJSON.h")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    compiler = args.cc or os.environ.get("CC") or next(
        (path for name in ("cc", "gcc", "clang") if (path := shutil.which(name))), None,
    )
    if not compiler:
        parser.exit(2, "Native C11 GCC/Clang is required; ESP32's RISC-V compiler cannot run host tests.\n")
    core = Path(os.environ.get("PLATFORMIO_CORE_DIR", Path.home() / ".platformio"))
    cjson = args.cjson or core / "packages/framework-espidf/components/json/cJSON"
    if not all((cjson / name).is_file() for name in ("cJSON.c", "cJSON.h")):
        parser.exit(2, "cJSON sources not found; build firmware first or pass --cjson DIRECTORY.\n")
    suites = {
        "foundation": ["tests/foundation_test.c", "src/wire.c", "src/asset_format.c", "src/compression.c",
                       "src/protocol.c", "src/manifest.c", "src/upload_guard.c", str(cjson / "cJSON.c")],
        "modes": ["tests/modes_test.c", "src/modes.c", "src/assets.c"],
        "playlist": ["tests/playlist_test.c", "src/playlist.c", "src/wire.c"],
    }
    with tempfile.TemporaryDirectory(prefix="clip-host-tests-") as temporary:
        for name, sources in suites.items():
            binary = Path(temporary) / (name + (".exe" if os.name == "nt" else ""))
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            '-DPLAYLIST_ROOT="."', '-D_POSIX_C_SOURCE=200809L',
                            "-Isrc", "-Itests/stubs", "-I", str(cjson), *sources,
                            "-lm", "-o", str(binary)], cwd=root, check=True)
            subprocess.run([str(binary)], cwd=temporary, check=True)
            print(f"{name}: PASS", flush=True)


if __name__ == "__main__":
    main()
