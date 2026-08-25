#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ARCHITECTURE="$(uname -m)"
DIST_DIR="$PROJECT_ROOT/dist/VCGDownloader"
RELEASE_DIR="$PROJECT_ROOT/release"
ARCHIVE_PATH="$RELEASE_DIR/VCGDownloader-Linux-$ARCHITECTURE.tar.gz"

cd "$PROJECT_ROOT"

if [[ "${1:-}" != "--skip-install" ]]; then
    python3 -m pip install -r requirements.txt -r requirements-build.txt
fi

python3 -m PyInstaller --noconfirm --clean packaging/vcg.spec

install -m 644 ico/logo.png "$DIST_DIR/vcg-downloader.png"
install -m 755 packaging/linux/install.sh "$DIST_DIR/install.sh"
install -m 644 packaging/linux/vcg-downloader.desktop "$DIST_DIR/vcg-downloader.desktop"

mkdir -p "$RELEASE_DIR"
rm -f -- "$ARCHIVE_PATH"
tar -C "$PROJECT_ROOT/dist" -czf "$ARCHIVE_PATH" VCGDownloader

printf 'Linux 构建完成：%s\n' "$ARCHIVE_PATH"
