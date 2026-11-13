#!/usr/bin/env bash
# Builds "Desk Guard.app". Its executable is a small C program (tools/launcher.c) that
# runs the Python app inside its own process, so macOS treats Desk Guard as a real app:
# its own Dock icon, name, menu bar and permissions.
# Usage: tools/make_app.sh [destination dir]   (default: ~/Desktop)
#
# The app uses the project in the folder that contains it if there is one, else the
# project it was built from. Code changes don't need a rebuild; a new venv Python does.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Desktop}"
APP="$DEST/Desk Guard.app"
ICNS="$PROJECT/assets/AppIcon.icns"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Desk Guard</string>
  <key>CFBundleDisplayName</key><string>Desk Guard</string>
  <key>CFBundleIdentifier</key><string>local.webcamfun.deskguard</string>
  <key>CFBundleExecutable</key><string>Desk Guard</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><false/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>Desk Guard switches and closes browser tabs for your gestures and when someone sits with you.</string>
  <key>NSCameraUsageDescription</key>
  <string>Desk Guard watches for people behind you, locks the Mac when you leave, checks your posture and reads hand gestures.</string>
</dict>
</plist>
PLIST

PY="$PROJECT/.venv/bin/python"
[ -x "$PY" ] || { echo "Run ./run desk once first to create the venv."; exit 1; }
PYBIN="$(dirname "$(readlink -f "$PY")")"
PYCONFIG="$PYBIN/python3.12-config"
PYLIB="$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
[ -x "$PYCONFIG" ] || { echo "No python3.12-config next to $PYBIN"; exit 1; }
clang -O2 -Wall $("$PYCONFIG" --includes) -DPROJECT_DIR="\"$PROJECT\"" \
  "$PROJECT/tools/launcher.c" -L "$PYLIB" -lpython3.12 -Wl,-rpath,"$PYLIB" \
  -o "$APP/Contents/MacOS/Desk Guard"

# Icon: use the committed .icns; regenerate it only if missing.
if [ ! -f "$ICNS" ]; then
  PY="$PROJECT/.venv/bin/python"
  [ -x "$PY" ] || { echo "Run ./run desk once first to create the venv."; exit 1; }
  TMP="$(mktemp -d)"
  "$PY" "$PROJECT/tools/make_icon.py" "$TMP/icon.png"
  mkdir "$TMP/AppIcon.iconset"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$TMP/icon.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$TMP/icon.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$TMP/AppIcon.iconset" -o "$ICNS"
  rm -rf "$TMP"
fi
cp "$ICNS" "$APP/Contents/Resources/AppIcon.icns"

# Ad-hoc signature so macOS remembers the camera permission for this app.
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || echo "warning: codesign failed"
touch "$APP"
echo "Built: $APP"
