"""Day 1 demo — the smallest complete agent: one tool, one loop, one answer.

Concept taught: what the loop actually needs from a "tool" is tiny. Any object
with a ``.spec`` (the schema the model reads) and a ``.run`` (the callable the
loop invokes with keyword arguments) will do -- no registry, no decorators, no
base class. Day 2 automates building these from plain functions; today one is
written by hand so the shape is visible.

Design rules on show: the parameter is typed "string" because models emit JSON
strings for numbers often enough that the tool must coerce rather than trust,
and the demo supplies its own ``on_event`` and ``before_tool`` -- the loop owns
control flow, the caller owns policy.

Run from the repo root:  python3 -m demos.day1_dice
"""

import random
import sys

from odysseus import loop, provider

TASK = "Roll 3 dice and tell me whether the total beats 10"


class RollDice:
    """A hand-written tool: schema for the model, callable for the loop."""

    spec = {
        "schema": {
            "name": "roll_dice",
            "description": "Roll count six-sided dice",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "string", "description": "How many dice"},
                },
                "required": ["count"],
            },
        }
    }

    def run(self, count):
        """Roll ``count`` six-sided dice and return the rolls."""
        # The schema says string, so coerce here instead of trusting the model.
        rolls = [random.randint(1, 6) for _ in range(int(count))]
        return "rolls=%s total=%d" % (rolls, sum(rolls))


def on_event(kind, payload):
    """Print the transcript as it happens."""
    if kind == "assistant":
        if payload["text"]:
            print("assistant: %s" % payload["text"])
        for call in payload["tool_calls"]:
            print("assistant -> tool call: %s(%s)" % (call["name"], call["args"]))
    elif kind == "tool_start":
        print("tool start: %s" % payload["name"])
    elif kind == "tool_end":
        print("tool result: %s" % payload["result"])


def before_tool(call):
    """Allow everything today; day 2 replaces this with a real policy."""
    return None


def main():
    """Run the dice task and print the final answer."""
    task = " ".join(sys.argv[1:]) or TASK
    messages = [{"role": "user", "text": task}]
    print("user: %s" % task)
    answer = loop.run_loop(
        model=provider.DEFAULT_MODEL,
        system="You are a concise assistant. Use tools when they help.",
        messages=messages,
        tools={"roll_dice": RollDice()},
        on_event=on_event,
        before_tool=before_tool,
    )
    print("\nfinal: %s" % answer)


if __name__ == "__main__":
    main()
