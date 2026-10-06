#!/usr/bin/env bash
# Build dist/BugReport-extension-<version>.zip from extension/, with manifest.json at the zip root.
# Refuses to build if extension/ holds anything that is not part of the extension.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
ext="$root/extension"
dist="$root/dist"

version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ext/manifest.json")"
[[ "$version" =~ ^[0-9]+(\.[0-9]+){1,3}$ ]] || { echo "Bad version in manifest.json: $version" >&2; exit 1; }

# The extension is flat: any subdirectory is a temp dir or cache that must not ship.
bad="$(cd "$ext" && find . -mindepth 1 \( -type d -o -type l \
  -o -name '.*' -o -name '*~' -o -name '*.swp' -o -name '*.bak' -o -name '*.orig' -o -name '*.tmp' \
  -o -name '*.pyc' -o -name '*.zip' -o -name '*.log' -o -iname 'Thumbs.db' -o -iname 'desktop.ini' \) -print)"
if [[ -n "$bad" ]]; then
  echo "Refusing to build, extension/ contains files that are not part of the extension:" >&2
  echo "$bad" >&2
  exit 1
fi

mkdir -p "$dist"
out="$dist/BugReport-extension-$version.zip"
rm -f "$out"
(cd "$ext" && zip -X -q "$out" ./*)
echo "Built $out"
(cd "$dist" && unzip -l "$(basename "$out")")
