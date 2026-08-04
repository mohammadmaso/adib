#!/usr/bin/env bash
# Populate apps/desktop/src-tauri/resources/fonts/ with the OFL fonts the
# renderer ships. SIL-licensed only, so redistribution in the packaged app is
# clean; Typst gets pointed at this directory with --font-path.
#
#   ./scripts/prepare-fonts.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FONT_DIR="$ROOT/apps/desktop/src-tauri/resources/fonts"
mkdir -p "$FONT_DIR"

fetch() {
  local out="$1" url="$2"
  if [[ -s "$FONT_DIR/$out" ]]; then
    echo "==> $out already present, skipping"
    return
  fi
  echo "==> fetching $out"
  curl -fsSL -o "$FONT_DIR/$out" "$url"
}

# Persian body text.
fetch "Vazirmatn-Regular.ttf" \
  "https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/Vazirmatn-Regular.ttf"
fetch "Vazirmatn-Bold.ttf" \
  "https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/Vazirmatn-Bold.ttf"

# Arabic, literary register (naskh script).
fetch "NotoNaskhArabic-Regular.ttf" \
  "https://github.com/google/fonts/raw/main/ofl/notonaskharabic/NotoNaskhArabic%5Bwght%5D.ttf"

# Latin fallback for LTR islands (technical terms, code) inside RTL pages.
fetch "NotoSerif-Regular.ttf" \
  "https://github.com/google/fonts/raw/main/ofl/notoserif/NotoSerif%5Bwdth,wght%5D.ttf"

# Code blocks and inline code spans, every language direction.
fetch "JetBrainsMono-Regular.ttf" \
  "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/ttf/JetBrainsMono-Regular.ttf"

echo "==> done:"
ls -l "$FONT_DIR"
