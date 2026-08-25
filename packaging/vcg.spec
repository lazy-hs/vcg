import sys
from pathlib import Path


# PyInstaller exposes SPECPATH as the directory containing this spec file.
project_root = Path(SPECPATH).resolve().parent
is_windows = sys.platform.startswith("win")

datas = [
    (str(project_root / "UI" / "main.ui"), "UI"),
    (str(project_root / "ico" / "app.ico"), "ico"),
    (str(project_root / "ico" / "head.ico"), "ico"),
    (str(project_root / "ico" / "Big.ico"), "ico"),
    (str(project_root / "ico" / "logo.png"), "ico"),
]

hiddenimports = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "xpinyin",
]

analysis = Analysis(
    [str(project_root / "ui_main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Pillow can optionally integrate with NumPy. Exclude it explicitly so a
    # build launched from Anaconda does not pull the entire MKL runtime into
    # the application even though this project never uses NumPy.
    excludes=["tkinter", "unittest", "numpy"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="VCGDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "ico" / "app.ico") if is_windows else None,
    version=(
        str(project_root / "packaging" / "windows_version_info.txt")
        if is_windows
        else None
    ),
)

bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VCGDownloader",
)
