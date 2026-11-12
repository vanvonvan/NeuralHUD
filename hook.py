# Made by Claude
"""Claude Code hook for the neural HUD. Records whether this session is working
or waiting for the user, logs tool calls for the activity stream, and starts the
HUD daemon when work begins. Never fails the hook."""
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
    if tool.startswith("mcp__"):
        return "MCP", " › ".join(tool.split("__")[1:])
    return "TOOL", tool


def emit(kind, detail):
    detail = " ".join(str(detail).split())[:160]
    with open(EVENTS, "a") as f:
        f.write(json.dumps({"ts": TS, "kind": kind, "detail": detail}) + "\n")


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
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    event = data.get("hook_event_name", "")
    tool = str(data.get("tool_name", ""))
    cwd = data.get("cwd", "")
    sid = "".join(ch for ch in str(data.get("session_id", "default")) if ch.isalnum() or ch == "-")
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "s-" + sid)

    if event in ("Stop", "SessionEnd", "StopFailure"):
        set_state(path, "done")
        if event == "Stop":
            emit("DONE", "task complete")
        return
    if event == "PreToolUse":
        if tool == "AskUserQuestion":
            set_state(path, "waiting")
        else:
            emit(*describe(tool, data.get("tool_input") or {}, cwd))
        return  # no state change: a permission prompt may follow
    if event in ("PermissionRequest", "Notification"):
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
    elif event == "PostToolUseFailure":
        emit("FAULT", f"{tool} failed")
    set_state(path, "working")

    try:  # closed with the shortcut during this turn: stay closed until the next prompt
        with open(os.path.join(STATE_DIR, "turn_start")) as f:
            if os.path.getmtime(os.path.join(STATE_DIR, "dismissed")) >= float(f.read()):
                return
    except (OSError, ValueError):
        pass
    if not daemon_running():
        subprocess.Popen([sys.executable, DAEMON], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=open(os.path.join(STATE_DIR, "daemon.err"), "w"), start_new_session=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
