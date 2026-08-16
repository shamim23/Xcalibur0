"""Day 1 — the agent loop: call, run tools, feed results back, repeat.

Concept taught: an agent is a while-loop around one model call. Everything
else in Odysseus is a policy hung off this loop's seams.

Design rules this file embodies:
  * Vendor-agnostic. It speaks only the neutral message format and asks the
    provider for a reply; nothing here knows what an API looks like.
  * The loop never crashes because a tool did. A failure becomes a tool result
    the model can read and route around.
  * Every decision point is a hook, not a branch: ``before_tool`` gates
    execution (day 2 hangs security on it), ``before_turn`` rewrites the
    message list (day 3 hangs compaction on it), ``on_event`` reports.
"""

from . import provider


def run_loop(model, system, messages, tools, on_event, before_tool,
             max_turns=80, before_turn=None):
    """Drive the model until it answers without calling a tool; return the text.

    ``tools`` maps name -> Tool, where a Tool exposes ``.spec`` (a
    ``{"schema": ...}`` dict) and ``.run`` (a callable taking keyword args).
    ``messages`` is mutated in place, so the caller keeps the full transcript.
    """
    specs = [tool.spec for tool in tools.values()]
    for _ in range(max_turns):
        # before_turn replaces the list in place, so the caller's transcript
        # object stays the same one the loop keeps appending to.
        if before_turn is not None:
            messages[:] = before_turn(messages)

        reply = provider.complete(model, system, messages, specs)
        messages.append({
            "role": "assistant",
            "text": reply["text"],
            "tool_calls": reply["tool_calls"],
        })
        on_event("assistant", reply)

        if not reply["tool_calls"]:
            return reply["text"]

        for call in reply["tool_calls"]:
            on_event("tool_start", call)
            result = _run_tool(tools, call, before_tool)
            messages.append({"role": "tool", "name": call["name"], "text": str(result)})
            on_event("tool_end", {"call": call, "result": result})

    # Out of turns: one last call with no tools, so the model must answer.
    messages.append({"role": "user", "text": "Turn limit reached; wrap up now."})
    final = provider.complete(model, system, messages, [])
    messages.append({"role": "assistant", "text": final["text"], "tool_calls": []})
    on_event("assistant", final)
    return final["text"]


def _run_tool(tools, call, before_tool):
    """Gate, dispatch, and execute one call; every failure returns as text."""
    reason = before_tool(call)
    if reason:
        return "BLOCKED: " + reason
    tool = tools.get(call["name"])
    if tool is None:
        return "ERROR: unknown tool " + str(call["name"])
    try:
        return tool.run(**(call.get("args") or {}))
    except Exception as error:  # a broken tool is data, not a crash
        return "ERROR: %s: %s" % (type(error).__name__, error)
