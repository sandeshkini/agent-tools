#!/bin/bash
# Rebuild + sign the macOS cptr-input helper. A rebuild changes the cdhash, so
# macOS may drop the Accessibility grant -- re-check after.
set -e
cd "$(dirname "$0")"
APP=cptr-input.app
mkdir -p "$APP/Contents/MacOS"
if [ ! -f "$APP/Contents/Info.plist" ]; then
cat > "$APP/Contents/Info.plist" <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.sandesh.cptr-input</string>
  <key>CFBundleName</key><string>cptr-input</string>
  <key>CFBundleExecutable</key><string>cptr-input</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>2.0</string>
  <key>LSBackgroundOnly</key><true/>
  <key>LSUIElement</key><true/>
</dict></plist>
PL
fi
swiftc -O -o "$APP/Contents/MacOS/cptr-input" main.swift \
  -framework CoreGraphics -framework ApplicationServices -framework Foundation
codesign --force --sign - --identifier com.sandesh.cptr-input "$APP"
echo "built + signed $APP"
echo "if input stops working, re-grant: tccutil reset Accessibility com.sandesh.cptr-input"
