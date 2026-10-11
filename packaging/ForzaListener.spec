# PyInstaller build of the capture window (listener_gui.py) -> dist/ForzaListener/ForzaListener.exe
# Run build_listener.bat. A folder build ("onedir"), no UPX and a version resource: packed
# single-file exes are what antivirus heuristics flag most often.
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "listener_gui.py")],
    pathex=[str(ROOT)],
    datas=[],                                     # the packet layout is in Listener.py
    hiddenimports=[],
    excludes=["numpy", "pandas", "fh6tuner"],      # the capture never uses the tuner
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ForzaListener",
    console=False,                                 # a window, no black console
    upx=False,
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="ForzaListener")
