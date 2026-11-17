"""Day 5 — the command line: XCALIBUR as a tool you can run.

Concept taught: the CLI is a thin shell over the harness. It parses flags,
renders events (``ui.py``), routes "/" lines to harness commands
(``commands.py``), and leaves the agent itself to ``Harness``.

    xcalibur                                  # interactive, safe mode
    xcalibur "fix the failing test"           # interactive, first task runs at once
    xcalibur -p "add tests"                   # headless, yolo mode
    cat err.log | xcalibur -p "explain this"  # piped input is appended to the task
    xcalibur -p "..." --output-format json    # one JSON object on stdout
    xcalibur -c                               # continue the latest session
    xcalibur sessions | fleet JOBS.json | doctor

Exit codes: 0 ok, 1 runtime or provider error, 2 usage error, 130 interrupted.
"""

import argparse
import json
import os
import platform
import sys
import time

from . import __version__, commands, memory, provider, session, skills
from .fleet import run_fleet
from .harness import Harness
from .security import MODES, Policy
from .ui import Console, clip, color_enabled

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130
OUTPUT_FORMATS = ("text", "json", "stream-json")
SUBCOMMANDS = ("sessions", "fleet", "doctor")
HISTORY_FILE = os.environ.get("XCALIBUR_HISTORY") or os.path.expanduser("~/.xcalibur_history")


def ask_human(prompt):
    """Ask on stderr and read a line from stdin; EOFError when nobody is there."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="xcalibur", description="XCALIBUR, a minimal coding agent.",
        epilog="subcommands: xcalibur sessions | fleet JOBS.json | doctor "
               "(run 'xcalibur <subcommand> -h' for details)")
    parser.add_argument("task", nargs="*", help="first task; starts the REPL with it")
    parser.add_argument("-p", "--prompt", help="run one task headless, then exit")
    parser.add_argument("--output-format", choices=OUTPUT_FORMATS, default="text",
                        help="headless output: text (default) | json | stream-json")
    parser.add_argument("-d", "--workdir", default=".", help="working directory (the jail)")
    parser.add_argument("-m", "--model", help="model name (default $XCALIBUR_MODEL or %s)"
                        % provider.DEFAULT_MODEL)
    parser.add_argument("--mode", choices=MODES,
                        help="safe (default interactively) | yolo (default headless) | read-only")
    parser.add_argument("-c", "--continue", dest="cont", action="store_true",
                        help="continue the latest session")
    parser.add_argument("--resume", nargs="?", const="", metavar="ID",
                        help="resume a session by id prefix (latest if omitted)")
    parser.add_argument("--max-turns", type=int, default=120)
    parser.add_argument("--budget-tokens", type=int, default=600_000,
                        help="compact the transcript above this many tokens")
    parser.add_argument("--system-append", default="", metavar="TEXT",
                        help="extra text for the system prompt")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="headless text mode: show tool calls on stderr")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument("--no-banner", action="store_true", help="skip the startup animation")
    parser.add_argument("-V", "--version", action="version",
                        version="xcalibur %s" % __version__)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        handler = {"sessions": cmd_sessions, "fleet": cmd_fleet, "doctor": cmd_doctor}
        return handler[argv[0]](argv[1:])

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else EXIT_USAGE

    first = " ".join(args.task).strip()
    piped = "" if _stdin_is_tty() else sys.stdin.read()
    headless = args.prompt is not None or bool(piped)
    if headless:
        task = "\n\n".join(part for part in ((args.prompt or first), piped.strip()) if part)
        if not task:
            parser.print_usage(sys.stderr)
            sys.stderr.write("xcalibur: error: empty prompt\n")
            return EXIT_USAGE
        return run_headless(args, task)
    return run_interactive(args, first)


def _stdin_is_tty():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def make_harness(args, console, mode):
    return Harness(args.workdir, model=args.model,
                   policy=Policy(mode, console.approve), on_event=console.on_event,
                   max_turns=args.max_turns, budget_tokens=args.budget_tokens,
                   system_extra=args.system_append)


def resume_requested(args, harness):
    """Apply -c/--resume; return (wanted, error message or None)."""
    if not (args.cont or args.resume is not None):
        return False, None
    path = (session.find(harness.workdir, args.resume) if args.resume
            else session.latest(harness.workdir))
    if not path:
        return True, ("no session matches %r" % args.resume if args.resume
                      else "no session to resume in %s" % harness.workdir)
    if not harness.resume(path):
        return True, "session %s is empty" % os.path.basename(path)
    return True, None


# --------------------------------------------------------------------------
# headless
# --------------------------------------------------------------------------
def _public(call):
    """A tool call without the provider's opaque reasoning signature."""
    return {"name": call.get("name"), "args": call.get("args") or {}}


def run_headless(args, task):
    fmt = args.output_format
    show = fmt == "text" and (args.verbose or _isatty(sys.stderr))
    console = Console(out=sys.stderr, err=sys.stderr, no_color=args.no_color,
                      show_events=show, ask=ask_human, echo_final=False)
    if fmt != "text":
        console.spinner.enabled = False
    mode = args.mode or "yolo"
    harness = make_harness(args, console, mode)

    def emit(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()

    if fmt == "stream-json":
        def on_event(kind, data):
            console.on_event(kind, data)
            if kind == "assistant":
                emit({"type": "assistant", "text": data.get("text") or "",
                      "tool_calls": [_public(c) for c in data.get("tool_calls") or []],
                      "usage": data.get("usage") or {}})
            elif kind == "tool_start":
                emit({"type": "tool_start", **_public(data)})
            elif kind == "tool_end":
                emit({"type": "tool_end", "name": (data.get("call") or {}).get("name"),
                      "result": str(data.get("result"))})
        harness.on_event = on_event

    _, error = resume_requested(args, harness)
    if error:
        return _finish(fmt, emit, console, harness, None, error, EXIT_ERROR)

    console.begin_run()
    try:
        result = harness.run(task)
    except KeyboardInterrupt:
        return _finish(fmt, emit, console, harness, None, "interrupted", EXIT_INTERRUPTED)
    except RuntimeError as error:
        return _finish(fmt, emit, console, harness, None, str(error), EXIT_ERROR)
    finally:
        console.end_run()
    return _finish(fmt, emit, console, harness, result, None, EXIT_OK)


def _finish(fmt, emit, console, harness, result, error, code):
    if fmt == "text":
        if error:
            console.error(error)
        else:
            sys.stdout.write((result or "") + "\n")
            sys.stdout.flush()
            if console.show_events:
                console.print(console.usage_line(), stream=sys.stderr)
        return code
    payload = {"ok": error is None, "result": result, "session": harness.session_path,
               "model": harness.model, "turns": console.run_usage["turns"],
               "usage": {"input": console.run_usage["input"],
                         "output": console.run_usage["output"]}}
    if error:
        payload["error"] = error
    if fmt == "stream-json":
        payload = {"type": "result", **payload}
    emit(payload)
    return code


def _isatty(stream):
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


# --------------------------------------------------------------------------
# interactive
# --------------------------------------------------------------------------
class Repl:
    """The read-eval-print loop: slash commands go to the harness, the rest to the agent."""

    def __init__(self, harness, console):
        self.harness, self.console = harness, console
        self._readline = _setup_readline()

    def prompt(self, continuation=False):
        style = self.console.style
        text = "... " if continuation else "xcalibur> "
        if not style.enabled:
            return text
        # \001/\002 tell readline the escapes take no width, so editing stays aligned.
        code = "2" if continuation else "1;36"
        if self._readline:
            return "\001\033[%sm\002%s\001\033[0m\002" % (code, text)
        return "\033[%sm%s\033[0m" % (code, text)

    def read(self):
        """Read one entry: a line, a "\\"-continued line, or a \"\"\" block."""
        line = input(self.prompt())
        if line.strip() == '"""':
            lines = []
            while True:
                more = input(self.prompt(True))
                if more.strip() == '"""':
                    return "\n".join(lines)
                lines.append(more)
        lines = []
        while line.endswith("\\"):
            lines.append(line[:-1])
            line = input(self.prompt(True))
        lines.append(line)
        return "\n".join(lines)

    def run_task(self, task):
        console = self.console
        console.begin_run()
        try:
            self.harness.run(task)
        except KeyboardInterrupt:
            console.end_run()
            console.print(console.style.yellow("\nInterrupted.") + console.style.dim(
                " The session log is kept; send another message to carry on."))
            return
        except RuntimeError as error:  # provider failures: report, keep the session
            console.end_run()
            console.error(error)
            return
        console.end_run()
        console.print(console.usage_line())

    def loop(self, first=""):
        if first:
            self.console.print(self.console.style.bold("xcalibur> ") + first)
            self.run_task(first)
        while True:
            try:
                entry = self.read()
            except EOFError:
                self.console.print()
                return EXIT_OK
            except KeyboardInterrupt:
                self.console.print()
                continue
            if not entry.strip():
                continue
            if entry.lstrip().startswith("/"):
                try:
                    if commands.dispatch(self, entry) == "exit":
                        return EXIT_OK
                except KeyboardInterrupt:
                    self.console.print()
                except Exception as error:  # a broken command must not end the REPL
                    self.console.error("%s: %s" % (type(error).__name__, error))
                continue
            self.run_task(entry)


def _setup_readline():
    """Enable history and slash-command completion if readline exists."""
    try:
        import atexit
        import readline
    except ImportError:
        return False
    try:
        readline.read_history_file(HISTORY_FILE)
    except (OSError, IOError):
        pass
    readline.set_history_length(1000)

    def save():
        try:
            readline.write_history_file(HISTORY_FILE)
        except (OSError, IOError):
            pass
    atexit.register(save)
    readline.set_completer(commands.complete)
    readline.set_completer_delims(" \t\n")
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
    return True


def show_banner(args, console, harness, mode):
    try:
        from . import banner
    except ImportError:
        console.print("%s  v%s  model %s  mode %s  jail %s" % (
            console.style.bold("XCALIBUR"), __version__, harness.model, mode,
            harness.workdir))
        return
    info = dict(version=__version__, model=harness.model, mode=mode,
                workdir=harness.workdir)
    animate = (console.style.enabled and not args.no_banner
               and not os.environ.get("XCALIBUR_NO_BANNER"))
    if animate:
        banner.play(sys.stdout, **info)
    else:
        banner.static(sys.stdout, color=console.style.enabled, **info)


def run_interactive(args, first=""):
    console = Console(no_color=args.no_color, ask=input)
    mode = args.mode or "safe"
    harness = make_harness(args, console, mode)
    show_banner(args, console, harness, mode)
    wanted, error = resume_requested(args, harness)
    if error:
        console.error(error)
    elif wanted:
        console.print(console.style.dim("Resumed %s (%d messages)." % (
            os.path.basename(harness.session_path), len(harness.messages))))
    console.print(console.style.dim(
        "/help for commands · Ctrl-C interrupts a run · Ctrl-D exits"))
    return Repl(harness, console).loop(first)


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
def cmd_sessions(argv):
    parser = argparse.ArgumentParser(
        prog="xcalibur sessions", description="List sessions, or show one.")
    parser.add_argument("action", nargs="?", choices=("list", "show"), default="list")
    parser.add_argument("id", nargs="?", help="session id prefix (for show)")
    parser.add_argument("-d", "--workdir", default=".")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else EXIT_USAGE
    workdir = os.path.realpath(args.workdir)
    console = Console(no_color=args.no_color)

    if args.action == "list":
        if args.json:
            print(json.dumps(session.list_sessions(workdir)[:args.limit], indent=2))
        else:
            commands.print_sessions(console, workdir, limit=args.limit)
        return EXIT_OK

    path = session.find(workdir, args.id) if args.id else session.latest(workdir)
    if not path:
        console.error("no session matches %r" % (args.id or ""))
        return EXIT_ERROR
    messages = session.load(path)
    if args.json:
        print(json.dumps([{k: v for k, v in m.items() if k != "tool_calls"}
                          | ({"tool_calls": [_public(c) for c in m["tool_calls"]]}
                             if m.get("tool_calls") else {})
                          for m in messages], indent=2, ensure_ascii=False))
        return EXIT_OK
    style = console.style
    console.print(style.bold(os.path.basename(path)))
    for message in messages:
        role = message.get("role")
        if role == "user":
            console.print(style.cyan("user> ") + message.get("text", ""))
        elif role == "assistant":
            if message.get("text"):
                console.print(message["text"])
            for call in message.get("tool_calls") or []:
                console.print(style.cyan("● %s " % call.get("name"))
                              + style.dim(clip(json.dumps(call.get("args") or {}))))
        elif role == "tool":
            console.print(style.dim("  ⎿ " + clip(message.get("text", ""))))
    return EXIT_OK


def cmd_fleet(argv):
    parser = argparse.ArgumentParser(
        prog="xcalibur fleet",
        description="Run several agents in parallel. JOBS is a JSON list of "
                    "{\"name\", \"workdir\", \"task\"}; relative workdirs are "
                    "resolved against the JSON file's directory.")
    parser.add_argument("jobs", help="path to the jobs JSON file")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--mode", choices=MODES, default="yolo")
    parser.add_argument("-m", "--model")
    parser.add_argument("--max-turns", type=int, default=120)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else EXIT_USAGE
    console = Console(no_color=args.no_color)
    try:
        with open(args.jobs, encoding="utf-8") as handle:
            jobs = json.load(handle)
        base = os.path.dirname(os.path.abspath(args.jobs))
        jobs = [{"name": str(job.get("name") or "job%d" % i),
                 "workdir": os.path.join(base, job["workdir"]),
                 "task": str(job["task"])} for i, job in enumerate(jobs, 1)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        console.error("bad jobs file %s: %s" % (args.jobs, error))
        return EXIT_USAGE

    console.print(console.style.dim("running %d jobs, %d at a time..."
                                    % (len(jobs), args.workers)), stream=sys.stderr)
    results = run_fleet(jobs, lambda workdir: Harness(
        workdir, model=args.model, policy=Policy(args.mode), max_turns=args.max_turns),
        max_workers=args.workers)
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for result in results:
            mark = console.style.green("✓") if result["ok"] else console.style.red("✗")
            console.print("%s %s" % (mark, console.style.bold(result["name"])))
            for line in str(result["report"]).splitlines():
                console.print("    " + line)
    return EXIT_OK if all(r["ok"] for r in results) else EXIT_ERROR


def cmd_doctor(argv):
    parser = argparse.ArgumentParser(prog="xcalibur doctor",
                                     description="Check that xcalibur can run here.")
    parser.add_argument("-d", "--workdir", default=".")
    parser.add_argument("-m", "--model")
    parser.add_argument("--ping", action="store_true", help="make one tiny API call")
    parser.add_argument("--no-color", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else EXIT_USAGE
    console = Console(no_color=args.no_color)
    style = console.style
    workdir = os.path.realpath(args.workdir)
    model = args.model or os.environ.get("XCALIBUR_MODEL") or provider.DEFAULT_MODEL
    failed = False

    def check(label, ok, detail):
        nonlocal failed
        failed = failed or ok is False
        mark = {True: style.green("✓"), False: style.red("✗"), None: style.yellow("·")}[ok]
        console.print("%s %-10s %s" % (mark, label, detail))

    check("xcalibur", True, "%s (python %s, %s)" % (
        __version__, platform.python_version(), platform.system()))
    check("python", sys.version_info >= (3, 9),
          "needs 3.9+" if sys.version_info < (3, 9) else "ok")
    try:
        key = provider.api_key()
        check("api key", True, "%s…%s" % (key[:7], key[-4:]) if len(key) > 12 else "set")
    except RuntimeError as error:
        key = None
        check("api key", False, str(error))
    check("model", True, model)
    writable = os.path.isdir(workdir) and os.access(workdir, os.W_OK)
    check("workdir", writable, workdir + ("" if writable else " (missing or not writable)"))
    has_memory = os.path.isfile(os.path.join(workdir, memory.MEMORY_FILE))
    check("memory", None if not has_memory else True,
          memory.MEMORY_FILE if has_memory else "no %s (optional)" % memory.MEMORY_FILE)
    count = len(skills.catalog(workdir))
    check("skills", True if count else None, "%d found" % count)
    check("sessions", True if session.list_sessions(workdir) else None,
          "%d in %s" % (len(session.list_sessions(workdir)), session.SESSION_DIR))
    if args.ping:
        if key is None:
            check("ping", False, "skipped: no API key")
        else:
            started = time.time()
            try:
                reply = provider.complete(model, "Reply with the single word: pong",
                                          [{"role": "user", "text": "ping"}], [])
                check("ping", True, "%r in %.1fs" % (clip(reply["text"], 30),
                                                     time.time() - started))
            except RuntimeError as error:
                check("ping", False, clip(error, 200))
    return EXIT_ERROR if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
