"""Day 4 — memory: what the agent knows before the first turn.

Concept taught: the system prompt is assembled, not hard-coded. A fixed base
says how XCALIBUR behaves; the harness adds where it is running; and a plain
markdown file in the project, XCALIBUR.md, carries what earlier sessions
learned. Memory that outlives a run is just a file the next run reads.

Design rules this file embodies:
  * Memory is human-readable and human-editable. A note is one markdown bullet,
    so the user can review, fix or delete what the agent remembers.
  * The working directory is reported as its real path, so the model never
    guesses at where "here" is.
  * Missing memory is not an error: a project without XCALIBUR.md just gets
    the base prompt.
"""

import os
import platform

MEMORY_FILE = "XCALIBUR.md"

BASE_PROMPT = (
    "You are XCALIBUR, a small, sharp coding agent. You work inside one "
    "directory using only the tools provided.\n"
    "- Act, don't narrate: call tools instead of describing what you would do.\n"
    "- Inspect before assuming: read files and list directories before you "
    "change them.\n"
    "- Prefer edit_file for small changes; rewrite a whole file only when most "
    "of it changes.\n"
    "- Verify after building: run the code or re-read the file to confirm it "
    "works.\n"
    "- Never repeat a failing call unchanged; read the error and change your "
    "approach.\n"
    "- When the task is complete, reply with a short summary and stop calling "
    "tools.")


def build_system_prompt(workdir, extra=""):
    """Return the base prompt + environment + project memory + ``extra``."""
    root = os.path.realpath(workdir)
    parts = [BASE_PROMPT,
             "Platform: %s. Working directory: %s" % (platform.system(), root)]
    path = os.path.join(root, MEMORY_FILE)
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as handle:
            notes = handle.read().strip()
        if notes:
            parts.append("Project memory (%s):\n%s" % (MEMORY_FILE, notes))
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def remember(workdir, note):
    """Append ``note`` as a bullet to XCALIBUR.md; return a confirmation."""
    # One line per note: a newline inside the note would break the bullet list.
    line = " ".join(str(note).split())
    with open(os.path.join(workdir, MEMORY_FILE), "a", encoding="utf-8") as handle:
        handle.write("- %s\n" % line)
    return "Remembered in %s" % MEMORY_FILE
