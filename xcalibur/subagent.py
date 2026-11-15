"""Day 4 — sub-agents: delegate a task to a fresh context.

Concept taught: a sub-agent is just another harness, exposed to its parent as a
tool. The child starts with a clean transcript, works the task with its own
tools, and hands back only its final report, so the parent's context grows by
one tool result instead of every file the child read along the way.

Design rules this file embodies:
  * The child cannot see the parent's conversation. The task string is its
    whole brief, and the tool description tells the model so.
  * Recursion is bounded. Each child is built one level deeper, and at the
    depth limit the tool refuses and tells the model to do the work itself.
  * This file knows nothing about how a harness is built. ``make_harness`` is
    injected, and anything with a ``run(task) -> str`` method will do.
"""

from .tools import tool


def subagent_tool(make_harness, depth=0, max_depth=2):
    """Return the spawn_agent Tool for a harness running at ``depth``.

    ``make_harness(depth)`` must return an object whose ``run(task)`` drives a
    fresh agent to completion and returns its final text.
    """
    @tool("Delegate a self-contained task to a fresh sub-agent with its own "
          "clean context. The sub-agent cannot see this conversation, so the "
          "task must include every detail it needs: goal, file paths, "
          "constraints and what to report back. Returns the sub-agent's final "
          "report.",
          task="The complete, self-contained task for the sub-agent")
    def spawn_agent(task):
        if depth >= max_depth:
            return "ERROR: sub-agent depth limit reached; do this task yourself"
        child = make_harness(depth + 1)
        return child.run(task)

    return spawn_agent
