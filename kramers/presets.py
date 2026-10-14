"""
presets.py -- what to back up for each kind of backup set.

A preset expands to one or more RootSpecs. Paths may use %ENV% variables,
{Known:Downloads}-style known folders, and * wildcards (each match becomes
its own root). Presets live in code so improvements reach existing configs;
a config only stores the preset name plus per-set overrides.

Login tokens are never backed up: credential files are excluded and the
Claude desktop app's config.json is copied with its token keys removed.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import winshell

# Keys removed from JSON settings files (case-insensitive regexes).
TOKEN_KEYS = (r"^oauth:", r"(?i)(token|secret|password|passwd|api[_-]?key|credential|cookie)")


@dataclass
class RootSpec:
    key: str                         # stable id within the set (for enable/disable)
    label: str                       # shown in the UI
    path: str                        # source (folder or file), may contain wildcards
    subdir: str                      # destination, relative to the set's folder
    excludes: tuple = ()
    only: tuple = ()
    sqlite_snapshot: bool = False
    redact_json: tuple = ()
    optional: bool = False
    note: str = ""


@dataclass
class Preset:
    name: str
    title: str
    description: str
    icon: str                        # "known:Downloads" | "path:<p>" | "package:<prefix>" | "exe:<p>"
    roots: list = field(default_factory=list)


def expand_path(p: str) -> str:
    def known(m):
        return winshell.known_folder(m.group(1)) or m.group(0)
    p = re.sub(r"\{Known:(\w+)\}", known, p)
    return os.path.normpath(os.path.expandvars(os.path.expanduser(p)))


def _wild(spec: RootSpec) -> list:
    """Resolve wildcards; each match is a separate root."""
    path = expand_path(spec.path)
    if not any(c in path for c in "*?["):
        return [(spec, path)]
    out = []
    for match in sorted(glob.glob(path)):
        if not os.path.isdir(match):
            continue
        name = Path(match).name
        key = f"{spec.key}:{name}"
        out.append((RootSpec(key=key, label=spec.label.replace("{name}", name), path=match,
                             subdir=spec.subdir.replace("{name}", name), excludes=spec.excludes,
                             only=spec.only, sqlite_snapshot=spec.sqlite_snapshot,
                             redact_json=spec.redact_json, optional=spec.optional,
                             note=spec.note), match))
    return out


# --------------------------------------------------------------------------- #
# Claude
# --------------------------------------------------------------------------- #

CLAUDE_APP_ONLY = (
    "claude_desktop_config.json", "claude_desktop_config.json.*",
    "config.json", "Claude Extensions", "Claude Extensions Settings",
    "extensions-installations.json", "extensions-blocklist.json",
    "mcp-user-tool-toggles.json", "claude-code-sessions", "local-agent-mode-sessions",
    "git-worktrees.json", "space-memory-copy", "spaces-present",
    "cowork-enabled-cli-ops.json", "cowork-spawn-settings", "plan-usage-history.json",
)
CLAUDE_APP_REDACT = (("config.json", TOKEN_KEYS), ("claude_desktop_config.json*", TOKEN_KEYS))

CLAUDE = Preset(
    name="claude", title="Claude",
    description=("Claude Code and the Claude desktop app: CLAUDE.md, settings, skills, plugins, "
                 "per-project memory, sessions, MCP servers and extensions. No login tokens."),
    icon="package:Claude",
    roots=[
        RootSpec("home", "~/.claude", r"%USERPROFILE%\.claude", ".claude",
                 excludes=("/cache", "/shell-snapshots", "/session-env", "/downloads",
                           "/.credentials.json", "/statsig", "/todos/*.lock")),
        RootSpec("homejson", "~/.claude.json", r"%USERPROFILE%\.claude.json", ".claude.json",
                 redact_json=(("*", (r"^oauth:", r"(?i)(secret|password|api[_-]?key|credential)")),)),
        # The Claude desktop app is an MSIX package: its data really lives in
        # the package's LocalCache. Older (non-MSIX) installs use %APPDATA%.
        RootSpec("app-msix", "Claude app data", r"%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude",
                 "AppData-Roaming-Claude-MSIX", only=CLAUDE_APP_ONLY,
                 redact_json=CLAUDE_APP_REDACT, optional=True),
        RootSpec("app", "Claude app data (classic install)", r"%APPDATA%\Claude",
                 "AppData-Roaming-Claude", only=CLAUDE_APP_ONLY,
                 redact_json=CLAUDE_APP_REDACT, optional=True),
        RootSpec("science", "~/.claude-science", r"%USERPROFILE%\.claude-science", ".claude-science",
                 only=("skills", "artifacts", "orgs", "mcp", "prompt-cards", "preferences.json"),
                 optional=True),
    ],
)

# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #

CODEX = Preset(
    name="codex", title="Codex",
    description=("Codex: config.toml, AGENTS.md, skills, memories, automations, plugins, "
                 "session history and state databases. No login tokens."),
    icon="package:OpenAI.Codex",
    roots=[
        RootSpec("home", "~/.codex", r"%USERPROFILE%\.codex", ".codex", sqlite_snapshot=True,
                 excludes=("/.tmp", "/tmp", "/cache", "/.sandbox", "/.sandbox-bin",
                           "/.sandbox-secrets", "/auth.json", "/cap_sid", "/logs_*.sqlite",
                           "/models_cache.json", "/thread-writer-locks", "/project-metadata-locks",
                           "/process_manager", "/*.guard", "/..codex-global-state.json*.tmp-*",
                           "*.lock")),
    ],
)

# --------------------------------------------------------------------------- #
# Python environments
# --------------------------------------------------------------------------- #

CONDA_ROOTS = ("miniconda3", "anaconda3", "miniforge3", "mambaforge")


def venv_base_interpreters() -> list:
    """Base Python installs that %USERPROFILE%\\.venvs\\* depend on."""
    homes = set()
    for cfg in glob.glob(expand_path(r"%USERPROFILE%\.venvs\*\pyvenv.cfg")):
        try:
            for line in Path(cfg).read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "home" and os.path.isdir(v.strip()):
                    homes.add(os.path.normpath(v.strip()))
        except OSError:
            pass
    return sorted(homes)


def environments_preset() -> Preset:
    roots = [
        RootSpec("venv", "venv {name}", r"%USERPROFILE%\.venvs\*", r"venvs\{name}", optional=True),
    ]
    for cr in CONDA_ROOTS:
        roots.append(RootSpec(f"conda-{cr}", "conda env {name}", rf"%USERPROFILE%\{cr}\envs\*",
                              r"conda-envs\{name}", optional=True))
    for home in venv_base_interpreters():
        name = Path(home).name
        roots.append(RootSpec(f"base:{name}", f"Python base {name}", home, rf"python-base\{name}",
                              excludes=("__pycache__",), optional=True,
                              note="the interpreter the venvs were created from"))
    roots += [
        RootSpec("gitconfig", "~/.gitconfig", r"%USERPROFILE%\.gitconfig", r"dotfiles\.gitconfig",
                 optional=True),
        RootSpec("dotconfig", "~/.config", r"%USERPROFILE%\.config", r"dotfiles\.config", optional=True),
    ]
    return Preset(
        name="environments", title="Python environments",
        description=("Full copies of your venvs and conda environments, the base Python they were "
                     "built from, plus rebuild recipes (pip freeze, conda explicit lists)."),
        icon="exe:" + (os.path.join(venv_base_interpreters()[0], "python.exe")
                       if venv_base_interpreters() else ""),
        roots=roots,
    )


# --------------------------------------------------------------------------- #
# EndNote
# --------------------------------------------------------------------------- #

ENDNOTE_DEFAULT = r"{Known:Documents}\EndNote"


def endnote_exe() -> str:
    """Newest installed EndNote.exe (EndNote 2025 beats EndNote 21)."""
    found = []
    for exe in glob.glob(r"C:\Program Files*\EndNote*\EndNote.exe"):
        digits = re.findall(r"\d+", Path(exe).parent.name)
        found.append((int(digits[0]) if digits else 0, exe))
    return max(found)[1] if found else ""


def endnote_preset(source: str = "") -> Preset:
    return Preset(
        name="endnote", title="EndNote",
        description=("EndNote libraries (.enl plus the .Data folder with PDFs). The library "
                     "databases are copied as consistent SQLite snapshots; a library that EndNote "
                     "holds locked is skipped until EndNote is closed. login.txt is never copied."),
        icon="exe:" + endnote_exe(),
        roots=[RootSpec("folder", "", source or ENDNOTE_DEFAULT, "", sqlite_snapshot=True,
                        excludes=("login.txt", "*.enl.lock"))],
    )


def folder_preset(source: str) -> Preset:
    return Preset(name="folder", title="Folder", description="A folder mirrored as-is.",
                  icon="path:" + source,
                  roots=[RootSpec("folder", "", source, "", sqlite_snapshot=True)])


def get_preset(name: str, source: str = "") -> Optional[Preset]:
    if name == "claude":
        return CLAUDE
    if name == "codex":
        return CODEX
    if name == "environments":
        return environments_preset()
    if name == "folder":
        return folder_preset(source)
    if name == "endnote":
        return endnote_preset(source)
    return None


def resolve_roots(preset: Preset) -> list:
    """[(RootSpec, absolute source path)] with wildcards expanded."""
    out = []
    for spec in preset.roots:
        out.extend(_wild(spec))
    return out


def detected(name: str) -> bool:
    """Is there anything on this PC for the preset to back up?"""
    p = get_preset(name)
    return bool(p) and any(os.path.exists(src) for _, src in resolve_roots(p))
