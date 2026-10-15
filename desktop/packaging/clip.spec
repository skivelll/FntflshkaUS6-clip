from pathlib import Path

desktop = Path(SPECPATH).parent
package = desktop / "src" / "fntflshka_us6_clip"
a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(desktop / "src")],
    binaries=[],
    datas=[(str(package / "resources" / "theme.qss"), "fntflshka_us6_clip/resources")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="FntflshkaUS6 clip", debug=False, strip=False,
          upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name="FntflshkaUS6 clip")
