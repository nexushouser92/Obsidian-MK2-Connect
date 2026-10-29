#!/bin/bash
# Builds "dist/MK2 Activator.app", the macOS window (Intel + Apple Silicon).
# Needs the Xcode Command Line Tools:  xcode-select --install
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/MK2 Activator.app"
rm -rf "$APP" build/mac
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build/mac

for arch in x86_64 arm64; do
  swiftc -O -target "$arch-apple-macos11" -framework Cocoa -o "build/mac/MK2-$arch" mac/MK2Activator.swift
done
lipo -create build/mac/MK2-x86_64 build/mac/MK2-arm64 -output "$APP/Contents/MacOS/MK2 Activator"

cp mac/Info.plist "$APP/Contents/Info.plist"
cp assets/obsidian-icon.icns "$APP/Contents/Resources/obsidian-icon.icns"
cp assets/obsidian-logo.png assets/Inter-Regular.ttf assets/Inter-SemiBold.ttf assets/Inter-OFL.txt "$APP/Contents/Resources/"
cp mk2_patcher_mac.py patch_mk2_mac.py "$APP/Contents/Resources/"
codesign --force --sign - "$APP"

echo "Built $APP"
echo "Open it with:  open \"$APP\""
