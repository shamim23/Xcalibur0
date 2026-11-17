"""Day 5 — slash commands: talk to the harness, not the model.

Concept taught: a REPL line that starts with "/" never reaches the model. It is
a command for the harness itself — switch model, compact, inspect memory — so
the user can steer the agent between runs without spending a turn on it.

Each command is ``fn(repl, arg) -> None | "exit"``. ``repl`` exposes
``harness`` and ``console``. Commands are registered in ``COMMANDS`` with a
one-line help string, which also feeds tab completion.
"""

import os
import shlex
import subprocess
import time

from . import context, memory, session, skills
from .security import MODES

COMMANDS = {}


def command(name, help_text):
    def wrap(fn):
        COMMANDS[name] = (help_text, fn)
        return fn
    return wrap


def dispatch(repl, line):
    """Run a "/name arg" line; return "exit" to leave the REPL, else None."""
    name, _, arg = line.strip()[1:].partition(" ")
    entry = COMMANDS.get(name.lower())
    if entry is None:
        matches = [n for n in COMMANDS if n.startswith(name.lower())]
        if len(matches) == 1:
            entry = COMMANDS[matches[0]]
        else:
            repl.console.error("unknown command /%s — try /help" % name)
            return None
    return entry[1](repl, arg.strip())


def complete(text, state):
    """readline completer for slash commands."""
    if not text.startswith("/"):
        return None
    options = sorted("/" + n for n in COMMANDS if ("/" + n).startswith(text))
    return options[state] if state < len(options) else None


@command("help", "show this list")
def _help(repl, arg):
    style = repl.console.style
    width = max(len(n) for n in COMMANDS) + 2
    for name, (text, _) in COMMANDS.items():
        repl.console.print("  %s %s" % (style.cyan(("/" + name).ljust(width)), text))
    repl.console.print(style.dim(
        "  End a line with \\ to continue it, or wrap a block in \"\"\" lines. "
        "Ctrl-C interrupts a run, Ctrl-D exits."))


@command("exit", "leave xcalibur")
def _exit(repl, arg):
    return "exit"


COMMANDS["quit"] = ("leave xcalibur", _exit)


@command("clear", "start a fresh conversation (the old session log is kept)")
def _clear(repl, arg):
    repl.harness.reset()
    repl.console.print(repl.console.style.dim("Started a new conversation."))


@command("mode", "show or set the permission mode: %s" % " | ".join(MODES))
def _mode(repl, arg):
    policy = repl.harness.policy
    if not arg:
        repl.console.print("mode: %s" % policy.mode)
    elif arg not in MODES:
        repl.console.error("mode must be one of %s" % ", ".join(MODES))
    else:
        policy.mode = arg
        repl.console.print("mode set to %s" % repl.console.style.bold(arg))


@command("model", "show or set the model")
def _model(repl, arg):
    if arg:
        repl.harness.model = arg
        repl.console.print("model set to %s" % repl.console.style.bold(arg))
    else:
        repl.console.print("model: %s" % repl.harness.model)


@command("compact", "summarize the conversation so far to free context")
def _compact(repl, arg):
    harness = repl.harness
    estimate = context.estimate_tokens(harness.messages)
    repl.console.spinner.start("compacting")
    try:
        before, after = harness.compact()
    finally:
        repl.console.spinner.stop()
    if before == after:
        repl.console.print("Nothing to compact (%d messages)." % before)
    else:
        repl.console.print("Compacted %d messages (~%s tokens) -> %d (~%s tokens)." % (
            before, format(estimate, ","), after,
            format(context.estimate_tokens(harness.messages), ",")))


@command("tokens", "token usage for the last run and this session")
def _tokens(repl, arg):
    console, harness = repl.console, repl.harness
    console.print("last run: " + console.usage_line(console.run_usage))
    console.print("session:  " + console.usage_line(console.total_usage))
    console.print(console.style.dim("context:  ~%s of %s token budget (%d messages)" % (
        format(context.estimate_tokens(harness.messages), ","),
        format(harness.budget_tokens, ","), len(harness.messages))))


@command("tools", "list the tools the agent can call")
def _tools(repl, arg):
    style = repl.console.style
    for name, item in repl.harness.tools.items():
        description = item.spec["schema"].get("description", "")
        mark = style.green(" (always)") if name in repl.console.always else ""
        repl.console.print("  %s%s %s" % (style.cyan(name), mark, style.dim(description)))


@command("memory", "show project memory (XCALIBUR.md); '/memory edit' opens $EDITOR")
def _memory(repl, arg):
    path = os.path.join(repl.harness.workdir, memory.MEMORY_FILE)
    if arg == "edit":
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        if not os.path.exists(path):
            open(path, "a", encoding="utf-8").close()
        subprocess.call(shlex.split(editor) + [path])
        repl.harness.refresh_system()
        repl.console.print("Memory reloaded.")
        return
    if not os.path.isfile(path):
        repl.console.print("No %s yet. Add facts with /remember or /memory edit."
                           % memory.MEMORY_FILE)
        return
    with open(path, encoding="utf-8", errors="replace") as handle:
        repl.console.print(handle.read().rstrip() or "(empty)")


@command("remember", "save a fact to project memory: /remember <fact>")
def _remember(repl, arg):
    if not arg:
        repl.console.error("usage: /remember <fact>")
        return
    repl.console.print(memory.remember(repl.harness.workdir, arg))
    repl.harness.refresh_system()


@command("skills", "list skills under ./skills")
def _skills(repl, arg):
    found = skills.catalog(repl.harness.workdir)
    if not found:
        repl.console.print("No skills. Add one at skills/<name>/SKILL.md.")
    for name, info in found.items():
        repl.console.print("  %s %s" % (repl.console.style.cyan(name),
                                         repl.console.style.dim(info["description"])))


@command("sessions", "list recent sessions in this directory")
def _sessions(repl, arg):
    print_sessions(repl.console, repl.harness.workdir, current=repl.harness.session_path)


@command("resume", "resume a session: /resume [id prefix] (latest if omitted)")
def _resume(repl, arg):
    harness = repl.harness
    path = session.find(harness.workdir, arg) if arg else session.latest(harness.workdir)
    if not path:
        repl.console.error("no session matches %r" % arg if arg else "no sessions yet")
        return
    harness.reset()
    if harness.resume(path):
        repl.console.print("Resumed %s (%d messages)." % (
            os.path.basename(path), len(harness.messages)))
    else:
        repl.console.error("session %s is empty" % os.path.basename(path))


def print_sessions(console, workdir, limit=20, current=None):
    """Print a table of sessions, newest first."""
    items = session.list_sessions(workdir)
    if not items:
        console.print("No sessions in %s." % workdir)
        return
    style = console.style
    for item in items[:limit]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(item["mtime"]))
        mark = style.green(" *") if item["path"] == current else ""
        console.print("  %s  %s  %s  %s%s" % (
            style.cyan(item["id"]), style.dim(when),
            style.dim("%3d msgs" % item["messages"]),
            item["title"][:60], mark))
    if len(items) > limit:
        console.print(style.dim("  ... and %d more" % (len(items) - limit)))
