#!/usr/bin/env bash
# Made by Claude
# Installs Neural HUD for Claude Code: copies the scripts to ~/.claude/neural-hud
# and adds the hooks to ~/.claude/settings.json (a backup is written first).
#
#   ./install.sh [--monitor NAME] [--theme NAME] [--context SIZE] [--standby banner|full]
#                [--shortcut '<Super><Alt>n']
#
#   --monitor   xrandr output (HDMI-1) or EDID text (a brand name) of the screen to use.
#               Default: the smallest non-primary monitor.
#   --theme     colour theme: cyan (default), amber, phosphor, red or violet.
#   --context   context window the gauge measures against, e.g. 200k or 1m.
#               Default: 200k, switching to 1m once a session goes past 200k.
#   --standby   while Claude waits on you: banner (default), a strip along the top of the
#               monitor, or full, the whole monitor.
#   --shortcut  GNOME only: bind a key combination that closes the HUD.
set -euo pipefail

MONITOR=""
THEME=""
CONTEXT=""
STANDBY=""
SHORTCUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --monitor) MONITOR="$2"; shift 2 ;;
    --theme) THEME="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --standby) STANDBY="$2"; shift 2 ;;
    --shortcut) SHORTCUT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

case "${THEME,,}" in
  ""|cyan|amber|phosphor|red|violet) ;;
  *) echo "unknown theme: $THEME (cyan, amber, phosphor, red, violet)" >&2; exit 1 ;;
esac
case "${STANDBY,,}" in
  ""|banner|full) ;;
  *) echo "unknown standby: $STANDBY (banner, full)" >&2; exit 1 ;;
esac
if [[ -n "$CONTEXT" ]]; then
  CONTEXT=$(python3 -c "
import re, sys
m = re.fullmatch(r'(\d+(?:\.\d+)?)([km]?)', sys.argv[1].strip().lower())
print(int(float(m[1]) * {'': 1, 'k': 1e3, 'm': 1e6}[m[2]]) if m else '')" "$CONTEXT")
  [[ -n "$CONTEXT" ]] || { echo "--context takes a size such as 200k or 1m" >&2; exit 1; }
fi

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

MONITOR="$MONITOR" THEME="${THEME,,}" STANDBY="${STANDBY,,}" CONTEXT="$CONTEXT" SETTINGS="$SETTINGS" python3 - <<'EOF'
import json, os, shlex
path = os.environ["SETTINGS"]
s = json.load(open(path))
cmd = "python3 ~/.claude/neural-hud/hook.py"
for var, key in (("CONTEXT", "NEURAL_HUD_CONTEXT_WINDOW"), ("THEME", "NEURAL_HUD_THEME"), ("MONITOR", "NEURAL_HUD_MONITOR"),
                 ("STANDBY", "NEURAL_HUD_STANDBY")):
    if os.environ[var]:
        cmd = f"{key}={shlex.quote(os.environ[var])} {cmd}"
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
