"""Day 4 — durability: a run that survives the process dying.

Concept taught: the transcript is the agent's whole state, so persisting it is
persisting the agent. Every message is appended to a JSONL file the moment it
exists; after a crash, ``load`` reads the file back and the loop continues as
if nothing happened.

Design rules this file embodies:
  * Append-only, one message per line. A crash can only damage the last line,
    never the history before it.
  * A torn tail is expected, not exceptional. The first line that fails to
    parse ends the file; everything before it is kept.
  * The loaded transcript is always valid to send. If the process died between
    the model asking for tools and those tools finishing, the missing results
    are filled in with an "interrupted" note, because every provider rejects a
    tool call that has no matching result.
"""

import json
import os
import re
import time

SESSION_DIR = ".xcalibur/sessions"
SLUG_CHARS = 40
INTERRUPTED = "Interrupted before this ran (process restarted)."


def new_session(workdir, label="session"):
    """Create the session directory and return a fresh session file path."""
    directory = os.path.join(workdir, SESSION_DIR)
    os.makedirs(directory, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(label)).strip("-")[:SLUG_CHARS].strip("-")
    return os.path.join(directory, "%d-%s.jsonl" % (int(time.time()), slug or "session"))


def append(path, message):
    """Append one message to the session file as a single JSON line."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")


def load(path):
    """Read a session file back into a message list that is safe to resume.

    The returned list may end with synthesized tool results that are not in the
    file. A caller that keeps appending to the same file should append those
    too, or a later load would find the calls unanswered mid-history.
    """
    messages = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                break  # torn tail: the process died mid-write
    return _repair(messages)


def latest(workdir):
    """Return the most recently written session file, or None."""
    directory = os.path.join(workdir, SESSION_DIR)
    if not os.path.isdir(directory):
        return None
    paths = [os.path.join(directory, name) for name in os.listdir(directory)
             if name.endswith(".jsonl")]
    # Most recently written wins, so a resumed session beats a newer idle one.
    return max(paths, key=lambda p: (os.path.getmtime(p), p)) if paths else None


def list_sessions(workdir):
    """Return [{id, path, mtime, messages, title}] for every session, newest first."""
    directory = os.path.join(workdir, SESSION_DIR)
    if not os.path.isdir(directory):
        return []
    found = []
    for name in os.listdir(directory):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(directory, name)
        count, title = 0, ""
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    break
                count += 1
                if not title and message.get("role") == "user":
                    title = " ".join(str(message.get("text") or "").split())
        found.append({"id": name[:-len(".jsonl")], "path": path,
                      "mtime": os.path.getmtime(path), "messages": count,
                      "title": title})
    return sorted(found, key=lambda s: (s["mtime"], s["path"]), reverse=True)


def find(workdir, prefix):
    """Return the path of the newest session whose id starts with ``prefix``."""
    for item in list_sessions(workdir):
        if item["id"].startswith(str(prefix)):
            return item["path"]
    return None


def _repair(messages):
    """Answer any tool calls of the last assistant message that never ran."""
    last = next((i for i in range(len(messages) - 1, -1, -1)
                 if messages[i].get("role") == "assistant"), None)
    if last is None:
        return messages
    # The loop appends results in call order, so the first N calls are the ones
    # that finished and everything past N is what the crash cut off.
    done = sum(1 for m in messages[last + 1:] if m.get("role") == "tool")
    for call in (messages[last].get("tool_calls") or [])[done:]:
        messages.append({"role": "tool", "name": call.get("name"), "text": INTERRUPTED})
    return messages
