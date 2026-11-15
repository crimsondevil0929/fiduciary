"""The files MCP clients keep their servers in, read and rewritten for ``fiduciary protect``.

Five clients, two formats (1.17.0; VS Code and Codex CLI 1.20.0):

=================  ===============================================  ==========================
Client             File                                             Where the servers are
=================  ===============================================  ==========================
Claude Desktop     ``claude_desktop_config.json``                   ``mcpServers``
Cursor             ``~/.cursor/mcp.json``, ``.cursor/mcp.json``      ``mcpServers``
Claude Code        ``.mcp.json`` (``--mcp-config``)                 ``mcpServers``
VS Code            the profile's ``mcp.json``, ``.vscode/mcp.json``  ``servers``
Codex CLI          ``~/.codex/config.toml``, ``.codex/config.toml``  ``[mcp_servers.<name>]``
=================  ===============================================  ==========================

A server entry is local (``command``, ``args``, ``env``) or remote: a URL with headers, which
each client writes its own way (``url`` and ``headers``; Codex's ``bearer_token_env_var``,
``http_headers`` and ``env_http_headers``). :func:`remote_of` reads every form into one.

Rewriting keeps everything else in the file: a JSON file is written whole with the entries
changed, and a TOML file has only the changed servers' tables replaced, comments and all
else left as they were. The first rewrite keeps a copy of the file beside it
(``<name>.before-fiduciary``), and the new file replaces the old in one rename.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from fiduciary.exceptions import FiduciaryError

__all__ = [
    "SECTIONS",
    "ClientFileError",
    "Remote",
    "client_paths",
    "read_entries",
    "remote_of",
    "toml_table",
    "write_entries",
]

SECTIONS: Final = ("mcpServers", "servers")
"""Where a JSON client file keeps its servers: ``mcpServers`` (Claude Desktop, Cursor,
Claude Code), ``servers`` (VS Code)."""
CODEX: Final = "mcp_servers"
"""Codex CLI's table of servers in ``config.toml``."""
_BARE: Final = re.compile(r"[A-Za-z0-9_-]+")
_HEADER: Final = re.compile(r"^\s*\[(?!\[)(.+?)\]\s*(?:#.*)?$")
_PLACEHOLDER: Final = re.compile(r"\$\{[^}]+\}")


class ClientFileError(FiduciaryError):
    """A client file that cannot be read or rewritten."""


def client_paths() -> dict[str, Path]:
    """Where each client keeps its user-wide servers, on this system."""
    home = Path.home()
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        claude, code = support / "Claude", support / "Code" / "User"
    elif os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", str(home)))
        claude, code = appdata / "Claude", appdata / "Code" / "User"
    else:
        claude, code = home / ".config" / "Claude", home / ".config" / "Code" / "User"
    return {
        "claude-desktop": claude / "claude_desktop_config.json",
        "cursor": home / ".cursor" / "mcp.json",
        "vscode": code / "mcp.json",
        "codex": home / ".codex" / "config.toml",
    }


def read_entries(config: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """Every server entry of ``config``: its section, name and entry.

    :raises ClientFileError: When the file is missing, or not the JSON object or TOML
        table a client writes.
    """
    try:
        text = config.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ClientFileError(f"{config}: no such file") from exc
    if config.suffix == ".toml":
        try:
            data: Any = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ClientFileError(f"{config} is not TOML: {exc}") from exc
        servers = data.get(CODEX)
        if not isinstance(servers, dict):
            return []
        return [(CODEX, str(n), dict(e)) for n, e in servers.items() if isinstance(e, dict)]
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ClientFileError(f"{config} is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ClientFileError(f"{config} is not a JSON object")
    found = []
    for section in SECTIONS:
        servers = data.get(section)
        if isinstance(servers, dict):
            found += [(section, str(n), dict(e)) for n, e in servers.items() if isinstance(e, dict)]
    return found


def _backup(config: Path) -> Path:
    backup = config.with_name(config.name + ".before-fiduciary")
    if not backup.exists():
        shutil.copy2(config, backup)
    return backup


def _replace(config: Path, text: str) -> None:
    temporary = config.with_name(config.name + f".{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    try:
        os.chmod(temporary, config.stat().st_mode & 0o777)
    except OSError:
        pass
    os.replace(temporary, config)


def write_entries(config: Path, changes: Mapping[tuple[str, str], Mapping[str, Any]]) -> Path:
    """Write ``changes`` (by section and name) into ``config``; the copy of the file as it
    was before ``protect`` first touched it.

    :raises ClientFileError: When a TOML file keeps a changed server some way other than its
        own ``[mcp_servers.<name>]`` table.
    """
    if config.suffix == ".toml":
        text = config.read_text(encoding="utf-8")
        for (_, name), entry in changes.items():
            text = _toml_replace(text, name, entry, config)
        backup = _backup(config)
        _replace(config, text)
        return backup
    data = json.loads(config.read_text(encoding="utf-8"))
    for (section, name), entry in changes.items():
        data[section][name] = dict(entry)
    backup = _backup(config)
    _replace(config, json.dumps(data, indent=2) + "\n")
    return backup


# -- remote entries ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Remote:
    """A remote server's entry, read from any client's form of it."""

    url: str
    headers: dict[str, str]
    """Each header as the entry writes it: a value, or a template naming a variable
    (``Bearer ${GITHUB_TOKEN}``, ``${env:KEY}``, ``${input:token}``)."""
    variables: tuple[str, ...]
    """Variables the client passes to a server it starts (Codex: ``env_vars``), so a header
    template that names one still finds it."""
    transport: str
    """``http`` (Streamable HTTP), or ``sse`` (the old HTTP+SSE transport)."""


def remote_of(entry: Mapping[str, Any]) -> Remote | None:
    """The remote server ``entry`` names, or ``None`` for a local one."""
    url = entry.get("url") or entry.get("serverUrl")
    if not isinstance(url, str):
        return None
    kind = str(entry.get("type") or entry.get("transport") or "http").lower()
    headers = {str(k): str(v) for k, v in (entry.get("headers") or {}).items()}
    headers.update({str(k): str(v) for k, v in (entry.get("http_headers") or {}).items()})
    variables: list[str] = []
    token = entry.get("bearer_token_env_var")
    if isinstance(token, str) and token:
        headers["Authorization"] = "Bearer ${" + token + "}"
        variables.append(token)
    for name, variable in (entry.get("env_http_headers") or {}).items():
        headers[str(name)] = "${" + str(variable) + "}"
        variables.append(str(variable))
    transport = "sse" if kind == "sse" else "http"
    return Remote(url, headers, tuple(dict.fromkeys(variables)), transport)


def placeholders(text: str) -> list[str]:
    """The ``${...}`` placeholders in ``text``."""
    return _PLACEHOLDER.findall(text)


# -- TOML (Codex CLI) --------------------------------------------------------------------


def _key(part: str) -> str:
    return part if _BARE.fullmatch(part) else json.dumps(part, ensure_ascii=False)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, Mapping):
        return "{ " + ", ".join(f"{_key(str(k))} = {_value(v)}" for k, v in value.items()) + " }"
    raise ClientFileError(f"a value of type {type(value).__name__} has no TOML form here")


def toml_table(name: str, entry: Mapping[str, Any]) -> str:
    """``entry`` as Codex's ``[mcp_servers.<name>]`` table."""
    lines = [f"[{CODEX}.{_key(name)}]"]
    lines += [f"{_key(str(k))} = {_value(v)}" for k, v in entry.items()]
    return "\n".join(lines) + "\n"


def _path_of(header: str) -> list[str] | None:
    """A table header's dotted key, part by part; ``None`` for one this reader does not
    split (it is then left alone)."""
    parts: list[str] = []
    rest = header.strip()
    while rest:
        if rest[0] in "\"'":
            quote = rest[0]
            end = rest.find(quote, 1)
            if end < 0:
                return None
            raw = rest[: end + 1]
            parts.append(json.loads(raw) if quote == '"' else raw[1:-1])
            rest = rest[end + 1 :].strip()
        else:
            match = re.match(r"[A-Za-z0-9_-]+", rest)
            if match is None:
                return None
            parts.append(match.group(0))
            rest = rest[match.end() :].strip()
        if rest.startswith("."):
            rest = rest[1:].strip()
        elif rest:
            return None
    return parts


def _toml_replace(text: str, name: str, entry: Mapping[str, Any], config: Path) -> str:
    lines = text.splitlines(keepends=True)
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for index, line in enumerate(lines):
        match = _HEADER.match(line)
        if match is None and not line.lstrip().startswith("[["):
            continue
        path = _path_of(match.group(1)) if match is not None else None
        ours = path is not None and path[:2] == [CODEX, name]
        if start is not None and not ours:
            blocks.append((start, index))
            start = None
        if ours and start is None:
            start = index
    if start is not None:
        blocks.append((start, len(lines)))
    # Blank lines and comments just before the next table are that table's, not ours.
    trimmed = []
    for begin, end in blocks:
        while end > begin + 1 and (
            not lines[end - 1].strip() or lines[end - 1].lstrip()[:1] == "#"
        ):
            end -= 1
        trimmed.append((begin, end))
    blocks = trimmed
    if not blocks:
        raise ClientFileError(
            f"{config} keeps server {name!r} some way other than its own "
            f"[{CODEX}.{_key(name)}] table: protect rewrites only that"
        )
    table = toml_table(name, entry)
    first = blocks[0][0]
    for begin, end in reversed(blocks):
        del lines[begin:end]
    if first > 0 and lines[first - 1].strip():
        table = "\n" + table
    if first < len(lines) and lines[first].strip():
        table += "\n"
    lines.insert(first, table)
    return "".join(lines)
