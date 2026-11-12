# Optional install settings, e.g.
#   make install MONITOR=ARZOPA SHORTCUT='<Super><Alt>n'
# `make monitors` lists the values MONITOR accepts.
# MONITOR: xrandr output (HDMI-1) or EDID text (a brand). Default: the smallest
#          non-primary monitor.
# SHORTCUT: GNOME only, a key combination that closes the HUD.
MONITOR ?=
SHORTCUT ?=

.PHONY: install uninstall monitors demo test art screenshot close

# Copy hud.py and hook.py to ~/.claude/neural-hud and register the hooks in
# ~/.claude/settings.json (a backup is written first). Safe to re-run: it
# replaces the previous install, so run it again after a `git pull`.
install:
	./install.sh $(if $(MONITOR),--monitor '$(MONITOR)') $(if $(SHORTCUT),--shortcut '$(SHORTCUT)')

# List connected monitors, their MONITOR= values, and which one the HUD uses
# by default. With MONITOR=..., also shows which monitor that value picks.
monitors:
	@NEURAL_HUD_MONITOR='$(MONITOR)' python3 hud.py --list-monitors

# Remove the hooks (settings.json is backed up first) and ~/.claude/neural-hud.
uninstall:
	./uninstall.sh

# Headless checks: hook events and state ordering, and the status-line typing.
# No display and no Claude Code session needed; the real state dir is untouched.
test:
	python3 -m py_compile hud.py hook.py
	python3 -m unittest discover -s tests

# Regenerate the README art (icon, logo, banner, social preview). Needs pycairo.
art:
	python3 tools/gen_art.py

# Play a scripted ~45 s session on the HUD, live on your monitor: a plan, timed
# steps, a permission hold and the outro. Runs the repo's hook.py and hud.py
# (not the installed copies) in a private state dir; Claude Code isn't needed.
demo:
	NEURAL_HUD_MONITOR='$(MONITOR)' python3 tools/demo.py

# Render the HUD mid-task, and on the standby screen, offscreen.
screenshot:
	python3 tools/screenshot.py 1920x1080 assets/screenshot.png
	python3 tools/screenshot.py 1920x1080 assets/screenshot-standby.png --waiting

# Close a running HUD until the next prompt (what the keyboard shortcut runs).
close:
	python3 hud.py --close
