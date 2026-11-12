"""Day 3 demo — a long task run on a deliberately tiny context budget.

Concept taught: compaction is invisible to the loop. ``run_loop`` already calls
``before_turn`` before every model call; handing it ``compact`` is the whole
integration. The budget here is set absurdly low so compaction fires within a
few turns and you can watch the transcript shrink while the work carries on.

Design rules on show: the demo reuses day 2's tools, policy, and printer
unchanged, and it only observes compaction by wrapping it, never by
modifying the loop.

Run from the repo root:  python3 -m demos.day3_compact [task...]
"""

import sys
import tempfile

from odysseus import context, loop, provider
from odysseus.security import Policy
from odysseus.tools import core_tools
from demos.day2_build import SYSTEM, on_event

BUDGET_TOKENS = 1500
TASK = ("Create five files one.txt through five.txt, each with 20 lines of the "
        "word ping, one write_file at a time with a read back after each; then "
        "MANIFEST.md listing each file and its line count verified with wc -l")


def main(task=None, workdir=None, budget_tokens=BUDGET_TOKENS):
    """Run ``task`` with compaction on ``before_turn``; return (answer, fired)."""
    task = task or " ".join(sys.argv[1:]) or TASK
    workdir = workdir or tempfile.mkdtemp(prefix="odysseus-day3-")
    model = provider.DEFAULT_MODEL
    fired = []

    def before_turn(messages):
        """Compact, and report whenever the transcript actually shrank."""
        before = context.estimate_tokens(messages)
        result = context.compact(model, messages, budget_tokens)
        if result is not messages:
            fired.append((len(messages), len(result)))
            print("[compacted: %d messages ~%d tokens -> %d messages ~%d tokens]"
                  % (len(messages), before, len(result), context.estimate_tokens(result)))
        return result

    print("workdir: %s\nuser: %s" % (workdir, task))
    answer = loop.run_loop(
        model=model,
        system=SYSTEM.format(workdir=workdir),
        messages=[{"role": "user", "text": task}],
        tools={t.name: t for t in core_tools(workdir)},
        on_event=on_event,
        before_tool=Policy("yolo").check,
        before_turn=before_turn,
    )
    print("\nfinal: %s\ncompactions: %d" % (answer, len(fired)))
    return answer, fired


if __name__ == "__main__":
    main()
