"""Test the actual ZIP after extraction, without Python/venv on the app PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", action="store_true", help="Also run the read-only real USB check; close other app instances first")
    parser.add_argument('--archive',type=Path,help='Alternative portable ZIP to verify')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    archive = (args.archive or root / "desktop/dist/FntflshkaUS6-clip-windows-x64.zip").resolve()
    reports = root / "desktop/build/verification"
    reports.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM"):
        env.pop(name, None)
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    with tempfile.TemporaryDirectory(prefix="clip-распаковка-") as temp:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temp)
        folder = Path(temp) / "FntflshkaUS6 clip"
        exe = folder / "FntflshkaUS6 clip.exe"
        info = json.loads((folder / "build-info.json").read_text(encoding="utf-8"))
        if hashlib.sha256(exe.read_bytes()).hexdigest() != info["exe_sha256"]:
            raise SystemExit("Extracted exe does not match build-info.json")
        checks = [("smoke", ["--mock", "--smoke-test"], env, 90)]
        if args.device:
            checks.append(("device", ["--device-check"], env, 210))
        for name, options, environment, timeout in checks:
            report = reports / f"{name}.json"
            if report.exists():
                report.unlink()
            result = subprocess.run([str(exe), *options, str(report)], cwd=temp, env=environment, timeout=timeout)
            if not report.exists():
                raise SystemExit(f"{name}: no report; exit={result.returncode}")
            data = json.loads(report.read_text(encoding="utf-8"))
            if result.returncode or not data.get("ok"):
                raise SystemExit(f"{name}: failed, report={report}, error={data.get('error')}")
            print(f"{name}: PASS ({data['seconds']}s), report={report}")
        print("ZIP extraction, SHA256 and isolated app environment: PASS")


if __name__ == "__main__":
    main()
