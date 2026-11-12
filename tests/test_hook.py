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
import types
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


class Timing(unittest.TestCase):
    def setUp(self):
        Lifecycle.setUp(self)

    def test_each_step_logs_an_end_with_the_same_id(self):
        run_hook({"hook_event_name": "PreToolUse", "session_id": "t", "tool_name": "Bash",
                  "tool_use_id": "toolu_1", "tool_input": {"description": "Run tests"}})
        run_hook({"hook_event_name": "PostToolUse", "session_id": "t", "tool_name": "Bash",
                  "tool_use_id": "toolu_1"})
        start, end = events()[-2:]
        self.assertEqual((start["kind"], start["id"], start["tool"]), ("EXEC", "toolu_1", "Bash"))
        self.assertEqual((end["kind"], end["id"]), ("END", "toolu_1"))

    def test_a_failed_step_also_ends(self):
        run_hook({"hook_event_name": "PostToolUseFailure", "session_id": "t", "tool_name": "Bash",
                  "tool_use_id": "toolu_2"})
        self.assertEqual([e["kind"] for e in events()[-2:]], ["END", "FAULT"])


class Waiting(unittest.TestCase):
    def setUp(self):
        Lifecycle.setUp(self)

    def reason(self):
        with open(os.path.join(hook.STATE_DIR, "w-t")) as f:
            return json.load(f)

    def test_permission_says_what_it_is_for(self):
        run_hook({"hook_event_name": "PermissionRequest", "session_id": "t", "tool_name": "Bash",
                  "tool_input": {"description": "Force-push", "command": "git push -f"}})
        self.assertEqual(state(), "waiting")
        self.assertEqual((self.reason()["kind"], self.reason()["detail"]), ("PERMISSION", "EXEC · Force-push"))

    def test_the_generic_notification_does_not_replace_it(self):
        run_hook({"hook_event_name": "PermissionRequest", "session_id": "t", "tool_name": "Bash",
                  "tool_input": {"description": "Force-push"}})
        run_hook({"hook_event_name": "Notification", "session_id": "t",
                  "message": "Claude needs your permission to use Bash"})
        self.assertEqual(self.reason()["detail"], "EXEC · Force-push")

    def test_the_notification_is_used_when_nothing_better_exists(self):
        run_hook({"hook_event_name": "Notification", "session_id": "t", "message": "Claude needs you"})
        self.assertEqual(self.reason()["detail"], "Claude needs you")

    def test_a_question_shows_its_text(self):
        run_hook({"hook_event_name": "PreToolUse", "session_id": "t", "tool_name": "AskUserQuestion",
                  "tool_input": {"questions": [{"question": "Which repo name?"}]}})
        self.assertEqual(state(), "waiting")
        self.assertEqual((self.reason()["kind"], self.reason()["detail"]), ("QUESTION", "Which repo name?"))


class Plan(unittest.TestCase):
    def setUp(self):
        Lifecycle.setUp(self)

    def plan(self):
        with open(os.path.join(hook.STATE_DIR, "plan-t.json")) as f:
            return [(i["id"], i["text"], i["status"]) for i in json.load(f)["items"]]

    def post(self, tool, inp, resp=None):
        run_hook({"hook_event_name": "PostToolUse", "session_id": "t", "tool_name": tool,
                  "tool_input": inp, "tool_response": resp})

    def test_todowrite_replaces_the_whole_list(self):
        self.post("TodoWrite", {"todos": [{"content": "A", "activeForm": "Doing A", "status": "completed"},
                                          {"content": "B", "activeForm": "Doing B", "status": "in_progress"}]})
        self.assertEqual(self.plan(), [("1", "A", "completed"), ("2", "B", "in_progress")])
        self.post("TodoWrite", {"todos": [{"content": "C", "status": "pending"}]})
        self.assertEqual(self.plan(), [("1", "C", "pending")])

    def test_todowrite_shows_the_current_step_in_the_stream(self):
        kind, detail = hook.describe("TodoWrite", {"todos": [
            {"content": "A", "status": "completed"}, {"content": "B", "activeForm": "Doing B", "status": "in_progress"}]}, "")
        self.assertEqual((kind, detail), ("PLAN", "Doing B"))

    def test_tasks_are_created_and_updated(self):
        self.post("TaskCreate", {"subject": "Write it", "activeForm": "Writing it"}, "Task #1 created successfully")
        self.post("TaskCreate", {"subject": "Test it"}, {"task": {"id": "2"}})
        self.post("TaskCreate", {"subject": "Ship it"})  # no id in the response: next number
        self.post("TaskUpdate", {"taskId": "1", "status": "completed"})
        self.post("TaskUpdate", {"taskId": "2", "status": "in_progress", "subject": "Test it well"})
        self.post("TaskUpdate", {"taskId": "3", "status": "deleted"})
        self.assertEqual(self.plan(), [("1", "Write it", "completed"), ("2", "Test it well", "in_progress")])


class HudTiming(unittest.TestCase):
    """HUD.finish() and HUD.elapsed(): pairing ENDs with their steps and timing them."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.HUD = importlib.import_module("hud").HUD
        except ImportError as e:
            raise unittest.SkipTest(f"PyQt6 not available: {e}")

    def hud(self):
        h = type("S", (), {})()
        h.running, h.ended, h.waiting_since, h.events, h.plan, h.turn_start = [], {}, None, [], None, 0
        for m in ("close_step", "elapsed", "code_delta"):
            setattr(h, m, types.MethodType(getattr(self.HUD, m), h))
        return h

    def test_pairs_by_id_and_times_it(self):
        h, ev = self.hud(), {"ts": 100.0, "id": "a", "tool": "Bash"}
        h.running.append(ev)
        self.assertAlmostEqual(self.HUD.elapsed(h, ev, 103.0), 3.0)
        self.HUD.finish(h, {"ts": 104.5, "kind": "END", "id": "a", "tool": "Bash"})
        self.assertEqual((h.running, ev["dur"]), ([], 4.5))

    def test_an_end_arriving_first_is_kept_for_its_step(self):
        h = self.hud()
        end = {"ts": 104.0, "kind": "END", "id": "b", "tool": "Read", "add": 3}
        self.HUD.finish(h, end)
        self.assertEqual(h.ended, {"b": end})  # whole, so its line counts survive the wait

    def test_without_ids_it_pairs_the_oldest_call_of_the_same_tool(self):
        h = self.hud()
        a, b = {"ts": 1.0, "tool": "Bash"}, {"ts": 2.0, "tool": "Bash"}
        h.running += [a, b]
        self.HUD.finish(h, {"ts": 5.0, "kind": "END", "tool": "Bash"})
        self.assertEqual((a.get("dur"), h.running), (4.0, [b]))

    def test_time_on_hold_is_not_counted(self):
        h, ev = self.hud(), {"ts": 100.0, "id": "c", "tool": "Bash"}
        h.running.append(ev)
        h.waiting_since = 102.0
        self.assertAlmostEqual(self.HUD.elapsed(h, ev, 110.0), 2.0)  # frozen while held
        self.HUD.end_wait(type("W", (), {"running": h.running, "waiting_since": 102.0,
                                          "reason": None, "accent": None})(), 110.0)
        self.HUD.finish(h, {"ts": 111.0, "kind": "END", "id": "c"})
        self.assertAlmostEqual(ev["dur"], 3.0)


    def test_line_counts_reach_the_step_and_the_summary(self):
        h = self.hud()
        a = {"ts": 1.0, "id": "e1", "tool": "Edit", "kind": "WRITE", "detail": "a.py"}
        b = {"ts": 2.0, "id": "e2", "tool": "Bash", "kind": "EXEC", "detail": "Run tests"}
        h.running += [a, b]
        h.events += [a, b, {"ts": 3.0, "kind": "FAULT", "detail": "EXEC failed"}]
        self.HUD.finish(h, {"ts": 1.5, "kind": "END", "id": "e1", "add": 7, "rm": 2, "file": "a.py"})
        self.HUD.finish(h, {"ts": 12.0, "kind": "END", "id": "e2"})
        self.assertEqual(self.HUD.code_delta(h), (7, 2, {"a.py"}))
        sm = self.HUD.make_summary(h, 20.0)
        self.assertEqual((sm["steps"], sm["add"], sm["rm"], sm["files"], sm["faults"]), (2, 7, 2, 1, 1))
        self.assertEqual(sm["longest"][:2], ("EXEC", "Run tests"))

    def test_no_summary_for_a_turn_without_steps(self):
        self.assertIsNone(self.HUD.make_summary(self.hud(), 5.0))


class Delta(unittest.TestCase):
    """Lines added and removed by each edit, carried on its END event."""

    def setUp(self):
        Lifecycle.setUp(self)

    def end_of(self, tool, inp, resp=None):
        run_hook({"hook_event_name": "PostToolUse", "session_id": "t", "tool_name": tool, "cwd": "/p",
                  "tool_use_id": "x", "tool_input": inp, "tool_response": resp})
        e = [e for e in events() if e["kind"] == "END"][-1]
        return e.get("add", 0), e.get("rm", 0), e.get("file", "")

    def test_edit(self):
        self.assertEqual(self.end_of("Edit", {"file_path": "/p/a.py", "old_string": "a\nb\nc",
                                              "new_string": "a\nB\nc\nd"}), (2, 1, "a.py"))

    def test_the_reported_patch_wins(self):
        patch = {"structuredPatch": [{"lines": [" ctx", "-old", "+new", "+more"]}]}
        self.assertEqual(self.end_of("Edit", {"file_path": "/p/a.py", "old_string": "x", "new_string": "y"}, patch),
                         (2, 1, "a.py"))

    def test_write_and_multiedit(self):
        self.assertEqual(self.end_of("Write", {"file_path": "/p/n.py", "content": "1\n2\n3\n"}), (3, 0, "n.py"))
        self.assertEqual(self.end_of("MultiEdit", {"file_path": "/p/m.py", "edits": [
            {"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "c\nd"}]}), (2, 1, "m.py"))

    def test_other_tools_change_nothing(self):
        self.assertEqual(self.end_of("Bash", {"command": "ls"}), (0, 0, ""))


class Sessions(unittest.TestCase):
    def setUp(self):
        Lifecycle.setUp(self)

    def test_events_carry_their_session(self):
        run_hook({"hook_event_name": "PreToolUse", "session_id": "abc", "tool_name": "Read",
                  "tool_input": {"file_path": "/p/x"}})
        self.assertEqual(events()[-1]["sid"], "abc")

    def test_a_prompt_records_the_project_transcript_and_turn(self):
        run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "abc", "cwd": "/p/proj",
                  "transcript_path": "/p/t.jsonl"})
        with open(os.path.join(hook.STATE_DIR, "m-abc.json")) as f:
            self.assertEqual(json.load(f), {"cwd": "/p/proj", "transcript": "/p/t.jsonl"})
        self.assertTrue(os.path.exists(os.path.join(hook.STATE_DIR, "turn-abc")))


class HudReaders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.hud = importlib.import_module("hud")
        except ImportError as e:
            raise unittest.SkipTest(f"PyQt6 not available: {e}")

    def test_git_branch(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, ".git"))
        with open(os.path.join(d, ".git", "HEAD"), "w") as f:
            f.write("ref: refs/heads/feature/plan\n")
        os.makedirs(os.path.join(d, "src", "deep"))
        self.assertEqual(self.hud.git_branch(os.path.join(d, "src", "deep")), "plan")
        with open(os.path.join(d, ".git", "HEAD"), "w") as f:
            f.write("0123456789abcdef\n")
        self.assertEqual(self.hud.git_branch(d), "0123456")
        self.assertEqual(self.hud.git_branch(tempfile.mkdtemp()), "")

    def test_context_reads_the_newest_main_thread_usage(self):
        path = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        rows = [{"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 10, "output_tokens": 5}}},
                {"type": "assistant", "message": {"model": "m", "usage": {
                    "input_tokens": 4, "cache_read_input_tokens": 90000, "cache_creation_input_tokens": 6,
                    "output_tokens": 990}}},
                {"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": 999999}}},
                {"type": "user", "message": {"content": "hi"}}]
        with open(path, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows) + '{"truncated')
        self.assertEqual(self.hud.read_context(path), (91000, "m"))
        self.assertIsNone(self.hud.read_context("/nonexistent"))

    def test_context_window(self):
        self.assertEqual(self.hud.context_window(91000, "claude-x"), 200_000)
        self.assertEqual(self.hud.context_window(250_000, "claude-x"), 1_000_000)
        self.assertEqual(self.hud.context_window(1000, "claude-x[1m]"), 1_000_000)

    def test_theme_hue_keeps_brightness(self):
        c = self.hud.QColor("#4fd8ff")
        self.hud.HUE = 34
        try:
            t = self.hud.T("#4fd8ff")
        finally:
            self.hud.HUE = None
        self.assertEqual((t.hue(), t.value(), t.saturation()), (34, c.value(), c.saturation()))


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
