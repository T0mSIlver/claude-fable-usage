#!/usr/bin/env bash
# Point Claude Code's statusLine at statusline.py, preserving other settings.
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/statusline.py"
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"

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
