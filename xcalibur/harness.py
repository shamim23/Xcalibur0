"""Day 5 — the harness: every earlier piece wired into one object.

Concept taught: a harness is composition, not new machinery. The loop (day 1)
runs tools (day 2) behind a policy (day 2), compaction (day 3) keeps the run in
budget, memory and skills (day 4) shape the system prompt, sub-agents (day 4)
are one more tool, and the session log (day 4) records every message so a
killed run can pick up where it stopped.

Design rules this file embodies:
  * Record as it lands. Each message is written to the session log when the
    loop reports it, never in a batch at the end, so a kill -9 loses at most
    the call in flight.
  * The log is the full history. Compaction shrinks the in-memory list, but
    the log keeps every original message; only messages added after a
    compaction are appended.
  * Children are ephemeral. A sub-agent shares the workdir and policy but
    writes no session log, so --resume always finds the parent's session.
"""

import os

from . import context, loop, memory, provider, session, skills, subagent
from .security import Policy
from .tools import core_tools, tool


class Harness:
    """One agent: tools, policy, prompt, transcript and session log."""

    def __init__(self, workdir=".", model=None, policy=None, extra_tools=None,
                 system_extra="", on_event=None, budget_tokens=600_000,
                 max_turns=120, session_path=None, enable_subagents=True,
                 persist=True, _depth=0):
        self.workdir = os.path.realpath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self.model = model or os.environ.get("XCALIBUR_MODEL") or provider.DEFAULT_MODEL
        self.policy = policy or Policy("yolo")
        self.on_event = on_event or (lambda kind, data: None)
        self.budget_tokens = budget_tokens
        self.max_turns = max_turns
        self.session_path = session_path
        self.persist = persist
        self.depth = _depth
        self.messages = []
        self._recorded = 0

        self.tools = {t.name: t for t in core_tools(self.workdir)}
        root = self.workdir

        @tool("Save a short fact about this project to XCALIBUR.md so future "
              "sessions know it (commands, conventions, decisions).",
              note="One self-contained fact, a single line")
        def remember(note):
            return memory.remember(root, note)
        self.tools[remember.name] = remember

        if skills.catalog(root):
            @tool("Load the full instructions of a skill listed in the system prompt.",
                  name="The skill name exactly as listed")
            def use_skill(name):
                return skills.read_skill(root, name)
            self.tools[use_skill.name] = use_skill

        if enable_subagents:
            def make_child(depth):
                # Same jail, policy and tools; no log, so it never hijacks --resume.
                return Harness(root, model=self.model, policy=self.policy,
                               extra_tools=extra_tools, system_extra=system_extra,
                               on_event=self.on_event, budget_tokens=budget_tokens,
                               max_turns=max_turns, enable_subagents=True,
                               persist=False, _depth=depth)
            spawn = subagent.subagent_tool(make_child, depth=_depth)
            self.tools[spawn.name] = spawn

        if extra_tools:
            items = extra_tools.values() if isinstance(extra_tools, dict) else extra_tools
            self.tools.update({t.name: t for t in items})

        self.system_extra = system_extra
        self.refresh_system()

    def refresh_system(self):
        """Rebuild the system prompt from memory and skills (after an edit)."""
        extra = "\n\n".join(part for part in (skills.catalog_prompt(self.workdir),
                                               self.system_extra) if part)
        self.system = memory.build_system_prompt(self.workdir, extra)

    def reset(self):
        """Drop the transcript; the next run starts a new session log."""
        self.messages = []
        self.session_path = None
        self._recorded = 0

    def compact(self):
        """Force a compaction now; return (messages before, messages after)."""
        before = len(self.messages)
        self.messages[:] = context.compact(self.model, self.messages, 0)
        self._recorded = min(self._recorded, len(self.messages))
        return before, len(self.messages)

    def resume(self, path=None):
        """Load a session log (the latest by default); return True if it had messages."""
        path = path or session.latest(self.workdir)
        if not path or not os.path.isfile(path):
            return False
        self.messages = session.load(path)
        self.session_path = path
        # Rewrite the log as loaded: this drops a torn tail line (appending
        # after one would corrupt the next line too) and saves any
        # "interrupted" results the repair added.
        if self.persist:
            temp = path + ".tmp"
            with open(temp, "w", encoding="utf-8"):
                pass
            for message in self.messages:
                session.append(temp, message)
            os.replace(temp, path)
        self._recorded = len(self.messages)
        return bool(self.messages)

    def run(self, task):
        """Run ``task`` to completion and return the agent's final text."""
        if self.persist and not self.session_path:
            self.session_path = session.new_session(self.workdir, task[:32])
        # A run cut off by Ctrl-C can leave calls without results; answer them
        # before the next user message so the transcript stays valid.
        session._repair(self.messages)
        self.messages.append({"role": "user", "text": task})
        self._record()

        def on_event(kind, data):
            self._record()
            self.on_event(kind, data)

        def before_turn(messages):
            compacted = context.compact(self.model, messages, self.budget_tokens)
            # The log already holds the originals; only append what comes after.
            self._recorded = min(self._recorded, len(compacted))
            return compacted

        try:
            return loop.run_loop(self.model, self.system, self.messages, self.tools,
                                 on_event, self.policy.check,
                                 max_turns=self.max_turns, before_turn=before_turn)
        finally:
            self._record()

    def _record(self):
        """Append every message not yet in the session log."""
        if self.persist and self.session_path:
            for message in self.messages[self._recorded:]:
                session.append(self.session_path, message)
        self._recorded = len(self.messages)
