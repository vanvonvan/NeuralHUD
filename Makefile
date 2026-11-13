# Optional install settings, e.g.
#   make install MONITOR=ARZOPA THEME=amber SHORTCUT='<Super><Alt>n'
# `make monitors` lists the values MONITOR accepts.
# MONITOR: xrandr output (HDMI-1) or EDID text (a brand). Default: the smallest
#          non-primary monitor.
# THEME:   cyan (default), amber, phosphor, red or violet. Try one first with
#          `make demo THEME=amber`.
# CONTEXT: the context window the gauge measures against, e.g. 200k or 1m.
#          Default: 200k, switching to 1m once a session goes past 200k.
# STANDBY: while Claude waits on you, banner (default: a strip along the top of
#          the monitor, so what's under it stays visible) or full (the whole monitor).
# SHORTCUT: GNOME only, a key combination that closes the HUD.
MONITOR ?=
THEME ?=
CONTEXT ?=
STANDBY ?=
SHORTCUT ?=

.PHONY: install uninstall monitors demo test art screenshot close

# Copy hud.py and hook.py to ~/.claude/neural-hud and register the hooks in
# ~/.claude/settings.json (a backup is written first). Safe to re-run: it
# replaces the previous install, so run it again after a `git pull`.
install:
	./install.sh $(if $(MONITOR),--monitor '$(MONITOR)') $(if $(THEME),--theme '$(THEME)') \
		$(if $(CONTEXT),--context '$(CONTEXT)') $(if $(STANDBY),--standby '$(STANDBY)') $(if $(SHORTCUT),--shortcut '$(SHORTCUT)')

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
	NEURAL_HUD_MONITOR='$(MONITOR)' NEURAL_HUD_THEME='$(THEME)' NEURAL_HUD_STANDBY='$(STANDBY)' python3 tools/demo.py

# Render the README screenshots offscreen: mid-task, standby, the end-of-task
# summary, and a half-size preview of each extra theme.
screenshot:
	python3 tools/screenshot.py 1920x1080 assets/screenshot.png
	python3 tools/screenshot.py 1920x1080 assets/screenshot-standby.png --waiting
	python3 tools/screenshot.py 1920x1080 assets/screenshot-summary.png --summary
	for t in amber phosphor red violet; do \
		NEURAL_HUD_THEME=$$t python3 tools/screenshot.py 1920x1080 assets/theme-$$t.png --half; done

# Close a running HUD until the next prompt (what the keyboard shortcut runs).
close:
	python3 hud.py --close
