#!/usr/bin/env bash
# Render every cwatts town tier into docs/assets/ as a 4× scaled PNG.
# Re-run after tweaking the tech tree, palette, or rendering primitives.
#
# Requires: pyxel (`pip install pyxel`).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/docs/assets"

echo "→ generating town screenshots into $ASSETS"
python3 "$ROOT/claudewatts.py" town --screenshot "$ASSETS"
echo "→ done. files:"
ls -1 "$ASSETS"/town-*.png 2>/dev/null || echo "  (none — pyxel may have failed to open a window)"
