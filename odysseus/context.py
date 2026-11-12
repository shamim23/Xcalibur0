"""Day 3 — the context engine: keep a long run inside a fixed token budget.

Concept taught: the transcript is the agent's working memory, and it grows
without bound. Compaction trades the old turns for a model-written summary and
keeps the recent turns verbatim, so the agent remembers what it did without
re-reading every byte of it. It plugs into the loop's ``before_turn`` socket.

Design rules this file embodies:
  * Estimate, don't tokenize. Four characters per token is crude but vendor
    neutral, and a budget only needs to be roughly right.
  * Recent turns are sacred; only the old slice is summarized.
  * The kept slice never opens with a tool result whose call was summarized
    away, since a result without its call is malformed on every provider.
"""

from . import provider

CHARS_PER_TOKEN = 4
KEEP_RECENT = 6
CLIP_CHARS = 2000

SUMMARY_SYSTEM = (
    "You compress agent transcripts. Preserve: the original task, every file "
    "created or edited and its purpose, key decisions, unresolved errors, and "
    "what remains to be done. Be dense and factual.")


def estimate_tokens(messages):
    """Roughly count the tokens in ``messages``."""
    return sum(len(str(message)) for message in messages) // CHARS_PER_TOKEN


def compact(model, messages, budget_tokens):
    """Return ``messages`` unchanged if within budget, else summary + recent."""
    if (estimate_tokens(messages) <= budget_tokens
            or len(messages) <= KEEP_RECENT + 1):
        return messages
    old, recent = messages[:-KEEP_RECENT], messages[-KEEP_RECENT:]
    # Orphan rule: tool results whose calls fall in the old slice move over
    # with them, so they are summarized rather than replayed without a call.
    while recent and recent[0].get("role") == "tool":
        old.append(recent.pop(0))
    summary = provider.complete(
        model, SUMMARY_SYSTEM,
        [{"role": "user", "text": _render(old)}], [])["text"]
    return [{"role": "user", "text": "[Conversation so far, compacted]\n" + summary}] + recent


def _render(messages):
    """Flatten messages into a plain transcript for the summarizer."""
    lines = []
    for message in messages:
        head = message.get("role", "?")
        if message.get("name"):
            head += " (%s)" % message["name"]
        lines.append("%s: %s" % (head, (message.get("text") or "")[:CLIP_CHARS]))
        # Name each call with clipped args: the path is what the summary needs.
        for call in message.get("tool_calls") or []:
            lines.append("  called %s(%s)" % (call.get("name"), str(call.get("args"))[:200]))
    return "\n".join(lines)
