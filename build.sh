#!/bin/bash
set -e

# Ad-hoc build for local use (no Developer ID, no notarization).

APP_PATH="dist/Ouroboros.app"
VERSION=$(cat VERSION | tr -d '[:space:]')
DMG_NAME="Ouroboros-${VERSION}-macos.dmg"
DMG_PATH="dist/$DMG_NAME"

echo "=== Building Ouroboros.app (v${VERSION}) ==="

if [ ! -f "python-standalone/bin/python3" ]; then
    echo "ERROR: python-standalone/ not found."
    echo "Run first: bash scripts/download_python_standalone.sh"
    exit 1
fi

echo "--- Installing launcher dependencies ---"
pip install -q -r requirements-launcher.txt

echo "--- Installing agent dependencies into python-standalone ---"
python-standalone/bin/pip3 install -q -r requirements.txt

echo "--- Normalizing python-standalone symlinks for PyInstaller ---"
python3 - <<'PY'
import pathlib
import shutil

root = pathlib.Path("python-standalone")
replaced = 0

for path in sorted(root.rglob("*")):
    if not path.is_symlink():
        continue
    target = path.resolve()
    path.unlink()
    if target.is_dir():
        shutil.copytree(target, path)
    else:
        shutil.copy2(target, path)
    replaced += 1

print(f"Replaced {replaced} symlinks in python-standalone")
PY

rm -rf build dist

export PYINSTALLER_CONFIG_DIR="$PWD/.pyinstaller-cache"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

echo "--- Running PyInstaller ---"
python3 -m PyInstaller Ouroboros.spec --clean --noconfirm

echo ""
echo "=== Signing Ouroboros.app (ad-hoc) ==="
codesign --force --deep --sign - "$APP_PATH"

echo ""
echo "=== Creating DMG ==="
hdiutil create -volname Ouroboros -srcfolder "$APP_PATH" -ov -format UDZO "$DMG_PATH"

echo ""
echo "=== Done ==="
echo "App: $APP_PATH"
echo "DMG: $DMG_PATH"
echo ""
echo "To install: open $DMG_PATH, drag Ouroboros to Desktop/Applications."
echo "First launch: right-click -> Open (Gatekeeper bypass for ad-hoc signed apps)."
