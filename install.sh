#!/usr/bin/env bash
# Point Claude Code's statusLine at statusline.py, preserving other settings.
set -euo pipefail

RAW="https://raw.githubusercontent.com/T0mSIlver/claude-fable-usage/main"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"

mkdir -p "$CLAUDE_DIR"

# Run from a clone, statusline.py sits next to us. Piped in from curl, it doesn't.
HERE="$(dirname "${BASH_SOURCE[0]:-$0}")"
if [ -f "$HERE/statusline.py" ]; then
    SCRIPT="$(cd "$HERE" && pwd)/statusline.py"
else
    SCRIPT="$CLAUDE_DIR/statusline.py"
    curl -fsSL "$RAW/statusline.py" -o "$SCRIPT"
fi

chmod +x "$SCRIPT"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak"

python3 - "$SETTINGS" "$SCRIPT" <<'PY'
import json, sys

settings_path, script_path = sys.argv[1], sys.argv[2]
with open(settings_path) as f:
    settings = json.load(f)

settings["statusLine"] = {
    "type": "command",
    "command": script_path,
    "refreshInterval": 10,
}

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
PY

echo "Installed. statusLine -> $SCRIPT"
echo "Previous settings saved to $SETTINGS.bak"
