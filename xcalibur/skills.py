"""Day 4 — skills: know-how the agent loads only when it needs it.

Concept taught: progressive disclosure. Every skill costs one catalog line in
the system prompt (its name and description); the full instructions are read
only when the model asks for them with the use_skill tool. A project can carry
dozens of skills without paying for them on every turn.

A skill is a folder, ``skills/<name>/SKILL.md``, whose optional front matter
holds a ``description:`` line:

    ---
    description: How to cut a release of this package
    ---
    # Releasing
    ...

Design rules this file embodies:
  * The catalog is the only way in. ``read_skill`` looks a name up in the
    catalog rather than joining it onto a path, so a model-supplied name like
    "../../etc" cannot read outside the skills folder.
  * A miss lists what exists, so the model can correct itself in one step.
"""

import os

SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"


def catalog(workdir):
    """Return {name: {"description", "path"}} for every skill under ``workdir``."""
    base = os.path.join(workdir, SKILLS_DIR)
    if not os.path.isdir(base):
        return {}
    found = {}
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, SKILL_FILE)
        if os.path.isfile(path):
            found[name] = {"description": _description(path), "path": path}
    return found


def catalog_prompt(workdir):
    """Return the system-prompt section listing skills, or "" if none exist."""
    skills = catalog(workdir)
    if not skills:
        return ""
    lines = ["Skills available (load one with the use_skill tool when relevant):"]
    for name, info in skills.items():
        lines.append("- %s: %s" % (name, info["description"]) if info["description"]
                     else "- %s" % name)
    return "\n".join(lines)


def read_skill(workdir, name):
    """Return the full SKILL.md text for ``name``, or an error naming the options."""
    skills = catalog(workdir)
    info = skills.get(str(name).strip())
    if info is None:
        return "ERROR: no skill named %s. Available: %s" % (
            name, ", ".join(skills) or "(none)")
    with open(info["path"], encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _description(path):
    """Read the ``description:`` line from a SKILL.md front matter, else ""."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":  # end of front matter
            break
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "description":
            return value.strip().strip("'\"")
    return ""
