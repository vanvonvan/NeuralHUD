# Development tasks. To install or remove the HUD, use ./install.sh and
# ./uninstall.sh directly: they take options (see README.md).

.PHONY: test art screenshot close

# Headless checks: hook events and state ordering, and the status-line typing.
# No display and no Claude Code session needed; the real state dir is untouched.
test:
	python3 -m py_compile hud.py hook.py
	python3 -m unittest discover -s tests

# Regenerate the README art (icon, logo, banner, social preview). Needs pycairo.
art:
	python3 tools/gen_art.py

# Render the HUD mid-task into assets/screenshot.png, offscreen.
screenshot:
	python3 tools/screenshot.py 1920x1080 assets/screenshot.png

# Close a running HUD until the next prompt (what the keyboard shortcut runs).
close:
	python3 hud.py --close
