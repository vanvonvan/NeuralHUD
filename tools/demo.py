#!/usr/bin/env python3
"""Play a scripted Claude Code session on the HUD, live, without Claude Code.

Feeds the repo's own hook.py the same JSON payloads Claude Code sends, so the
whole path runs for real: hook -> state files -> hud.py on your monitor. It
uses a private state directory, so a real session's HUD and state are never
touched. About a minute: a plan, timed steps and edits, a subagent, a second
session in another project, a failing step, a permission hold, and the
end-of-task summary.

Run: python3 tools/demo.py      (or: make demo MONITOR=ARZOPA THEME=amber)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hook.py")
TMP = tempfile.mkdtemp(prefix="neural-hud-demo-")
ENV = dict(os.environ, XDG_RUNTIME_DIR=TMP)

# a second "project" on its own branch, for the second session's track
OTHER = os.path.join(TMP, "orbital-api")
os.makedirs(os.path.join(OTHER, ".git"))
with open(os.path.join(OTHER, ".git", "HEAD"), "w") as f:
    f.write("ref: refs/heads/feature/rate-limits\n")

PLAN = [("Read the hook payloads", "Reading the hook payloads"),
        ("Pair each step with its END", "Pairing each step with its END"),
        ("Run the test suite", "Running the test suite"),
        ("Publish the release", "Publishing the release")]
OLD = "def tick(self):\n    now = time.time()\n    self.poll()\n"
NEW = "def tick(self):\n    now = time.time()\n    dt = now - self.prev\n    self.prev = now\n    self.poll(dt)\n"


class Session:
    def __init__(self, sid, cwd):
        self.sid, self.cwd, self.n = sid, cwd, 0
        self.transcript = os.path.join(TMP, f"{sid}.jsonl")
        self.tokens = 0

    def hook(self, event, **payload):
        payload.update(hook_event_name=event, session_id=self.sid, cwd=self.cwd, transcript_path=self.transcript)
        subprocess.run([sys.executable, HOOK], input=json.dumps(payload), text=True, env=ENV, check=False)

    def think(self, tokens):
        """Grow the context the gauge reads from the transcript."""
        self.tokens += tokens
        with open(self.transcript, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"model": "claude-demo", "usage": {
                "input_tokens": 3, "cache_read_input_tokens": self.tokens, "output_tokens": 800}}}) + "\n")

    def step(self, tool, inp, seconds, then=0.6, fail=False, resp="ok"):
        """One tool call: PreToolUse, run for `seconds`, then PostToolUse (or a failure)."""
        self.n += 1
        tid = f"toolu_{self.sid}_{self.n}"
        self.hook("PreToolUse", tool_name=tool, tool_input=inp, tool_use_id=tid)
        time.sleep(seconds)
        if fail:
            self.hook("PostToolUseFailure", tool_name=tool, tool_input=inp, tool_use_id=tid,
                      error="Exit code 1: 2 tests failed")
        else:
            self.hook("PostToolUse", tool_name=tool, tool_input=inp, tool_use_id=tid, tool_response=resp)
        self.think(2500)
        time.sleep(then)

    def plan(self, current):
        todos = [{"content": c, "activeForm": a,
                  "status": "completed" if i < current else "in_progress" if i == current else "pending"}
                 for i, (c, a) in enumerate(PLAN)]
        self.step("TodoWrite", {"todos": todos}, 0.1)


def side_session():
    """A second Claude Code session in another project, a few steps of its own."""
    s = Session("demo-side", OTHER)
    s.think(41000)
    s.hook("UserPromptSubmit", prompt="demo")
    time.sleep(1.0)
    s.step("Read", {"file_path": f"{OTHER}/limits.py"}, 0.4, then=2.0)
    s.step("Grep", {"pattern": "RateLimiter"}, 0.6, then=2.5)
    s.step("Edit", {"file_path": f"{OTHER}/limits.py", "old_string": "a\nb", "new_string": "a\nB\nc\nd"}, 0.3, then=2.0)
    s.step("Bash", {"description": "Run the rate limit tests", "command": "pytest"}, 4.0, then=1.0)
    s.hook("Stop")


def main():
    print("demo: the HUD opens on", os.environ.get("NEURAL_HUD_MONITOR") or "your smallest non-primary monitor")
    s = Session("demo", ROOT)
    s.think(64000)
    s.hook("UserPromptSubmit", prompt="demo")  # also starts hud.py, as a real prompt would
    time.sleep(2.0)
    s.plan(0)
    s.step("Read", {"file_path": f"{ROOT}/hook.py"}, 0.3)
    s.step("Grep", {"pattern": "tool_use_id"}, 0.4)
    s.step("Agent", {"description": "Map the hook payloads"}, 7.0)
    s.plan(1)
    side = threading.Thread(target=side_session)
    side.start()
    s.step("Edit", {"file_path": f"{ROOT}/hook.py", "old_string": OLD, "new_string": NEW}, 0.3)
    s.step("Write", {"file_path": f"{ROOT}/tests/test_timing.py", "content": "import unittest\n" * 24}, 0.3)
    s.step("WebFetch", {"url": "https://doc.qt.io/qt-6/qpainter.html"}, 1.8)
    s.plan(2)
    s.step("Bash", {"description": "Run the full test suite", "command": "make test"}, 5.0, fail=True, then=1.5)
    s.step("Edit", {"file_path": f"{ROOT}/hud.py", "old_string": "x = 1\ny = 2", "new_string": "x = 1\ny = 3"}, 0.3)
    s.step("Bash", {"description": "Run the full test suite", "command": "make test"}, 6.0)

    # a permission prompt: the HUD shrinks to the amber standby banner, then fills the monitor again
    s.plan(3)
    side.join()
    inp = {"description": "Push the release tag to GitHub", "command": "git push --tags"}
    s.hook("PreToolUse", tool_name="Bash", tool_input=inp, tool_use_id="toolu_demo_push")
    time.sleep(0.3)
    s.hook("PermissionRequest", tool_name="Bash", tool_input=inp)
    s.hook("Notification", message="Claude needs your permission to use Bash")
    time.sleep(7.0)
    s.hook("PostToolUse", tool_name="Bash", tool_input=inp, tool_use_id="toolu_demo_push", tool_response="ok")
    time.sleep(1.0)

    s.step("Read", {"file_path": f"{ROOT}/README.md"}, 0.3)
    s.step("mcp__ide__getDiagnostics", {}, 2.2)
    todos = [{"content": c, "activeForm": a, "status": "completed"} for c, a in PLAN]
    s.step("TodoWrite", {"todos": todos}, 0.1, then=2.5)
    s.hook("Stop")
    time.sleep(6.0)  # the summary, then the power-off
    print("demo: done")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:  # end the sessions: the HUD plays its outro and exits
        Session("demo", ROOT).hook("Stop")
        Session("demo-side", OTHER).hook("Stop")
