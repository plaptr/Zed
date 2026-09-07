"""
splitjoin.py

mini.splitjoin from wish:

tasks.json:
[
    {
        "label": "splitjoin",
        "command": "python3",
        "args": [
            "-S",
            "~/.config/zed/splitjoin.py", // CHANGE THIS
            "$ZED_FILE",
            "$ZED_ROW",
            "$ZED_COLUMN",
        ],
        "reveal": "never",
        "use_new_terminal": false,
        "hide": "always",
        "allow_concurrent_runs": true,
    }
]

keymap.json:
[
    {
        "context": "vim_mode == normal || vim_mode == visual",
        "bindings": {
            "g s": [
                "action::Sequence",
                [
                    "workspace::SaveWithoutFormat",
                    ["task::Spawn", {"task_name": "splitjoin"}]
                ]
            ]
        },
    }
]

"workspace::SaveWithoutFormat" is useful since the script modifies the file on disk,
failing to save beforehand would result in mismatching versions between the editor and the file system
"""

import re
import sys
from math import gcd

BRACKET_PAIRS = [("(", ")"), ("[", "]"), ("{", "}")]
QUOTE_CHARS = ('"', "'")
SEPARATOR = ","


def read_file(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        content = f.read()

    is_using_crlf = "\r\n" in content
    if is_using_crlf:
        content = content.replace("\r\n", "\n")

    has_trailing_newline = content.endswith("\n")
    lines = content.split("\n")

    if has_trailing_newline:
        lines = lines[:-1]

    return lines, has_trailing_newline, is_using_crlf


def write_file(path, lines, has_trailing_newline, is_using_crlf):
    content = "\n".join(lines)

    if has_trailing_newline:
        content += "\n"

    if is_using_crlf:
        content = content.replace("\n", "\r\n")

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def get_indent(line):
    return re.match(r"^[ \t]*", line).group(0)  # pyright: ignore[reportOptionalMemberAccess]


def detect_indent_unit(lines):
    tab_count = 0
    space_widths = set()

    for line in lines:
        indent = get_indent(line)
        if not indent:
            continue
        if "\t" in indent:
            tab_count += 1
        else:
            space_widths.add(len(indent))

    if tab_count and tab_count >= len(space_widths):
        return "\t"

    if space_widths:
        widths = sorted(space_widths)
        unit = widths[0]
        for w in widths[1:]:
            unit = gcd(unit, w)
        return " " * (unit if unit > 0 else 2)

    return "  "  # fallback: 2 spaces


def build_flat(lines):
    parts = [line + "\n" for line in lines]
    flat = "".join(parts)
    line_starts = []
    cur = 0
    for p in parts:
        line_starts.append(cur)
        cur += len(p)
    return flat, line_starts


def pos_to_offset(line_starts, line, col):
    line = max(1, min(line, len(line_starts)))
    return line_starts[line - 1] + max(0, col - 1)


def offset_to_pos(line_starts, offset):
    line_idx = 0
    for i, start in enumerate(line_starts):
        if start <= offset:
            line_idx = i
        else:
            break
    col = offset - line_starts[line_idx] + 1
    return line_idx + 1, col


def find_bracket_matches(flat, open_ch, close_ch):
    matches = []
    stack = []
    for i, ch in enumerate(flat):
        if ch == open_ch:
            stack.append(i)
        elif ch == close_ch and stack:
            o = stack.pop()
            matches.append((o, i))
    return matches


def find_smallest_region(flat, cursor_offset):
    best, best_width = None, None
    for open_ch, close_ch in BRACKET_PAIRS:
        for o, c in find_bracket_matches(flat, open_ch, close_ch):
            if o <= cursor_offset <= c:
                width = c - o
                if best_width is None or width < best_width:
                    best, best_width = (o, c), width
    return best


def find_split_boundaries(region_s, separator=SEPARATOR):
    n = len(region_s)
    inner = region_s[1 : n - 1]

    forbidden = []

    for open_ch, close_ch in BRACKET_PAIRS:
        i, m = 0, len(inner)
        while i < m:
            if inner[i] == open_ch:
                depth, j = 1, i + 1
                while j < m and depth > 0:
                    if inner[j] == open_ch:
                        depth += 1
                    elif inner[j] == close_ch:
                        depth -= 1
                    j += 1
                if depth == 0:
                    forbidden.append((i + 1, j + 1))
                    i = j
                    continue
            i += 1

    m = re.search(re.escape(separator) + r"\s*$", inner)
    if m:
        forbidden.append((m.start() + 1, m.end() + 1))

    def is_forbidden(p):
        return any(a <= p < b for a, b in forbidden)

    seps = [mm.start() for mm in re.finditer(re.escape(separator), region_s)]
    valid_seps = [p for p in seps if not is_forbidden(p)]

    boundaries = []
    if n > 2:
        boundaries.append(1)
    boundaries.extend(p + 1 for p in valid_seps)
    boundaries.append(n - 1)
    return sorted(set(boundaries))


def do_split(lines, from_line, from_col, to_col, indent_unit):
    line = lines[from_line - 1]
    start_col = from_col - 1
    end_col = to_col - 1

    prefix = line[:start_col]
    region_s = line[start_col : end_col + 1]
    suffix = line[end_col + 1 :]

    boundaries = find_split_boundaries(region_s, SEPARATOR)
    if not boundaries:
        return False

    segs, prev = [], 0
    for b in boundaries:
        segs.append(region_s[prev:b])
        prev = b
    segs.append(region_s[prev:])

    segs[0] = prefix + segs[0]
    segs[-1] = segs[-1] + suffix

    orig_indent = get_indent(line)
    n = len(segs)
    new_lines = []
    for idx, seg in enumerate(segs):
        if idx == 0:
            new_lines.append(seg.rstrip())
        elif idx == n - 1:
            new_lines.append(orig_indent + seg.strip())
        else:
            new_lines.append(orig_indent + indent_unit + seg.strip())

    lines[from_line - 1 : from_line] = new_lines
    return True


def do_join(lines, from_line, to_line):
    region_lines = lines[from_line - 1 : to_line]
    n_join = len(region_lines) - 1
    if n_join <= 0:
        return False

    result = region_lines[0]
    for i in range(1, len(region_lines)):
        pad = "" if (i == 1 or i == n_join) else " "
        result = result.rstrip() + pad + region_lines[i].lstrip()

    lines[from_line - 1 : to_line] = [result]
    return True


def main():
    if len(sys.argv) < 4:
        print("Usage: splitjoin.py <file> <row> <col>", file=sys.stderr)
        sys.exit(1)

    path, row, col = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

    lines, had_nl, uses_crlf = read_file(path)
    if not lines:
        sys.exit(0)

    indent_unit = detect_indent_unit(lines[:200])
    flat, line_starts = build_flat(lines)
    cursor_offset = pos_to_offset(line_starts, row, col)
    cursor_offset = max(0, min(cursor_offset, len(flat) - 1))

    region = find_smallest_region(flat, cursor_offset)
    if region is None:
        sys.exit(0)

    o, c = region
    from_line, from_col = offset_to_pos(line_starts, o)
    to_line, to_col = offset_to_pos(line_starts, c)

    if from_line == to_line:
        changed = do_split(lines, from_line, from_col, to_col, indent_unit)
    else:
        changed = do_join(lines, from_line, to_line)

    if changed:
        write_file(path, lines, had_nl, uses_crlf)


if __name__ == "__main__":
    main()
