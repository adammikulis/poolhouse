"""The ecosystem records: the names a toolchain loads from an ordinary source position, each with what it does.

A name is here because the tool reads it without being told to (`tests/test_publication_oracle.py` runs the real tool on every candidate
and fails a name that runs and is not here), or because a lockfile or wrapper decides what an install or a build runs. `guard.runs_later`
reads these records beside the language registry snapshot (docs/design-isolated-launcher.md, 4.4.2); nothing else keeps a list of names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache

__all__ = [
    "BUILD",
    "CI",
    "DOCUMENTS",
    "ECOSYSTEMS",
    "HOOKS",
    "INSTALL",
    "PROGRAM",
    "PROGRAM_FOLDERS",
    "REVIEW",
    "SHELL_START",
    "WRAPPER",
    "Ecosystem",
    "folders",
    "names",
    "patterns",
    "source_extensions",
    "suffixes",
]

BUILD = "runs when the project is built or tested"
HOOKS = "runs when git commits, merges or pushes"
SHELL_START = "runs every time a shell starts"
CI = "runs on the build servers"
INSTALL = "decides what an install or a build runs"
WRAPPER = "is a wrapper that downloads and runs the build tool"
REVIEW = "decides who reviews a change"
PYTHON_START = "runs every time python starts"
LOGIN = "can start a program for the person"
GIT = "sets commands git runs on its own"
ENVIRONMENT = "runs when a shell enters the folder"
PROGRAM = "is a program in a folder of programs"
DOCUMENTS = frozenset({".md", ".markdown", ".rst", ".adoc", ".txt"})
"""Extensions a tool reads as prose and never runs, though a registry claimant types them as a programming language (`.md` is also a machine
description language): a file with one that a runner names is named and taints nothing it names in turn."""
PROGRAM_FOLDERS = frozenset({"scripts", "bin", "sbin", "libexec", "hooks"})
"""Folders whose files without an extension are programs: the layout of the filesystem hierarchy and of every project that keeps scripts."""


@dataclass(frozen=True, slots=True)
class Ecosystem:
    """One toolchain's record: base names (case folded), suffixes, name patterns, folder names and the extensions it reads as source."""

    tool: str
    names: dict[str, str] = field(default_factory=dict)
    suffixes: dict[str, str] = field(default_factory=dict)
    patterns: tuple[tuple[str, str], ...] = ()
    folders: dict[str, str] = field(default_factory=dict)
    source: tuple[str, ...] = ()


def _each(role: str, *words: str) -> dict[str, str]:
    return dict.fromkeys(words, role)


ECOSYSTEMS = (
    Ecosystem("python", {**_each(BUILD, "conftest.py", "setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "noxfile.py", "pytest.ini"),
                         **_each(PYTHON_START, "sitecustomize.py", "usercustomize.py")}, {".pth": PYTHON_START}),
    Ecosystem("javascript", _each(BUILD, "package.json", ".npmrc", ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs", "gruntfile.js", "gulpfile.js", "gulpfile.ts"),
              patterns=((r".+\.config\.(?:js|mjs|cjs|ts|mts|cts)", BUILD), (r"\.(?:eslintrc|babelrc)\.(?:js|cjs)", BUILD)), source=(".ts", ".tsx")),
    Ecosystem("rust", _each(BUILD, "cargo.toml", "build.rs"), source=(".rs",)),
    Ecosystem("jvm", {**_each(BUILD, "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties", "pom.xml"),
                      **_each(WRAPPER, "gradlew", "gradlew.bat", "mvnw", "mvnw.cmd")}, {".gemspec": BUILD, ".jar": WRAPPER}),
    Ecosystem("make", {**_each(BUILD, "makefile", "gnumakefile", "justfile", "taskfile.yml", "taskfile.yaml", "cmakelists.txt", "procfile",
                               "rakefile", "gemfile", "vagrantfile"), **_each(INSTALL, "workspace", "build.bazel", "lefthook.yml", "lefthook.yaml")},
              {".mk": BUILD, ".cmake": BUILD, ".bzl": BUILD}),
    Ecosystem("ci", _each(CI, "jenkinsfile", ".gitlab-ci.yml", ".travis.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".drone.yml")),
    Ecosystem("lockfiles", _each(INSTALL, "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock", "poetry.lock", "uv.lock", "go.mod",
                                 "go.sum", "pipfile", "pipfile.lock", "requirements.txt", "gemfile.lock")),
    Ecosystem("review", _each(REVIEW, "codeowners")),
    Ecosystem("git", {**_each(GIT, ".gitconfig", ".gitmodules", ".gitattributes"), ".pre-commit-config.yaml": HOOKS}),
    Ecosystem("direnv", _each(ENVIRONMENT, ".envrc", ".tool-versions", "mise.toml", ".mise.toml")),
    Ecosystem("login", _each(SHELL_START, ".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile", ".zshrc", ".zshenv", ".zprofile", ".zlogin",
                             ".zlogout", ".cshrc", ".tcshrc", ".kshrc", "config.fish", ".vimrc", ".exrc", "init.vim", "init.lua", ".emacs", "init.el",
                             ".xinitrc", ".xprofile", "crontab"),
              {".plist": "can start a program when launchd loads it", ".service": "can start a program when systemd loads it",
               ".timer": "can start a program when systemd loads it"},
              folders={"launchagents": "runs at login", "launchdaemons": "runs at startup"}),
    Ecosystem("text", source=(".txt",)),
)


@cache
def names() -> dict[str, str]:
    """Every record's base name, case folded, with what it does."""
    return {name: role for each in ECOSYSTEMS for name, role in each.names.items()}


@cache
def suffixes() -> dict[str, str]:
    return {suffix: role for each in ECOSYSTEMS for suffix, role in each.suffixes.items()}


@cache
def folders() -> dict[str, str]:
    return {folder: role for each in ECOSYSTEMS for folder, role in each.folders.items()}


@cache
def patterns() -> tuple[tuple[re.Pattern[str], str], ...]:
    return tuple((re.compile(pattern, re.IGNORECASE), role) for each in ECOSYSTEMS for pattern, role in each.patterns)


@cache
def source_extensions() -> frozenset[str]:
    """Extensions the language registry shares with a data language that a toolchain reads as source: `.rs`, `.ts`, `.tsx`, `.txt`."""
    return frozenset(extension for each in ECOSYSTEMS for extension in each.source)
