#!/usr/bin/env python3
"""Play a scripted Claude Code session on the HUD, live, without Claude Code.

Feeds the repo's own hook.py the same JSON payloads Claude Code sends, so the
whole path runs for real: hook -> state files -> hud.py on your monitor. It
uses a private state directory, so a real session's HUD and state are never
touched. About 45 seconds: a plan, timed steps, a permission hold, and the
TASK COMPLETE outro.

Run: python3 tools/demo.py      (or: make demo MONITOR=ARZOPA)
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hook.py")
ENV = dict(os.environ, XDG_RUNTIME_DIR=tempfile.mkdtemp(prefix="neural-hud-demo-"))
SID = "demo"
CWD = ROOT
n = 0

PLAN = [("Read the hook payloads", "Reading the hook payloads"),
        ("Pair each step with its END", "Pairing each step with its END"),
        ("Run the test suite", "Running the test suite"),
        ("Publish the release", "Publishing the release")]


def hook(event, **payload):
    payload.update(hook_event_name=event, session_id=SID, cwd=CWD)
    subprocess.run([sys.executable, HOOK], input=json.dumps(payload), text=True, env=ENV, check=False)


def step(tool, inp, seconds, then=0.6):
    """One tool call: PreToolUse, run for `seconds`, PostToolUse."""
    global n
    n += 1
    tid = f"toolu_demo_{n}"
    hook("PreToolUse", tool_name=tool, tool_input=inp, tool_use_id=tid)
    time.sleep(seconds)
    hook("PostToolUse", tool_name=tool, tool_input=inp, tool_use_id=tid, tool_response="ok")
    time.sleep(then)


def plan(current):
    todos = [{"content": c, "activeForm": a,
              "status": "completed" if i < current else "in_progress" if i == current else "pending"}
             for i, (c, a) in enumerate(PLAN)]
    step("TodoWrite", {"todos": todos}, 0.1)


def main():
    print("demo: the HUD opens on", os.environ.get("NEURAL_HUD_MONITOR") or "your smallest non-primary monitor")
    hook("UserPromptSubmit", prompt="demo")  # also starts hud.py, as a real prompt would
    time.sleep(2.0)
    plan(0)
    step("Read", {"file_path": f"{CWD}/hook.py"}, 0.3)
    step("Grep", {"pattern": "tool_use_id"}, 0.4)
    step("Agent", {"description": "Map the hook payloads"}, 5.0)
    plan(1)
    step("Edit", {"file_path": f"{CWD}/hook.py"}, 0.3)
    step("Edit", {"file_path": f"{CWD}/hud.py"}, 0.3)
    step("WebFetch", {"url": "https://doc.qt.io/qt-6/qpainter.html"}, 1.8)
    plan(2)
    step("Bash", {"description": "Run the full test suite", "command": "make test"}, 8.0)

    # a permission prompt: the HUD holds on the amber standby screen
    plan(3)
    inp = {"description": "Push the release tag to GitHub", "command": "git push --tags"}
    hook("PreToolUse", tool_name="Bash", tool_input=inp, tool_use_id="toolu_demo_push")
    time.sleep(0.3)
    hook("PermissionRequest", tool_name="Bash", tool_input=inp)
    hook("Notification", message="Claude needs your permission to use Bash")
    time.sleep(8.0)
    hook("PostToolUse", tool_name="Bash", tool_input=inp, tool_use_id="toolu_demo_push", tool_response="ok")
    time.sleep(1.0)

    step("Read", {"file_path": f"{CWD}/README.md"}, 0.3)
    step("mcp__ide__getDiagnostics", {}, 2.2)
    todos = [{"content": c, "activeForm": a, "status": "completed"} for c, a in PLAN]
    step("TodoWrite", {"todos": todos}, 0.1, then=2.5)
    hook("Stop")
    time.sleep(3.0)
    print("demo: done")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:  # end the session: the HUD plays its outro and exits
        hook("Stop")
