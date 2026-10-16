"""Build a portable Windows folder with the active repo-local interpreter."""
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import argparse


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable natively on Windows; source execution remains cross-platform.")
    root = Path(__file__).resolve().parents[1]
    desktop = root / "desktop"
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist-dir',type=Path,help='Alternative output folder for a side-by-side portable build')
    args=parser.parse_args()
    dist=(args.dist_dir or desktop/'dist').resolve()
    try:
        packages = {name: version(name) for name in
                    ("PyInstaller", "pyinstaller-hooks-contrib", "PySide6", "Pillow", "pyserial")}
    except PackageNotFoundError:
        raise SystemExit("Install desktop/requirements-build.txt and the desktop package in .venv first.")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(dist), "--workpath", str(desktop / "build"),
                    str(desktop / "packaging" / "clip.spec")], cwd=root, check=True)
    folder = dist / "FntflshkaUS6 clip"
    shutil.copyfile(desktop / "packaging" / "PORTABLE_README.txt", folder / "README.txt")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, text=True, capture_output=True)
    manifest = {"python": platform.python_version(), "platform": platform.platform(),
                "packages": packages, "source_commit": revision.stdout.strip(),
                "source_modified": bool(dirty.stdout.strip()),
                "exe_sha256": hashlib.sha256((folder / "FntflshkaUS6 clip.exe").read_bytes()).hexdigest()}
    (folder / "build-info.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = shutil.make_archive(str(dist / "FntflshkaUS6-clip-windows-x64"), "zip",
                                  root_dir=folder.parent, base_dir=folder.name)
    print(f"Portable app: {folder}\nArchive: {archive}")


if __name__ == "__main__":
    main()
