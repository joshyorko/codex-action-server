#!/usr/bin/env python3
"""Fill absent Codex worker sandbox and approval defaults without rewriting TOML."""

from pathlib import Path
import re
import sys
import tomllib


TOP_LEVEL_DEFAULTS = {
    "sandbox_mode": 'sandbox_mode = "workspace-write"',
    "approval_policy": 'approval_policy = "on-request"',
    "approvals_reviewer": 'approvals_reviewer = "user"',
}
WORKSPACE_DEFAULTS = {
    "network_access": "network_access = true",
    "writable_roots": 'writable_roots = ["/workspaces"]',
}


def _table_start(content: str) -> int:
    match = re.search(r"(?m)^[ \t]*\[{1,2}(?:[A-Za-z_]|[\"'])", content)
    return match.start() if match else len(content)


def _top_level_insertion(content: str, lines: list[str]) -> str:
    position = _table_start(content)
    prefix, suffix = content[:position], content[position:]
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    inserted = "\n".join(lines) + "\n"
    if suffix and not suffix.startswith("\n"):
        inserted += "\n"
    return prefix + inserted + suffix


def _inline_table_closing_brace(content: str, opening_brace: int) -> int:
    depth = 0
    quote = ""
    escaped = False
    comment = False
    for position in range(opening_brace, len(content)):
        char = content[position]
        if comment:
            if char == "\n":
                comment = False
            continue
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char == "#":
            comment = True
        elif char in ('"', "'"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return position
    raise ValueError("unterminated sandbox_workspace_write inline table")


def _extend_inline_table(
    content: str, defaults: list[str], workspace: dict[str, object]
) -> str:
    table_start = _table_start(content)
    preamble = content[:table_start]
    match = re.search(r"(?m)^[ \t]*sandbox_workspace_write[ \t]*=[ \t]*\{", preamble)
    if match is None:
        raise ValueError("sandbox_workspace_write inline table was not found")
    opening = preamble.index("{", match.start())
    closing = _inline_table_closing_brace(preamble, opening)
    body = preamble[opening + 1 : closing]
    separator = ", " if workspace else " "
    return (
        preamble[: opening + 1]
        + " "
        + ", ".join(defaults)
        + separator
        + body
        + preamble[closing:]
        + content[table_start:]
    )


def _extend_workspace_table(content: str, defaults: list[str]) -> str:
    match = re.search(
        r"(?m)^[ \t]*\[sandbox_workspace_write\][ \t]*(?:#[^\n]*)?(?:\n|$)",
        content,
    )
    if match is None:
        raise ValueError("sandbox_workspace_write table was not found")
    next_table = re.search(
        r"(?m)^[ \t]*\[{1,2}(?:[A-Za-z_]|[\"'])", content[match.end() :]
    )
    end = match.end() + next_table.start() if next_table else len(content)
    prefix, suffix = content[:end], content[end:]
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    lines = "".join(f"{line}\n" for line in defaults)
    if suffix and not suffix.startswith("\n"):
        lines += "\n"
    return prefix + lines + suffix


def ensure_worker_defaults(path: Path) -> None:
    content = path.read_text()
    config = tomllib.loads(content)
    missing_top = [
        line for key, line in TOP_LEVEL_DEFAULTS.items() if key not in config
    ]
    if missing_top:
        content = _top_level_insertion(content, missing_top)

    workspace = config.get("sandbox_workspace_write")
    if workspace is None:
        content += "\n[sandbox_workspace_write]\n"
        content += "\n".join(WORKSPACE_DEFAULTS.values()) + "\n"
    elif isinstance(workspace, dict):
        missing_workspace = [
            line for key, line in WORKSPACE_DEFAULTS.items() if key not in workspace
        ]
        if missing_workspace:
            if re.search(
                r"(?m)^[ \t]*\[sandbox_workspace_write\][ \t]*(?:#[^\n]*)?(?:\n|$)",
                content,
            ):
                content = _extend_workspace_table(content, missing_workspace)
            elif re.search(
                r"(?m)^[ \t]*sandbox_workspace_write[ \t]*=[ \t]*\{",
                content[: _table_start(content)],
            ):
                content = _extend_inline_table(content, missing_workspace, workspace)
            else:
                content = _top_level_insertion(
                    content,
                    [
                        f"sandbox_workspace_write.{key} = {line.split('=', 1)[1].strip()}"
                        for key, line in WORKSPACE_DEFAULTS.items()
                        if key not in workspace
                    ],
                )
    else:
        raise ValueError("sandbox_workspace_write must be a TOML table")

    tomllib.loads(content)
    if content != path.read_text():
        path.write_text(content)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ensure_worker_defaults.py CONFIG_TOML")
    ensure_worker_defaults(Path(sys.argv[1]))
