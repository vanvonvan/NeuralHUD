#!/usr/bin/env bash
# Made by Claude
# Installs Neural HUD for Claude Code: copies the scripts to ~/.claude/neural-hud
# and adds the hooks to ~/.claude/settings.json (a backup is written first).
#
#   ./install.sh [--monitor NAME] [--shortcut '<Super><Alt>n']
#
#   --monitor   xrandr output (HDMI-1) or EDID text (a brand name) of the screen to use.
#               Default: the smallest non-primary monitor.
#   --shortcut  GNOME only: bind a key combination that closes the HUD.
set -euo pipefail

MONITOR=""
SHORTCUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --monitor) MONITOR="$2"; shift 2 ;;
    --shortcut) SHORTCUT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

python3 -c "import PyQt6.QtOpenGLWidgets" 2>/dev/null || {
  echo "PyQt6 is missing: install it with your package manager (e.g. python3-pyqt6) or pip." >&2; exit 1; }
command -v xrandr >/dev/null || { echo "xrandr is missing." >&2; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.claude/neural-hud"
mkdir -p "$DEST"
cp "$SRC/hud.py" "$SRC/hook.py" "$DEST/"
echo "copied scripts to $DEST"

SETTINGS="$HOME/.claude/settings.json"
[[ -f "$SETTINGS" ]] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-neural-hud"

MONITOR="$MONITOR" SETTINGS="$SETTINGS" python3 - <<'EOF'
import json, os, shlex
path = os.environ["SETTINGS"]
s = json.load(open(path))
cmd = "python3 ~/.claude/neural-hud/hook.py"
if os.environ["MONITOR"]:
    cmd = f"NEURAL_HUD_MONITOR={shlex.quote(os.environ['MONITOR'])} {cmd}"
hooks = s.setdefault("hooks", {})
for ev in list(hooks):  # drop a previous install, keep everything else
    hooks[ev] = [e for e in hooks[ev]
                 if not any("neural-hud/hook.py" in h.get("command", "") for h in e.get("hooks", []))]
    if not hooks[ev]:
        del hooks[ev]
h = {"type": "command", "command": cmd, "async": True, "timeout": 5}
for ev, matcher in [("UserPromptSubmit", None), ("PreToolUse", "*"), ("PostToolUse", "*"),
                    ("PostToolUseFailure", "*"), ("PermissionRequest", "*"),
                    ("Notification", "permission_prompt"), ("Stop", None),
                    ("StopFailure", None), ("SessionEnd", None)]:
    e = {"hooks": [h]}
    if matcher:
        e["matcher"] = matcher
    hooks.setdefault(ev, []).append(e)
json.dump(s, open(path, "w"), indent=2)
EOF
echo "added hooks to $SETTINGS (backup: $SETTINGS.bak-neural-hud)"

if [[ -n "$SHORTCUT" ]]; then
  KEY=org.gnome.settings-daemon.plugins.media-keys
  BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
  LIST=$(gsettings get $KEY custom-keybindings)
  i=0; while [[ "$LIST" == *"$BASE/custom$i/"* ]]; do i=$((i+1)); done
  NEW="$BASE/custom$i/"
  LIST=$(LIST="$LIST" NEW="$NEW" python3 -c "
import ast, os
l = ast.literal_eval(os.environ['LIST'].replace('@as ', ''))
print(str(l + [os.environ['NEW']]))")
  gsettings set $KEY custom-keybindings "$LIST"
  gsettings set $KEY.custom-keybinding:$NEW name 'Close Neural HUD'
  gsettings set $KEY.custom-keybinding:$NEW command "python3 $DEST/hud.py --close"
  gsettings set $KEY.custom-keybinding:$NEW binding "$SHORTCUT"
  echo "bound $SHORTCUT to close the HUD"
fi

echo "done: the HUD opens with your next Claude Code prompt."
