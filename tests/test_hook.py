#!/usr/bin/env python3
"""Headless checks for hook.py and the HUD's typing logic. No display, no
Claude Code session, and nothing touches the real state directory.

Run: python3 -m unittest discover -s tests   (or: make test)
"""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="neural-hud-test-")
hook = importlib.import_module("hook")  # reads XDG_RUNTIME_DIR at import


def run_hook(payload):
    """Run hook.main() on one event, with a fresh TS and the daemon launch stubbed out."""
    hook.TS = time.time()
    launched = []
    real_popen, real_stdin = hook.subprocess.Popen, sys.stdin
    hook.subprocess.Popen = lambda *a, **k: launched.append(a)
    sys.stdin = __import__("io").StringIO(json.dumps(payload))
    try:
        hook.main()
    finally:
        hook.subprocess.Popen, sys.stdin = real_popen, real_stdin
    return launched


def state(sid="t"):
    with open(os.path.join(hook.STATE_DIR, "s-" + sid)) as f:
        return f.read().split()[0]


def events():
    with open(hook.EVENTS) as f:
        return [json.loads(line) for line in f]


class Describe(unittest.TestCase):
    def test_kinds(self):
        cwd = "/home/u/proj"
        self.assertEqual(hook.describe("Read", {"file_path": "/home/u/proj/a.py"}, cwd), ("READ", "a.py"))
        self.assertEqual(hook.describe("Edit", {"file_path": "/etc/x"}, cwd), ("WRITE", "/etc/x"))
        self.assertEqual(hook.describe("Bash", {"command": "rm -rf /", "description": "Clean"}, cwd),
                         ("EXEC", "Clean"))
        self.assertEqual(hook.describe("Grep", {"pattern": "foo"}, cwd), ("SEARCH", "foo"))
        self.assertEqual(hook.describe("WebFetch", {"url": "https://example.com/a/b"}, cwd), ("NET", "example.com"))
        self.assertEqual(hook.describe("mcp__ide__getDiagnostics", {}, cwd), ("MCP", "ide › getDiagnostics"))
        self.assertEqual(hook.describe("Whatever", {}, cwd), ("TOOL", "Whatever"))

    def test_bash_never_shows_the_raw_command(self):
        kind, detail = hook.describe("Bash", {"command": "curl secret-token"}, "")
        self.assertEqual((kind, detail), ("EXEC", "shell command"))

    def test_home_paths_are_shortened(self):
        home = os.path.expanduser("~")
        self.assertEqual(hook.rel(home + "/x/y", "/elsewhere"), "~/x/y")


class Lifecycle(unittest.TestCase):
    def setUp(self):
        for f in os.listdir(hook.STATE_DIR) if os.path.isdir(hook.STATE_DIR) else []:
            os.remove(os.path.join(hook.STATE_DIR, f))

    def test_prompt_works_and_starts_the_daemon(self):
        launched = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "t"})
        self.assertEqual(state(), "working")
        self.assertEqual(len(launched), 1)
        self.assertEqual(events()[-1]["kind"], "LINK")

    def test_tool_use_is_logged_without_changing_state(self):
        run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "t"})
        run_hook({"hook_event_name": "PreToolUse", "session_id": "t", "tool_name": "Read",
                  "tool_input": {"file_path": "/p/a.py"}, "cwd": "/p"})
        self.assertEqual(state(), "working")
        self.assertEqual(events()[-1]["kind"], "READ")
        self.assertEqual(events()[-1]["detail"], "a.py")

    def test_permission_then_stop(self):
        run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "t"})
        run_hook({"hook_event_name": "PermissionRequest", "session_id": "t"})
        self.assertEqual(state(), "waiting")
        run_hook({"hook_event_name": "Stop", "session_id": "t"})
        self.assertEqual(state(), "done")

    def test_an_older_hook_never_overwrites_a_newer_state(self):
        run_hook({"hook_event_name": "Stop", "session_id": "t"})
        hook.TS = time.time() - 60  # a working event that started earlier but finished later
        hook.set_state(os.path.join(hook.STATE_DIR, "s-t"), "working")
        self.assertEqual(state(), "done")

    def test_dismissed_stays_closed_until_the_next_prompt(self):
        run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "t"})
        open(os.path.join(hook.STATE_DIR, "dismissed"), "w").close()
        self.assertEqual(run_hook({"hook_event_name": "PostToolUse", "session_id": "t"}), [])
        time.sleep(0.01)
        self.assertEqual(len(run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "t"})), 1)

    def test_long_details_are_truncated(self):
        run_hook({"hook_event_name": "PreToolUse", "session_id": "t", "tool_name": "Grep",
                  "tool_input": {"pattern": "x" * 500}})
        self.assertEqual(len(events()[-1]["detail"]), 160)


class Typing(unittest.TestCase):
    """HUD.typed() drives the status line's typewriter effect."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.typed = staticmethod(importlib.import_module("hud").HUD.typed)
        except ImportError as e:
            raise unittest.SkipTest(f"PyQt6 not available: {e}")

    def test_types_in_then_stops(self):
        hud, ev, text = type("S", (), {})(), {"ts": 1, "kind": "EXEC"}, "Run the hook tests"
        self.assertEqual(self.typed(hud, ev, text, 100.0), ("", True))
        mid, typing = self.typed(hud, ev, text, 100.3)
        self.assertTrue(typing and 0 < len(mid) < len(text) and text.startswith(mid))
        self.assertEqual(self.typed(hud, ev, text, 102.0), (text, False))

    def test_long_lines_finish_within_the_cap(self):
        hud = type("S", (), {})()
        text = "a very long status line / with/many.separators " * 3
        self.typed(hud, {"ts": 2}, text, 0.0)
        self.assertEqual(self.typed(hud, {"ts": 2}, text, 1.61), (text, False))

    def test_a_new_event_restarts_typing(self):
        hud = type("S", (), {})()
        self.typed(hud, {"ts": 1}, "first", 0.0)
        self.assertEqual(self.typed(hud, {"ts": 1}, "first", 5.0), ("first", False))
        self.assertEqual(self.typed(hud, {"ts": 2}, "second", 5.0), ("", True))


if __name__ == "__main__":
    unittest.main()
