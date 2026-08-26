import sys
from pathlib import Path


# PyInstaller exposes SPECPATH as the directory containing this spec file.
project_root = Path(SPECPATH).resolve().parent
is_windows = sys.platform.startswith("win")
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app_icon import find_app_icon


icon_directory = project_root / "ico"
selected_icon = find_app_icon(icon_directory)
if selected_icon is None:
    raise FileNotFoundError(
        "No supported icon found. Add ico/logo.ico or ico/app.ico."
    )

datas = [
    (str(project_root / "UI" / "main.ui"), "UI"),
    (str(selected_icon), "ico"),
]

print("Application icon: {0}".format(selected_icon))

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
    icon=str(selected_icon) if is_windows else None,
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
