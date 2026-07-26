#!/usr/bin/env bash
# Build a single-file, dependency-free standalone of the offline State Media FM CLI.
#
# The offline demo path (git source → fake LLM → tone TTS → plan) uses only the
# Python standard library, so it packs into a zipapp that runs anywhere with
# just `python3` — no pip install, no PYTHONPATH.
#
#   ./scripts/build_standalone.sh
#   python3 dist/statemediafm.pyz demo --repo /path/to/a/git/repo
#
# Note: `--live` (real Claude via LiteLLM) and the web server need the extras
# from pyproject.toml and a normal install — they are not in the standalone.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/build/standalone"
OUT="$ROOT/dist/statemediafm.pyz"

rm -rf "$STAGE"
mkdir -p "$STAGE" "$ROOT/dist"

# Copy the package, excluding caches.
cp -r "$ROOT/src/statemediafm" "$STAGE/statemediafm"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Archive entry point.
cat > "$STAGE/__main__.py" <<'EOF'
from statemediafm.cli import main

raise SystemExit(main())
EOF

python3 -m zipapp "$STAGE" -o "$OUT" -p "/usr/bin/env python3"
chmod +x "$OUT"
echo "Built $OUT"
