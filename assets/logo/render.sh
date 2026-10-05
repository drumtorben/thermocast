#!/usr/bin/env bash
# Render the brand PNGs for Home Assistant from the SVG sources (needs Google Chrome + uv).
#   ./assets/logo/render.sh
set -euo pipefail
cd "$(dirname "$0")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
OUT=../../custom_components/thermocast/brand
mkdir -p "$OUT"

shot() { # svg width height scale output
  "$CHROME" --headless=new --hide-scrollbars --default-background-color=00000000 \
    --force-device-scale-factor="$4" --window-size="$2,$3" --screenshot="$OUT/$5" "file://$PWD/$1" 2>/dev/null
}

shot icon.svg 256 256 1 icon.png
shot icon.svg 256 256 2 icon@2x.png
shot icon-dark.svg 256 256 1 dark_icon.png
shot icon-dark.svg 256 256 2 dark_icon@2x.png
shot logo-light.svg 620 128 1 logo.png
shot logo-light.svg 620 128 2 logo@2x.png
shot logo-dark.svg 620 128 1 dark_logo.png
shot logo-dark.svg 620 128 2 dark_logo@2x.png

# trim the logos to their content width; @2x exactly twice the 1x size
cd ../..
uv run python - <<'PY'
from PIL import Image

base = "custom_components/thermocast/brand"
for name in ("logo", "dark_logo"):
    one = Image.open(f"{base}/{name}.png")
    one = one.crop((0, 0, one.getbbox()[2], one.size[1]))
    one.save(f"{base}/{name}.png")
    two = Image.open(f"{base}/{name}@2x.png")
    canvas = Image.new("RGBA", (one.size[0] * 2, one.size[1] * 2), (0, 0, 0, 0))
    canvas.paste(two.crop((0, 0, one.size[0] * 2, one.size[1] * 2)), (0, 0))
    canvas.save(f"{base}/{name}@2x.png")
    print(name, one.size, canvas.size)
PY
