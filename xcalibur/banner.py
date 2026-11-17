"""Day 5 — banner: the sword is drawn before the session starts.

Concept taught: a terminal animation is just the same block of lines printed
again and again. Each frame moves the cursor back up over the previous one and
overwrites it line by line, so the user's scrollback is never cleared.

Design rules this file embodies:
  * A frame is a list of rows; a row is a list of ``(char, style)`` cells.
    Colour is applied last, so the plain-text path is the same code with the
    styles dropped (``static(color=False)`` emits zero escapes).
  * The cursor is hidden while frames play and restored in ``finally``.
  * Ctrl-C does not crash the banner: it skips to the final frame.
  * A terminal too narrow for the art gets a one-line wordmark instead.
"""

import shutil
import sys
import time

_GLYPHS = {
    "X": ["██╗  ██╗", "╚██╗██╔╝", " ╚███╔╝ ", " ██╔██╗ ", "██╔╝ ██╗", "╚═╝  ╚═╝"],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "B": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██████╔╝", "╚═════╝ "],
    "U": ["██╗   ██╗", "██║   ██║", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
}
_NAME = "XCALIBUR"
_WORD = ["".join(_GLYPHS[ch][row] for ch in _NAME) for row in range(6)]
_ART_W = max(len(row) for row in _WORD)            # 60 columns
_INDENT = 2
_MAX_W = 80

_HILT = "◆═══╬"
_TIP = "╾─"
_SWORD = _HILT + "━" * (_ART_W - len(_HILT) - len(_TIP)) + _TIP

# SGR parameter strings (256-colour).
_STEEL = ["38;5;255", "38;5;254", "38;5;252", "38;5;250", "38;5;248", "38;5;246"]
_SHADOW = "38;5;240"
_BLADE = "38;5;250"
_TIP_C = "38;5;245"
_GOLD = "38;5;220"
_GOLD_DIM = "38;5;178"
_GLINT = "1;38;5;231"
_HALO = "38;5;229"
_KEY = "38;5;244"
_VAL = "38;5;252"

_HIDE, _SHOW = "\033[?25l", "\033[?25h"
_CLEAR_LINE = "\r\033[2K"


def _paint(cells, color):
    """Turn ``[(char, style)]`` into a string, one escape per run of a style."""
    text = "".join(ch for ch, _ in cells).rstrip()
    if not color:
        return text
    out, current = [], None
    for ch, style in cells[:len(text)]:
        if ch == " ":
            style = current                        # spaces never switch colour
        if style != current:
            out.append("\033[0m" if style is None else f"\033[0;{style}m")
            current = style
        out.append(ch)
    if current is not None:
        out.append("\033[0m")
    return "".join(out)


def _pad(cells):
    return [(" ", None)] * _INDENT + cells


def _word_row(row, edge=None):
    """One wordmark row. ``edge`` is the reveal column (None = fully shown)."""
    cells = []
    for col, ch in enumerate(_WORD[row]):
        if ch == " " or (edge is not None and col > edge - row):
            cells.append((" ", None))
            continue
        style = _STEEL[row] if ch == "█" else _SHADOW
        if edge is not None:
            behind = (edge - row) - col            # distance behind the shine
            if behind < 3:
                style = _GLINT
            elif behind < 6:
                style = _HALO
        cells.append((ch, style))
    return _pad(cells)


def _sword_row(length=None, glint=None):
    """The blade. ``length`` = columns drawn so far, ``glint`` = shine column."""
    shown = len(_SWORD) if length is None else length
    cells = []
    for col, ch in enumerate(_SWORD):
        if col >= shown:
            break
        if col < len(_HILT):
            style = _GOLD if ch in "◆╬" else _GOLD_DIM
        elif col >= len(_SWORD) - len(_TIP):
            style = _TIP_C
        else:
            style = _BLADE
        if length is not None and col >= len(_HILT) and col >= shown - 2:
            style = _GLINT                         # hot leading edge
        if glint is not None and col >= len(_HILT) and abs(col - glint) <= 1:
            style = _GLINT
        cells.append((ch, style))
    return _pad(cells)


def _info_rows(width, version, model, mode, workdir):
    """The info line as cell rows, shortened (or split in two) to fit."""
    def build(fields):
        cells = []
        for key, value in fields:
            if cells:
                cells += [(c, _KEY) for c in " · "]
            if key:
                cells += [(c, _KEY) for c in key + " "]
            cells += [(c, _VAL) for c in value]
        return cells

    def clip(cells):
        if len(cells) > width:
            cells = cells[:max(width - 1, 0)] + [("…", _KEY)]
        return cells

    def squeeze(path, room):
        """Keep the tail of ``path`` in ``room`` columns behind a leading …."""
        if len(path) <= room:
            return path
        return "…" + path[len(path) - (room - 1):] if room > 1 else "…"

    head = []
    if version:
        head.append(("", "v" + str(version).lstrip("v")))
    if model:
        head.append(("model", str(model)))
    if mode:
        head.append(("mode", str(mode)))
    workdir = str(workdir) if workdir else ""
    if not workdir:
        return [clip(build(head))] if head else []

    used = len(build(head + [("jail", "")]))
    room = width - used
    if room >= min(len(workdir), 12):
        return [build(head + [("jail", squeeze(workdir, room))])]
    # Too tight for one line: the jail path gets a line of its own.
    rows = [clip(build(head))] if head else []
    rows.append(clip(build([("jail", squeeze(workdir, width - len("jail ")))])))
    return rows


def _layout(out_width):
    """Return (compact?, text width, indent) for a terminal ``out_width`` wide."""
    cols = max(out_width, 1)
    compact = cols < _INDENT + _ART_W + 1
    indent = _INDENT if cols > 20 else 0
    return compact, max(min(cols, _MAX_W) - indent - 1, 1), indent


def _compact_title(width, shown=len(_NAME)):
    """One-line wordmark; ``shown`` letters are revealed, the newest glinting."""
    small = "◆═╬━━━━━╾"
    spaced = width >= len(small) + 2 + len(_NAME) * 2 - 1
    cells = []
    if width >= len(small) + 2 + len(_NAME):
        for ch in small:
            style = _GOLD if ch in "◆╬" else _GOLD_DIM if ch == "═" else _BLADE
            cells.append((ch, style))
        cells += [(" ", None)] * 2
    for i, ch in enumerate(_NAME[:shown]):
        last = i == shown - 1 and shown < len(_NAME)
        cells.append((ch, _GLINT if last else "1;" + _STEEL[1]))
        if spaced and i < len(_NAME) - 1:
            cells.append((" ", None))
    return cells


def _final_rows(compact, width, indent, info, color):
    """Every row of the finished banner, already painted."""
    gap = [(" ", None)] * indent
    info_rows = [_paint(gap + row, color) for row in _info_rows(width, **info)]
    if compact:
        return [""] + [_paint(gap + _compact_title(width), color)] + info_rows
    rows = [""] + [_paint(_word_row(r), color) for r in range(6)]
    rows += [_paint(_sword_row(), color), ""] + info_rows
    return rows


def _size():
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines


def static(out=None, *, version="", model="", mode="", workdir="", color=True):
    """Print the final banner frame only. ``color=False`` emits no escapes."""
    out = sys.stdout if out is None else out
    info = dict(version=version, model=model, mode=mode, workdir=workdir)
    compact, width, indent = _layout(_size()[0])
    out.write("\n".join(_final_rows(compact, width, indent, info, color)) + "\n")
    out.flush()


def _ease(step, steps, total):
    """Ease-out position in ``0..total`` for frame ``step`` of ``steps``."""
    t = (step + 1) / steps
    return round(total * (1 - (1 - t) ** 2))


def _full_frames():
    """Yield (rows, delay) for the full-size animation, final frame excluded."""
    blank = [""] * 6

    def frame(word, sword):
        return [""] + word + [sword, "", ""]

    # 1. The blade is drawn out from the hilt.
    steps = 9
    for i in range(steps):
        length = max(_ease(i, steps, len(_SWORD)), len(_HILT))
        yield frame(blank, _paint(_sword_row(length=length), True)), 0.03
    # 2. A slanted shine sweeps left to right, leaving the wordmark behind it.
    steps, sword = 14, _paint(_sword_row(), True)
    for i in range(steps):
        edge = round((i + 1) / steps * (_ART_W + 12))
        yield frame([_paint(_word_row(r, edge), True) for r in range(6)], sword), 0.032
    # 3. One glint runs down the blade.
    steps, word = 9, [_paint(_word_row(r), True) for r in range(6)]
    for i in range(steps):
        col = len(_HILT) + round(i / (steps - 1) * (len(_SWORD) - len(_HILT)))
        yield frame(word, _paint(_sword_row(glint=col), True)), 0.024


def play(out=None, *, version="", model="", mode="", workdir=""):
    """Animate the banner (about one second), ending on the static frame."""
    out = sys.stdout if out is None else out
    info = dict(version=version, model=model, mode=mode, workdir=workdir)
    isatty = getattr(out, "isatty", None)
    if not (isatty and isatty()):
        return static(out, color=False, **info)    # nothing to animate on a pipe

    cols, lines = _size()
    compact, width, indent = _layout(cols)
    final = _final_rows(compact, width, indent, info, True)
    if lines <= len(final) + 1:
        return static(out, color=True, **info)     # frame would scroll off-screen

    height = len(final)
    drawn = False

    def draw(rows):
        nonlocal drawn
        rows = rows + [""] * (height - len(rows))
        head = f"\033[{height}A" if drawn else ""
        drawn = True
        out.write(head + "".join(_CLEAR_LINE + row + "\n" for row in rows))
        out.flush()

    try:
        out.write(_HIDE)
        try:
            if compact:
                gap = [(" ", None)] * indent
                for shown in range(1, len(_NAME) + 1):
                    draw(["", _paint(gap + _compact_title(width, shown), True)])
                    time.sleep(0.05)
            else:
                for rows, delay in _full_frames():
                    draw(rows)
                    time.sleep(delay)
        except KeyboardInterrupt:
            pass                                   # skip straight to the end
        draw(final)
        out.write(_CLEAR_LINE)                     # wipe any echoed ^C
    except KeyboardInterrupt:
        pass
    finally:
        out.write(_SHOW)
        out.flush()
