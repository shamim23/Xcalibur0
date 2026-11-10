"""Day 2 demo — an agent that builds: real tools, one policy gate, one loop.

Concept taught: yesterday's loop needs no changes to become a coding agent.
``core_tools`` supplies the hands, ``Policy.check`` plugs into the
``before_tool`` socket as the conscience, and ``run_loop`` stays exactly as it
was. The model writes files, runs them, and reads the output back to check
its own work.

Design rules on show: the agent is confined to a fresh scratch directory, so
nothing it writes touches the repo; the policy runs in "yolo" mode, which
shows that the deny list still holds when everything else is allowed.

Run from the repo root:  python3 -m demos.day2_build [task...]
"""

import sys
import tempfile

from odysseus import loop, provider
from odysseus.security import Policy
from odysseus.tools import core_tools

TASK = ("Create fib.py with an iterative fib(n), a __main__ printing fib(30), "
        "run it and confirm the output is 832040")

SYSTEM = """You are a careful coding agent working in the directory {workdir}.
Use the tools to act; do not describe what you would do. After changing code,
run it and check the output before you claim success. If a tool result says
BLOCKED or ERROR, do not retry the same thing: explain plainly what happened.
Decline destructive requests politely. Finish with a short summary."""


def on_event(kind, payload):
    """Print the transcript as it happens, clipping long tool output."""
    if kind == "assistant":
        if payload["text"]:
            print("assistant: %s" % payload["text"])
        for call in payload["tool_calls"]:
            print("assistant -> %s(%s)" % (call["name"], call["args"]))
    elif kind == "tool_end":
        result = str(payload["result"])
        if len(result) > 600:
            result = result[:600] + " ... [%d chars]" % len(result)
        print("tool result: %s" % result)


def main(task=None, workdir=None):
    """Run ``task`` in ``workdir`` (a new scratch directory by default)."""
    task = task or " ".join(sys.argv[1:]) or TASK
    workdir = workdir or tempfile.mkdtemp(prefix="odysseus-day2-")
    policy = Policy("yolo")
    tools = {t.name: t for t in core_tools(workdir)}
    print("workdir: %s" % workdir)
    print("user: %s" % task)
    answer = loop.run_loop(
        model=provider.DEFAULT_MODEL,
        system=SYSTEM.format(workdir=workdir),
        messages=[{"role": "user", "text": task}],
        tools=tools,
        on_event=on_event,
        before_tool=policy.check,
    )
    print("\nfinal: %s" % answer)
    return answer


if __name__ == "__main__":
    main()
