# Made by Claude
"""Claude Code hook for the neural HUD. Records whether this session is working
or waiting for the user (and why), logs tool calls and when they finish for the
activity stream, keeps the session's plan for the checklist panel, and starts
the HUD daemon when work begins. Never fails the hook."""
import difflib
import fcntl
import json
import os
import re
import subprocess
import sys
import time

TS = time.time()  # hook start time, used to order async hooks that finish out of order
STATE_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "claude-neural")
DAEMON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud.py")
EVENTS = os.path.join(STATE_DIR, "events.jsonl")
SID = "default"  # this event's session, set in main()
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


def rel(path, cwd):
    path = str(path or "")
    base = (cwd or "").rstrip("/")
    if base and path.startswith(base + "/"):
        return path[len(base) + 1:]
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + "/") else path


def describe(tool, inp, cwd):
    if tool == "Read":
        return "READ", rel(inp.get("file_path"), cwd)
    if tool in ("Edit", "Write", "NotebookEdit"):
        return "WRITE", rel(inp.get("file_path") or inp.get("notebook_path"), cwd)
    if tool in ("Bash", "PowerShell"):
        return "EXEC", inp.get("description") or "shell command"
    if tool in ("Grep", "Glob"):
        return "SEARCH", str(inp.get("pattern", ""))
    if tool in ("Agent", "Task"):
        return "AGENT", inp.get("description") or "subagent"
    if tool == "WebFetch":
        return "NET", re.sub(r"^https?://([^/]+).*", r"\1", str(inp.get("url", "")))
    if tool == "WebSearch":
        return "NET", str(inp.get("query", ""))
    if tool == "Skill":
        return "SKILL", str(inp.get("skill", ""))
    if tool == "TodoWrite":
        todos = [t for t in inp.get("todos") or [] if isinstance(t, dict)]
        now_doing = next((t for t in todos if t.get("status") == "in_progress"), None)
        done = sum(1 for t in todos if t.get("status") == "completed")
        if now_doing:
            return "PLAN", now_doing.get("activeForm") or now_doing.get("content") or "plan updated"
        return "PLAN", f"{done}/{len(todos)} steps done"
    if tool in PLAN_TOOLS:
        if inp.get("status") == "completed":
            return "PLAN", f"step {inp.get('taskId', '')} done"
        return "PLAN", inp.get("activeForm") or inp.get("subject") or f"step {inp.get('taskId', '')} updated"
    if tool == "AskUserQuestion":
        qs = inp.get("questions") or [{}]
        return "QUESTION", (qs[0] or {}).get("question") or "question for you"
    if tool.startswith("mcp__"):
        return "MCP", " › ".join(tool.split("__")[1:])
    return "TOOL", tool


PLAN_TOOLS = ("TodoWrite", "TaskCreate", "TaskUpdate")


def clean(text, n=160):
    return " ".join(str(text).split())[:n]


def emit(kind, detail, **extra):
    """Append one line to the activity log. `id` and `tool` pair a step with its END."""
    ev = {"ts": TS, "kind": kind, "detail": clean(detail), "sid": SID}
    ev.update({k: v for k, v in extra.items() if v})
    with open(EVENTS, "a") as f:
        f.write(json.dumps(ev) + "\n")


def locked_json(path, update):
    """Read-modify-write a small JSON file under an exclusive lock: hooks run in parallel."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    with os.fdopen(fd, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = json.loads(f.read() or "{}")
        except ValueError:
            data = {}
        new = update(data)
        if new is not None:
            f.seek(0)
            f.truncate()
            json.dump(new, f)


def set_reason(path, kind, detail, weak=False):
    """Why the session is waiting, for the HUD's standby screen. A weak reason (the generic
    notification text) never replaces a specific one written moments before."""
    def update(old):
        if old.get("ts", 0) > TS or (weak and TS - old.get("ts", 0) < 30):
            return None
        return {"ts": TS, "kind": kind, "detail": clean(detail, 200)}
    locked_json(path, update)


def task_id(resp):
    """The id TaskCreate assigned, wherever the tool response puts it."""
    if isinstance(resp, dict):
        for k in ("taskId", "id"):
            if isinstance(resp.get(k), (str, int)):
                return str(resp[k])
        for v in resp.values():
            found = task_id(v)
            if found:
                return found
        return None
    m = re.search(r"#(\d+)", str(resp or ""))
    return m.group(1) if m else None


def update_plan(path, tool, inp, resp):
    """Keep the session's plan: {"ts", "items": [{"id", "text", "active", "status"}]}."""
    def update(plan):
        items = plan.get("items", [])
        if tool == "TodoWrite":
            if plan.get("ts", 0) > TS:
                return None  # a later full list already landed
            items = [{"id": str(i + 1), "text": clean(t.get("content", ""), 120),
                      "active": clean(t.get("activeForm", ""), 120), "status": t.get("status", "pending")}
                     for i, t in enumerate(inp.get("todos") or []) if isinstance(t, dict)]
        elif tool == "TaskCreate":
            tid = task_id(resp) or str(1 + max([int(i["id"]) for i in items if str(i["id"]).isdigit()] or [0]))
            items = [i for i in items if i["id"] != tid] + [{
                "id": tid, "text": clean(inp.get("subject", ""), 120),
                "active": clean(inp.get("activeForm", ""), 120), "status": "pending"}]
        elif tool == "TaskUpdate":
            tid = str(inp.get("taskId", ""))
            if inp.get("status") == "deleted":
                items = [i for i in items if i["id"] != tid]
            else:
                item = next((i for i in items if i["id"] == tid), None)
                if item is None and inp.get("subject"):
                    item = {"id": tid, "text": "", "active": "", "status": "pending"}
                    items.append(item)
                if item is not None:
                    for src, dst in (("status", "status"), ("subject", "text"), ("activeForm", "active")):
                        if inp.get(src):
                            item[dst] = clean(inp[src], 120)
        return {"ts": max(TS, plan.get("ts", 0)), "items": items}
    locked_json(path, update)


def line_delta(old, new):
    """(lines added, lines removed) turning text old into new."""
    add = rm = 0
    sm = difflib.SequenceMatcher(None, str(old or "").splitlines(), str(new or "").splitlines(), autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            rm += i2 - i1
        if tag in ("replace", "insert"):
            add += j2 - j1
    return add, rm


def edit_delta(tool, inp, resp):
    """(added, removed) lines for a finished edit. Prefers the patch Claude Code reports,
    else works it out from the tool's input."""
    if isinstance(resp, dict) and isinstance(resp.get("structuredPatch"), list):
        lines = [ln for h in resp["structuredPatch"] if isinstance(h, dict) for ln in h.get("lines") or []]
        return sum(1 for ln in lines if ln.startswith("+")), sum(1 for ln in lines if ln.startswith("-"))
    if tool == "Edit":
        add, rm = line_delta(inp.get("old_string"), inp.get("new_string"))
        return add, rm
    if tool == "MultiEdit":
        pairs = [line_delta(e.get("old_string"), e.get("new_string")) for e in inp.get("edits") or [] if isinstance(e, dict)]
        return sum(a for a, _ in pairs), sum(r for _, r in pairs)
    if tool == "Write":
        return len(str(inp.get("content") or "").splitlines()), 0
    if tool == "NotebookEdit":
        return len(str(inp.get("new_source") or "").splitlines()), 0
    return 0, 0


def write_meta(data, first_prompt):
    """The session's project dir and transcript (for the header and the context gauge),
    and its own turn start, so several sessions keep separate tracks."""
    meta = os.path.join(STATE_DIR, f"m-{SID}.json")
    if first_prompt or not os.path.exists(meta):
        with open(meta + ".tmp", "w") as f:
            json.dump({"cwd": data.get("cwd", ""), "transcript": data.get("transcript_path", "")}, f)
        os.replace(meta + ".tmp", meta)
    if first_prompt:
        with open(os.path.join(STATE_DIR, f"turn-{SID}"), "w") as f:
            f.write(str(TS))


def set_state(path, state):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    with os.fdopen(fd, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        old = f.read().split()
        if len(old) == 2 and float(old[1]) > TS:
            return  # a later event already wrote this session's state
        f.seek(0)
        f.truncate()
        f.write(f"{state} {TS}")


def daemon_running():
    try:
        with open(os.path.join(STATE_DIR, "daemon.lock"), "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
    except OSError:
        return True


def main():
    global SID
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    event = data.get("hook_event_name", "")
    tool = str(data.get("tool_name", ""))
    cwd = data.get("cwd", "")
    sid = SID = "".join(ch for ch in str(data.get("session_id", "default")) if ch.isalnum() or ch == "-")
    os.makedirs(STATE_DIR, exist_ok=True)
    write_meta(data, event == "UserPromptSubmit")
    path = os.path.join(STATE_DIR, "s-" + sid)
    reason = os.path.join(STATE_DIR, "w-" + sid)
    inp = data.get("tool_input") or {}
    tid = data.get("tool_use_id")

    if event in ("Stop", "SessionEnd", "StopFailure"):
        set_state(path, "done")
        if event == "Stop":
            emit("DONE", "task complete")
        return
    if event == "PreToolUse":
        kind, detail = describe(tool, inp, cwd)
        if tool == "AskUserQuestion":
            set_reason(reason, "QUESTION", detail)
            set_state(path, "waiting")
        else:
            emit(kind, detail, id=tid, tool=tool)
        return  # no state change: a permission prompt may follow
    if event == "PermissionRequest":
        kind, detail = describe(tool, inp, cwd)
        set_reason(reason, "PERMISSION", f"{kind} · {detail}")
        set_state(path, "waiting")
        return
    if event == "Notification":
        set_reason(reason, "PERMISSION", data.get("message") or "Claude needs your permission", weak=True)
        set_state(path, "waiting")
        return

    # UserPromptSubmit, PostToolUse, PostToolUseFailure: Claude is working
    if event == "UserPromptSubmit":
        with open(os.path.join(STATE_DIR, "turn_start"), "w") as f:
            f.write(str(TS))
        try:
            if os.path.getsize(EVENTS) > 512 * 1024:
                open(EVENTS, "w").close()
        except OSError:
            pass
        emit("LINK", "directive received")
    else:  # the step finished: lets the HUD time it
        if tool != "AskUserQuestion":
            add = rm = 0
            if tool in EDIT_TOOLS and event == "PostToolUse":
                add, rm = edit_delta(tool, inp, data.get("tool_response"))
            emit("END", tool, id=tid, tool=tool, add=add, rm=rm,
                 file=rel(inp.get("file_path") or inp.get("notebook_path"), cwd) if add or rm else "")
        if event == "PostToolUseFailure":
            err = clean(data.get("error") or "", 90)
            emit("FAULT", f"{describe(tool, inp, cwd)[0]} failed" + (f": {err}" if err else ""))
        elif tool in PLAN_TOOLS:
            update_plan(os.path.join(STATE_DIR, f"plan-{sid}.json"), tool, inp, data.get("tool_response"))
    set_state(path, "working")

    try:  # closed with the shortcut during this turn: stay closed until the next prompt
        with open(os.path.join(STATE_DIR, "turn_start")) as f:
            if os.path.getmtime(os.path.join(STATE_DIR, "dismissed")) >= float(f.read()):
                return
    except (OSError, ValueError):
        pass
    # The HUD draws through X (XWayland on Wayland). Without a display, e.g. Claude Code run
    # over SSH, there is nothing to draw on: skip the launch rather than start Qt for nothing
    # on every step. The state is still recorded, so a HUD already on screen keeps up.
    if not os.environ.get("DISPLAY"):
        return
    if not daemon_running():
        subprocess.Popen([sys.executable, DAEMON], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=open(os.path.join(STATE_DIR, "daemon.err"), "w"), start_new_session=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
