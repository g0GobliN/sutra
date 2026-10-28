"""Register SUTRA's MCP server in a client's config, without rewriting the file.

The edits are textual: a client config may be JSONC (OpenCode's default is
`opencode.jsonc`, and a real one carries comments and other servers), so a
`json.load`/`json.dump` round trip would silently delete both. This module
finds the `mcp` object, adds or removes exactly one `"sutra"` member, and
writes the rest of the file back byte for byte. Every write names a backup
first, and `--dry-run` writes nothing at all.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import time

SERVER_ARGS = ("-m", "sutra.mcp")


# --- a small, string-aware JSON scanner -------------------------------------

def _skip_string(text: str, index: int) -> int:
    """Index just past the string that starts at `text[index]`."""
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    raise ValueError("unterminated string")


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _skip_comment(text: str, index: int) -> int:
    """Index past a // or /* */ comment at `index`, or `index` unchanged."""
    if text.startswith("//", index):
        end = text.find("\n", index)
        return len(text) if end < 0 else end + 1
    if text.startswith("/*", index):
        end = text.find("*/", index + 2)
        if end < 0:
            raise ValueError("unterminated comment")
        return end + 2
    return index


def _skip_trivia(text: str, index: int) -> int:
    """Whitespace and JSONC comments, which a JSONC client config may carry."""
    while True:
        index = _skip_ws(text, index)
        after = _skip_comment(text, index)
        if after == index:
            return index
        index = after


def _value_end(text: str, index: int) -> int:
    """Index just past the JSON value that starts at `text[index]`."""
    index = _skip_ws(text, index)
    if index >= len(text):
        raise ValueError("unexpected end of file")
    char = text[index]
    if char == '"':
        return _skip_string(text, index)
    if char in "{[":
        close = "}" if char == "{" else "]"
        depth = 0
        while index < len(text):
            current = text[index]
            if current == '"':
                index = _skip_string(text, index)
                continue
            if current == "/":
                index = _skip_comment(text, index)
                continue
            if current == char:
                depth += 1
            elif current == close:
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        raise ValueError("unbalanced brackets")
    end = index
    while end < len(text) and text[end] not in ",}] \t\r\n":
        end += 1
    return end


def _members(text: str, open_index: int) -> tuple[list[tuple[int, int, int, int]], int]:
    """(key_start, key_end, value_start, value_end) per member, and the closing `}`."""
    index = open_index + 1
    found: list[tuple[int, int, int, int]] = []
    while True:
        index = _skip_trivia(text, index)
        if index >= len(text):
            raise ValueError("unterminated object")
        if text[index] == "}":
            return found, index
        if text[index] == ",":
            index += 1
            continue
        if text[index] != '"':
            raise ValueError("expected an object key at offset %d" % index)
        key_start = index
        key_end = _skip_string(text, index)
        index = _skip_trivia(text, key_end)
        if index >= len(text) or text[index] != ":":
            raise ValueError("expected ':' after a key")
        value_start = _skip_trivia(text, index + 1)
        value_end = _value_end(text, value_start)
        found.append((key_start, key_end, value_start, value_end))
        index = _skip_trivia(text, value_end)


def _root_object(text: str) -> int:
    index = _skip_trivia(text, 0)
    if index >= len(text) or text[index] != "{":
        raise ValueError("the config's root is not an object")
    return index


def _find(text: str, members, key: str):
    wanted = json.dumps(key)
    for member in members:
        if text[member[0]:member[1]] == wanted:
            return member
    return None


def _line_indent(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    prefix = text[line_start:index]
    return prefix[: len(prefix) - len(prefix.lstrip(" \t"))]


def _entry_block(indent: str, python: str) -> str:
    command = ", ".join(json.dumps(part) for part in (python, *SERVER_ARGS))
    inner = indent + "  "
    return "\n".join((
        indent + '"sutra": {',
        inner + '"type": "local",',
        inner + '"command": [' + command + "],",
        inner + '"enabled": true',
        indent + "}",
    ))


def with_entry(text: str, python: str) -> tuple[str, bool]:
    """The config with a `sutra` MCP entry. Returns (text, changed)."""
    root = _root_object(text)
    root_members, root_close = _members(text, root)
    mcp = _find(text, root_members, "mcp")
    if mcp is None:
        key_indent = _line_indent(text, root) + "  "
        block = key_indent + '"mcp": {\n' + _entry_block(key_indent + "  ", python) + "\n" + key_indent + "}"
        before = text[:root_close].rstrip()
        separator = "," if root_members else ""
        return before + separator + "\n" + block + "\n" + text[root_close:], True
    value_start = mcp[2]
    if text[value_start] != "{":
        raise ValueError('the "mcp" key is not an object')
    members, close = _members(text, value_start)
    existing = _find(text, members, "sutra")
    if existing is not None:
        body = text[existing[2]:existing[3]]
        if all(json.dumps(part) in body for part in (python, "-m", "sutra.mcp")):
            return text, False
        raise ValueError("a different sutra entry already exists; run `sutra uninstall` first")
    key_indent = _line_indent(text, mcp[0])
    block = _entry_block(key_indent + "  ", python)
    if not members:
        return text[:value_start + 1] + "\n" + block + "\n" + key_indent + text[close:], True
    last_end = members[-1][3]
    tail = text[last_end:close]
    # An existing separator after the last member becomes the separator for the
    # new entry, and is re-emitted after it. A file that was JSONC because it
    # ended in a comma keeps that property; one that was strict JSON gets no
    # comma it did not have. Both stay reversible.
    comma = _skip_trivia(tail, 0)
    re_emit = comma < len(tail) and tail[comma] == ","
    if re_emit:
        tail = tail[:comma] + tail[comma + 1:]
    # Whatever follows that separator — a comment on the member's line, say —
    # stays where it is and keeps its owner; the new entry goes after it.
    kept = tail.rstrip(" \t")
    lead = "" if kept.endswith("\n") else "\n"
    return (text[:last_end] + "," + kept + lead + block
            + ("," if re_emit else "") + "\n" + key_indent + text[close:]), True


def without_entry(text: str) -> tuple[str, bool]:
    """The config with the `sutra` MCP entry removed. Returns (text, changed)."""
    root = _root_object(text)
    root_members, _ = _members(text, root)
    mcp = _find(text, root_members, "mcp")
    if mcp is None or text[mcp[2]] != "{":
        return text, False
    members, close = _members(text, mcp[2])
    target = _find(text, members, "sutra")
    if target is None:
        return text, False
    position = members.index(target)
    start, end = target[0], target[3]
    if position + 1 < len(members):          # not last: take the separator after it
        end = _skip_trivia(text, end)
        if end < len(text) and text[end] == ",":
            end += 1
        end = _skip_trivia(text, end)
    elif position > 0:                       # last: drop the separator before it too
        previous_end = members[position - 1][3]
        after = _skip_ws(text, target[3])
        if after < close and text[after] == ",":          # a re-emitted trailing comma
            gap = text[previous_end:start].rstrip(" \t")
            return (text[:previous_end] + gap + _line_indent(text, close)
                    + text[close:]), True
        gap = text[previous_end:start]
        if "//" in gap or "/*" in gap:
            # A comment sits between the previous member and this one. It is
            # not ours to delete: take the separator and the new entry's
            # indentation, and give the closing brace its own indentation back.
            comma = _skip_ws(gap, 0)
            if comma < len(gap) and gap[comma] == ",":
                gap = gap[:comma] + gap[comma + 1:]
            return (text[:previous_end] + gap.rstrip(" \t")
                    + _line_indent(text, close) + text[close:]), True
        start = previous_end
        comma = _skip_trivia(text, start)
        if comma < len(text) and text[comma] == ",":
            start = comma
    return text[:start] + text[end:], True


# --- config discovery and writing -------------------------------------------

def opencode_config(explicit: str | None = None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser()
    override = os.environ.get("SUTRA_OPENCODE_CONFIG")
    if override:
        return pathlib.Path(override).expanduser()
    base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "opencode"
    for name in ("opencode.jsonc", "opencode.json"):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return base / "opencode.json"


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    """Atomic write with a timestamped backup beside the original.

    The stamp has one-second resolution, so a second write inside the same
    second would otherwise overwrite the first backup — and the first backup
    is the only copy of the bytes before the first edit.
    """
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(path.name + ".bak-" + stamp)
    suffix = 1
    while backup.exists():
        suffix += 1
        backup = path.with_name(f"{path.name}.bak-{stamp}-{suffix}")
    shutil.copy2(path, backup)
    temp = path.with_name(path.name + ".sutra-tmp")
    temp.write_text(text, encoding="utf-8")
    shutil.copymode(path, temp)
    os.replace(temp, path)
    return backup


def _python() -> str:
    return sys.executable or "python3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sutra install",
        description="Register SUTRA's MCP server for a client.")
    parser.add_argument("action", nargs="?", default="install",
                        choices=["install", "uninstall"])
    parser.add_argument("--client", default="opencode", choices=["opencode"],
                        help="the client whose config to edit (opencode is the verified one)")
    parser.add_argument("--config", default=None, help="override the config path")
    parser.add_argument("--dry-run", action="store_true", help="print the change; write nothing")
    args = parser.parse_args(argv)

    path = opencode_config(args.config)
    installing = args.action == "install"
    if not path.is_file():
        if installing:
            print(f"sutra: {path} does not exist; open OpenCode once so it writes its "
                  f"config, or pass --config", file=sys.stderr)
            return 1
        print(f"sutra: {path} does not exist; nothing to uninstall")
        return 0
    text = path.read_text(encoding="utf-8")
    try:
        updated, changed = with_entry(text, _python()) if installing else without_entry(text)
    except ValueError as exc:
        print(f"sutra: {path} could not be edited safely: {exc}", file=sys.stderr)
        return 1
    if not changed:
        state = "has" if installing else "has no"
        print(f"sutra: {path} already {state} sutra MCP entry; nothing to do")
        return 0
    if args.dry_run:
        print(f"sutra: dry run, {path} would change (no file was written)")
        return 0
    backup = _write(path, updated)
    print(f"sutra: {'registered' if installing else 'removed'} the sutra MCP entry in {path}")
    print(f"sutra: backup at {backup}")
    if installing:
        print("sutra: restart OpenCode and confirm `sutra` appears in its MCP list")
    return 0
