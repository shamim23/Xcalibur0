"""Day 2 — tools: plain Python functions turned into things a model can call.

Concept taught: a tool is a name, a schema the model reads, and a callable the
loop runs. The ``tool`` decorator derives the schema from the function's own
signature, so the description lives next to the code it describes and the two
cannot drift apart.

Design rules this file embodies:
  * Every parameter is typed "string". Models emit numbers as strings often
    enough that a tool must coerce what it receives rather than trust a type.
  * Every path goes through one ``resolve``. The working directory is the
    agent's whole world; a path that escapes it raises PermissionError, which
    the loop turns into a tool result the model can read.
  * Recoverable mistakes ("snippet not found") are returned as text, not
    raised, and every output is capped: it all lands in the next prompt.
"""

import fnmatch
import inspect
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
MAX_READ_LINES = 4000
MAX_BASH_CHARS = 12000
MAX_LIST_ENTRIES = 500
MAX_GREP_HITS = 200
MAX_GREP_LINE = 200


@dataclass
class Tool:
    """A callable the loop can run, plus the schema the model sees."""

    name: str
    spec: dict
    run: Callable


def tool(description, **params):
    """Turn ``fn`` into a Tool; ``params`` maps argument -> description text."""
    def wrap(fn):
        signature = inspect.signature(fn).parameters
        properties = {name: {"type": "string", "description": params.get(name, name)}
                      for name in signature}
        required = [name for name, param in signature.items()
                    if param.default is inspect.Parameter.empty]
        spec = {"schema": {"name": fn.__name__, "description": description,
                           "parameters": {"type": "object", "properties": properties,
                                          "required": required}}}
        return Tool(name=fn.__name__, spec=spec, run=fn)  # defaults -> optional
    return wrap


def core_tools(workdir):
    """Return the six core tools, all confined to the real path of ``workdir``."""
    root = os.path.realpath(workdir)

    def resolve(path):
        """Map a model-supplied path to a real path inside the working directory."""
        # realpath first, check second: this defeats both "../" and symlinks
        # that point outside, since the check sees where the path really lands.
        real = os.path.realpath(os.path.join(root, path))
        if os.path.commonpath([root, real]) != root:
            raise PermissionError(f"{path!r} escapes the working directory")
        return real

    def matches(rel, pattern):
        """Match a glob against the relative path or the basename."""
        # fnmatch's "*" already crosses "/", but "**/" demands a slash, so
        # "**/*" would miss top-level files without the stripped retry.
        stripped = pattern[3:] if pattern.startswith("**/") else pattern
        return any(fnmatch.fnmatch(target, glob)
                   for target in (rel, os.path.basename(rel))
                   for glob in (pattern, stripped))

    def walk(pattern):
        """Yield sorted relative paths of files matching ``pattern``."""
        found = []
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for name in files:
                rel = os.path.relpath(os.path.join(current, name), root)
                if matches(rel, pattern):
                    found.append(rel)
        return sorted(found)

    @tool("Read a text file; lines are returned numbered as N<TAB>line.",
          path="File path relative to the working directory")
    def read_file(path):
        with open(resolve(path), encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        shown = [f"{n}\t{line}" for n, line in enumerate(lines[:MAX_READ_LINES], 1)]
        if len(lines) > MAX_READ_LINES:
            shown.append(f"... (truncated: file has {len(lines)} lines)")
        return "\n".join(shown)

    @tool("Create or overwrite a file, creating parent directories as needed.",
          path="File path relative to the working directory",
          content="The complete new file contents")
    def write_file(path, content):
        real = resolve(path)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "w", encoding="utf-8") as handle:
            handle.write(content)
        return f"Wrote {len(content)} chars to {path}"

    @tool("Replace one exact snippet in a file. The snippet must occur exactly once.",
          path="File path relative to the working directory",
          old="The exact existing text to replace, copied from the file",
          new="The replacement text")
    def edit_file(path, old, new):
        real = resolve(path)
        with open(real, encoding="utf-8") as handle:
            text = handle.read()
        # Uniqueness rule: an ambiguous edit is refused rather than guessed at,
        # because replacing the wrong occurrence silently corrupts the file.
        count = text.count(old)
        if count == 0:
            return "ERROR: snippet not found — read the file and copy it exactly"
        if count > 1:
            return (f"ERROR: snippet appears {count} times — include more context "
                    "to make it unique")
        with open(real, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))
        return f"Edited {path}"

    @tool("Run a shell command in the working directory; returns stdout and stderr.",
          command="The shell command to run",
          timeout="Seconds before the command is killed (default 120)")
    def bash(command, timeout="120"):
        seconds = float(timeout)
        try:
            done = subprocess.run(command, shell=True, cwd=root, capture_output=True,
                                  text=True, errors="replace", timeout=seconds)
        except subprocess.TimeoutExpired:
            return f"ERROR: timed out after {timeout}s"
        output = (done.stdout or "") + (done.stderr or "")
        if len(output) > MAX_BASH_CHARS:
            # Keep both ends: the start shows what ran, the end shows how it died.
            half, cut = MAX_BASH_CHARS // 2, len(output) - MAX_BASH_CHARS
            output = f"{output[:half]}\n... [{cut} chars truncated] ...\n{output[-half:]}"
        return output or f"(exit {done.returncode}, no output)"

    @tool("List files under the working directory matching a glob.",
          pattern="Glob matched against relative path or basename (default **/*)")
    def list_files(pattern="**/*"):
        found = walk(pattern)
        lines = found[:MAX_LIST_ENTRIES]  # sorted, then capped
        if len(found) > MAX_LIST_ENTRIES:
            lines.append(f"... and {len(found) - MAX_LIST_ENTRIES} more")
        return "\n".join(lines) or "(no files match)"

    @tool("Search file contents with a regular expression; returns path:lineno: text.",
          regex="Python regular expression to search for",
          pattern="Glob restricting which files are searched (default *)")
    def grep(regex, pattern="*"):
        compiled, hits = re.compile(regex), []
        for rel in walk(pattern):
            try:
                with open(os.path.join(root, rel), encoding="utf-8") as handle:
                    for number, line in enumerate(handle, 1):
                        if compiled.search(line):
                            hits.append(f"{rel}:{number}: {line.rstrip()[:MAX_GREP_LINE]}")
                            if len(hits) >= MAX_GREP_HITS:
                                return "\n".join(hits) + f"\n... capped at {MAX_GREP_HITS} hits"
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable: not searchable text
        return "\n".join(hits) or "(no matches)"

    return [read_file, write_file, edit_file, bash, list_files, grep]
