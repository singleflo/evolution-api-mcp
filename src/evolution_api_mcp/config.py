"""Local (stdio) configuration, read once at startup from the environment and the command line.

Blank means unset for every variable: MCP hosts template `${VAR}` into a server's `env`, and an unfilled
placeholder arrives as an empty string that must behave like an absent variable.

Missing credentials are NOT an error here: the server starts, lists its tools, and reports the missing variables
when a tool is called. Every other invalid value fails startup with a `ConfigError` naming the variable and what
it accepts, so a typo never silently widens (or narrows) what the server may do.

Validating tool names needs the registry, so `tools.load_all()` MUST have run before `load_local_config`.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from evolution_api_mcp import clock

ENV_URL = "EVOLUTION_API_URL"
ENV_TOKEN = "EVOLUTION_INSTANCE_TOKEN"
ENV_TOOLSETS = "EVOLUTION_MCP_TOOLSETS"
ENV_ALLOW = "EVOLUTION_MCP_ALLOW"
ENV_DENY = "EVOLUTION_MCP_DENY"
ENV_ALLOW_IRREVERSIBLE = "EVOLUTION_MCP_ALLOW_IRREVERSIBLE"
ENV_DELAY = "EVOLUTION_MCP_DEFAULT_DELAY_MS"
ENV_WRITES = "EVOLUTION_MCP_MAX_WRITES_PER_MINUTE"
ENV_FILE_ROOTS = "EVOLUTION_MCP_FILE_ROOTS"
ENV_DOWNLOAD_DIR = "EVOLUTION_MCP_DOWNLOAD_DIR"
ENV_TIMEZONE = "EVOLUTION_MCP_TIMEZONE"

DEFAULT_DELAY_MS = 1200
MAX_DELAY_MS = 20_000
DEFAULT_MAX_WRITES_PER_MINUTE = 30
DEFAULT_ROOT_NAMES = ("Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music")


class ConfigError(Exception):
    """An environment variable or command-line option holds a value the server cannot accept."""


@dataclass(frozen=True)
class LocalConfig:
    base_url: str | None
    token: str | None
    toolsets: frozenset[str]
    read_only: bool
    allow: frozenset[str] | None
    deny: frozenset[str]
    deny_is_default: bool
    irreversible_granted: bool
    default_delay_ms: int
    max_writes_per_minute: int
    file_roots: tuple[Path, ...]
    download_dir: Path
    timezone: str = "UTC"


def _get(environ: Mapping[str, str], name: str) -> str | None:
    """The stripped value of a variable, None when unset or blank."""
    value = environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _int(environ: Mapping[str, str], name: str, *, default: int, minimum: int, maximum: int | None) -> int:
    raw = _get(environ, name)
    if raw is None:
        return default
    accepted = f"an integer from {minimum} to {maximum}" if maximum is not None else f"an integer of {minimum} or more"
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not valid: use {accepted}.") from None
    if value < minimum or (maximum is not None and value > maximum):
        raise ConfigError(f"{name}={raw!r} is out of range: use {accepted}.")
    return value


def _names(raw: str) -> list[str]:
    """Split a comma list, trimming spaces and dropping empties. Tool names are case-sensitive."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def _validated_names(name: str, raw: str, valid: frozenset[str], accepted: str) -> frozenset[str]:
    entries = _names(raw)
    unknown = sorted({entry for entry in entries if entry not in valid})
    if unknown:
        raise ConfigError(f"{name} names unknown tools: {', '.join(unknown)}. {accepted}")
    return frozenset(entries)


def _parse_roots(raw: str) -> tuple[Path, ...]:
    roots: list[Path] = []
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        path = Path(entry).expanduser()
        if not path.is_absolute():
            raise ConfigError(
                f"{ENV_FILE_ROOTS} entry {entry!r} is not an absolute path: list absolute directories "
                f"separated by {os.pathsep!r}."
            )
        if not path.is_dir():
            raise ConfigError(
                f"{ENV_FILE_ROOTS} entry {entry!r} is not an existing directory: list absolute directories "
                f"separated by {os.pathsep!r}."
            )
        roots.append(path)
    if not roots:
        raise ConfigError(
            f"{ENV_FILE_ROOTS} lists no directory: give absolute directories separated by {os.pathsep!r}."
        )
    return tuple(roots)


def load_local_config(
    environ: Mapping[str, str], *, cli_toolsets: str | None = None, cli_read_only: bool = False
) -> LocalConfig:
    """Build the local configuration from `environ` and the command-line options."""
    # Imported here: toolsets and policy import this module or its neighbours at load time.
    from evolution_api_mcp import policy, registry
    from evolution_api_mcp.toolsets import parse_toolsets

    if cli_toolsets is not None and cli_toolsets.strip():
        source, toolsets_raw = "--toolsets", cli_toolsets
    else:
        source, toolsets_raw = ENV_TOOLSETS, _get(environ, ENV_TOOLSETS)
    try:
        toolsets = parse_toolsets(toolsets_raw)
    except ConfigError as exc:
        raise ConfigError(f"{source}: {exc}") from None

    specs = registry.specs()
    write_names = frozenset(spec.name for spec in specs if spec.kind != "read")
    all_names = frozenset(spec.name for spec in specs)

    read_only = cli_read_only
    allow: frozenset[str] | None = None
    allow_raw = _get(environ, ENV_ALLOW)
    if allow_raw is not None and allow_raw != "*":
        if allow_raw.lower() == "none":
            read_only = True
        else:
            allow = _validated_names(
                ENV_ALLOW,
                allow_raw,
                write_names,
                "Accepted: '*', 'none' (read-only) or a comma list of tools that change data "
                "(evolution-api-mcp --list-tools).",
            )

    deny_raw = _get(environ, ENV_DENY)
    if deny_raw is None:
        deny, deny_is_default = policy.DEFAULT_DENY, True
    else:
        deny = _validated_names(
            ENV_DENY, deny_raw, all_names, "Accepted: a comma list of tool names (evolution-api-mcp --list-tools)."
        )
        deny_is_default = False

    grant_raw = _get(environ, ENV_ALLOW_IRREVERSIBLE)
    if grant_raw is None or grant_raw.lower() in ("no", "false", "0"):
        irreversible_granted = False
    elif grant_raw.lower() in ("yes", "true", "1"):
        irreversible_granted = True
    else:
        raise ConfigError(f"{ENV_ALLOW_IRREVERSIBLE}={grant_raw!r} is not valid: use yes, true, 1, no, false or 0.")

    default_delay_ms = _int(environ, ENV_DELAY, default=DEFAULT_DELAY_MS, minimum=0, maximum=MAX_DELAY_MS)
    max_writes = _int(environ, ENV_WRITES, default=DEFAULT_MAX_WRITES_PER_MINUTE, minimum=0, maximum=None)

    download_raw = _get(environ, ENV_DOWNLOAD_DIR)
    download_dir = Path(download_raw).expanduser() if download_raw else Path.home() / "Downloads" / "evolution-api-mcp"
    if not download_dir.is_absolute():
        raise ConfigError(f"{ENV_DOWNLOAD_DIR}={download_raw!r} is not an absolute path: use an absolute directory.")

    timezone_raw = _get(environ, ENV_TIMEZONE)
    if timezone_raw is None:
        display_zone = clock.detect_local_zone(environ)
    else:
        try:
            clock.zone(timezone_raw)
        except ValueError:
            raise ConfigError(
                f"{ENV_TIMEZONE}: unknown time zone '{timezone_raw}'. Use an IANA name such as Europe/Rome, or UTC."
            ) from None
        display_zone = timezone_raw

    roots_raw = _get(environ, ENV_FILE_ROOTS)
    if roots_raw is not None:
        file_roots = _parse_roots(roots_raw)
    else:
        defaults = [Path.home() / name for name in DEFAULT_ROOT_NAMES]
        file_roots = (*[path for path in defaults if path.is_dir()], download_dir)

    return LocalConfig(
        base_url=_get(environ, ENV_URL),
        token=_get(environ, ENV_TOKEN),
        toolsets=toolsets,
        read_only=read_only,
        allow=allow,
        deny=deny,
        deny_is_default=deny_is_default,
        irreversible_granted=irreversible_granted,
        default_delay_ms=default_delay_ms,
        max_writes_per_minute=max_writes,
        file_roots=file_roots,
        download_dir=download_dir,
        timezone=display_zone,
    )
