#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/opt/vcg-downloader"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

mkdir -p "$APP_DIR" "$DESKTOP_DIR" "$ICON_DIR"
cp -a "$SOURCE_DIR/." "$APP_DIR/"
install -m 644 "$SOURCE_DIR/vcg-downloader.png" "$ICON_DIR/vcg-downloader.png"

cat > "$DESKTOP_DIR/vcg-downloader.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=高清图片下载
Comment=多来源高清图片批量下载工具
Exec=$APP_DIR/VCGDownloader
Icon=vcg-downloader
Terminal=false
Categories=Network;Graphics;Utility;
StartupNotify=true
EOF

chmod +x "$APP_DIR/VCGDownloader"
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 \
    || true

printf '安装完成，可从应用菜单启动“高清图片下载”。\n'
