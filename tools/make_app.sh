#!/usr/bin/env bash
# Builds "Desk Guard.app", a clickable launcher for the menu bar controller.
# Usage: tools/make_app.sh [destination dir]   (default: ~/Desktop)
#
# The app looks for the project in the folder that contains it (so the copy at the
# repo root works from any clone); if it lives elsewhere, e.g. on the Desktop, it
# falls back to the project path it was built from.
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
  <key>CFBundleExecutable</key><string>deskguard</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSCameraUsageDescription</key>
  <string>Desk Guard watches for people behind you, locks the Mac when you leave, checks your posture and reads hand gestures.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/deskguard" <<SCRIPT
#!/bin/bash
# Project = the folder containing this .app if it has the code, else the build-time path.
APP_PARENT="\$(cd "\$(dirname "\$0")/../../.." && pwd)"
if [ -d "\$APP_PARENT/webcam_fun" ]; then
  PROJECT="\$APP_PARENT"
else
  PROJECT="$PROJECT"
fi
PY="\$PROJECT/.venv/bin/python"
if [ ! -x "\$PY" ]; then
  osascript -e 'display alert "Desk Guard is not set up yet" message "Open Terminal in the desk-guard folder and run:  ./run desk\n\nThe first run installs everything. After that this app works."'
  exit 1
fi
cd "\$PROJECT" || exit 1
exec "\$PY" -m webcam_fun.menubar >> "\$PROJECT/desk.log" 2>&1
SCRIPT
chmod +x "$APP/Contents/MacOS/deskguard"

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
