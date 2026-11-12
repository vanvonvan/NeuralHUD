#!/usr/bin/env bash
# Made by Claude
# Removes the Neural HUD hooks from ~/.claude/settings.json and deletes ~/.claude/neural-hud.
# A GNOME shortcut added by install.sh stays: remove it in Settings > Keyboard > Custom Shortcuts.
set -euo pipefail

SETTINGS="$HOME/.claude/settings.json"
pkill -f "neural-hud/hud.py" 2>/dev/null || true
if [[ -f "$SETTINGS" ]]; then
  cp "$SETTINGS" "$SETTINGS.bak-neural-hud"
  SETTINGS="$SETTINGS" python3 - <<'EOF'
import json, os
path = os.environ["SETTINGS"]
s = json.load(open(path))
hooks = s.get("hooks", {})
for ev in list(hooks):
    hooks[ev] = [e for e in hooks[ev]
                 if not any("neural-hud/hook.py" in h.get("command", "") for h in e.get("hooks", []))]
    if not hooks[ev]:
        del hooks[ev]
if not hooks:
    s.pop("hooks", None)
json.dump(s, open(path, "w"), indent=2)
EOF
  echo "removed the hooks from $SETTINGS (backup: $SETTINGS.bak-neural-hud)"
fi
rm -rf "$HOME/.claude/neural-hud"
echo "done."
