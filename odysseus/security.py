"""Day 2 — security: one gate in front of every tool call.

Concept taught: safety is a policy hung on the loop's ``before_tool`` seam, not
logic scattered through the tools. ``Policy.check`` sees each call before it
runs and returns None to allow it or a reason string to block it; the loop
hands that reason back to the model as "BLOCKED: <reason>".

Design rules this file embodies:
  * The deny list beats every mode, yolo included. Some commands are never
    worth one keystroke of convenience.
  * Reading is always allowed; only writes and commands need permission.
  * Fail closed. With no approver, or an approver that says anything but a
    plain yes, the call is blocked.
"""

import re

READ_TOOLS = {"read_file", "list_files", "grep"}
MODES = ("read-only", "safe", "yolo")

# Checked with re.search against every bash command, in every mode.
DENY_PATTERNS = [
    # Recursive rm aimed at an absolute path, ~ or $HOME, whatever the flag
    # spelling (-rf, -fr, -r -f, --recursive). Absolute paths are included
    # because "/Users/me" is the home directory without the tilde.
    r"\brm(?=[^;&|]*\s-(?:[a-zA-Z]*[rR]|-recursive))[^;&|]*\s[\"']?(?:/|~|\$\{?HOME\b)",
    r"\bsudo\b",
    r"\bmkfs(?:\.\w+)?\b",
    r"\bdd\s+if=",
    r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
    r"\bgit\s+push\b[^;&|]*(?:--force\b|\s-f\b)",
    r">\s*/dev/sd[a-z]",
]


class Policy:
    """Decide, call by call, whether a tool may run."""

    def __init__(self, mode="safe", approver=None):
        """``approver(call, reason) -> bool`` is asked in safe mode."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self.approver = approver or (lambda call, reason: False)  # no human: no

    def check(self, call):
        """Return None to allow ``call`` or a reason string to block it."""
        name = call.get("name")
        # Order matters: the deny list runs before any allow rule can fire.
        if name == "bash":
            command = str((call.get("args") or {}).get("command", ""))
            for pattern in DENY_PATTERNS:
                if re.search(pattern, command):
                    return f"command refused by the deny list (matches {pattern!r})"
        if name in READ_TOOLS or self.mode == "yolo":
            return None
        if self.mode == "read-only":
            return f"{name} is not allowed in read-only mode"
        reason = f"{name} needs approval in safe mode"
        try:
            approved = self.approver(call, reason)
        except Exception:  # a broken approver must not become a yes
            approved = False
        return None if approved is True else f"{reason}; the user did not approve"
