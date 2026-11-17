"""Day 5 — terminal UI: colors, a spinner, an event printer and the approval prompt.

Concept taught: presentation is a separate seam from behavior. The harness only
emits events and asks an approver; this file decides how those look on a
terminal, and degrades to plain text when the output is a pipe or the user set
NO_COLOR.

Design rules this file embodies:
  * Never decorate what isn't a terminal. Escape codes in a log file or a pipe
    are noise, so color and the spinner switch off unless the stream is a TTY.
  * One lock for the screen. The spinner thread and the printer share a lock,
    and the spinner is always stopped before anything else is written.
  * Approval fails closed: EOF or any answer but yes/always is a no.
"""

import itertools
import json
import os
import sys
import threading
import time

CLIP = 120
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def color_enabled(stream, force_off=False):
    """True when ``stream`` is a TTY, NO_COLOR is unset and not forced off."""
    if force_off or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


class Style:
    """ANSI helpers that become no-ops when color is off."""

    def __init__(self, enabled):
        self.enabled = enabled

    def _wrap(self, code, text):
        return "\033[%sm%s\033[0m" % (code, text) if self.enabled else str(text)

    def bold(self, text):
        return self._wrap("1", text)

    def dim(self, text):
        return self._wrap("2", text)

    def red(self, text):
        return self._wrap("31", text)

    def green(self, text):
        return self._wrap("32", text)

    def yellow(self, text):
        return self._wrap("33", text)

    def cyan(self, text):
        return self._wrap("36", text)


def clip(text, limit=CLIP):
    """Collapse whitespace and cut ``text`` to ``limit`` characters."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def format_args(call, limit=CLIP):
    return clip(json.dumps(call.get("args") or {}, ensure_ascii=False), limit)


class Spinner:
    """A one-line "thinking" indicator on a TTY stream; silent elsewhere."""

    def __init__(self, stream, lock, enabled):
        self.stream, self.lock, self.enabled = stream, lock, enabled
        self._stop = threading.Event()
        self._thread = None
        self.label = "thinking"

    def start(self, label="thinking"):
        if not self.enabled or self._thread:
            return
        self.label = label
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        if not self._thread:
            return
        self._stop.set()
        self._thread.join()
        self._thread = None
        with self.lock:
            self.stream.write("\r\033[K")
            self.stream.flush()

    def _spin(self):
        started = time.time()
        for frame in itertools.cycle(FRAMES):
            if self._stop.wait(0.08):
                return
            with self.lock:
                self.stream.write("\r\033[2m%s %s %ds\033[0m\033[K"
                                  % (frame, self.label, time.time() - started))
                self.stream.flush()


class Console:
    """Prints harness events, runs the spinner and asks for approvals.

    ``out`` receives assistant text and tool lines; ``err`` hosts the spinner.
    Usage is summed from ``assistant`` events into ``run_usage`` and
    ``total_usage``.
    """

    def __init__(self, out=None, err=None, no_color=False, show_events=True,
                 ask=input, echo_final=True):
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.style = Style(color_enabled(self.out, no_color))
        self.lock = threading.Lock()
        self.spinner = Spinner(self.err, self.lock, color_enabled(self.err, no_color))
        self.show_events = show_events
        self.echo_final = echo_final  # False: the caller prints the final answer
        self.ask = ask
        self.always = set()          # tool names approved for the whole session
        self.total_usage = {"input": 0, "output": 0, "turns": 0}
        self.run_usage = dict(self.total_usage)

    # -- output -------------------------------------------------------------
    def print(self, text="", stream=None):
        self.spinner.stop()
        stream = stream or self.out
        with self.lock:
            stream.write(str(text) + "\n")
            stream.flush()

    def error(self, text):
        self.print(self.style.red("error: ") + str(text), stream=self.err)

    def begin_run(self):
        self.run_usage = {"input": 0, "output": 0, "turns": 0}
        self.spinner.start()

    def end_run(self):
        self.spinner.stop()

    def on_event(self, kind, data):
        """The harness ``on_event`` hook."""
        if kind == "assistant":
            self.spinner.stop()
            usage = data.get("usage") or {}
            for bucket in (self.run_usage, self.total_usage):
                bucket["input"] += usage.get("input") or 0
                bucket["output"] += usage.get("output") or 0
                bucket["turns"] += 1
            narration = self.echo_final or data.get("tool_calls")
            if self.show_events and narration and data.get("text"):
                self.print(data["text"])
        elif kind == "tool_start":
            self.spinner.stop()
            if self.show_events:
                self.print("%s %s" % (self.style.cyan("● " + str(data.get("name"))),
                                      self.style.dim(format_args(data))))
        elif kind == "tool_end":
            if self.show_events:
                result = str(data.get("result"))
                lines = result.splitlines() or [""]
                more = " (+%d lines)" % (len(lines) - 1) if len(lines) > 1 else ""
                head = clip(lines[0]) + more
                if result.startswith(("ERROR", "BLOCKED")):
                    head = self.style.red(head)
                else:
                    head = self.style.dim(head)
                self.print("  ⎿ " + head)
            self.spinner.start("working")

    def usage_line(self, usage=None):
        usage = usage or self.run_usage
        return self.style.dim("%s in · %s out tokens · %d turns" % (
            format(usage["input"], ","), format(usage["output"], ","), usage["turns"]))

    # -- approvals ----------------------------------------------------------
    def approve(self, call, reason):
        """Policy approver: y = once, a = this tool for the session, else no."""
        name = call.get("name")
        if name in self.always:
            return True
        self.spinner.stop()
        prompt = "%s %s %s? %s " % (
            self.style.yellow("approve"), self.style.bold(name), format_args(call, 200),
            self.style.dim("[y]es / [n]o / [a]lways"))
        try:
            answer = self.ask(prompt).strip().lower()
        except EOFError:  # no human on the other end: fail closed
            return False
        # Ctrl-C is not caught: it interrupts the whole run, as anywhere else.
        if answer in ("a", "always"):
            self.always.add(name)
        approved = answer in ("y", "yes", "a", "always")
        if approved:
            self.spinner.start("working")
        return approved
