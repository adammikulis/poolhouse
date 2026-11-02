"""Source-backed launch accounting rules, never resource reservations or execution rights."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .common import keys


def load(root: Path) -> dict:
    value = tomllib.loads((root / "native_boundaries/launch_data.toml").read_text())
    shape = {"version", "status", "arguments", "environment", "reservation", "units", "input_vector_null_slots", "mac", "linux", "windows"}
    keys(value, shape, shape)
    if (type(value["version"]) is not int or value["version"] != 1 or value["status"] != "untrusted-data-validation"
            or value["arguments"] != "complete-argv-including-explicit-argv0"
            or value["environment"] != "explicit-ordered-named-entries-no-inheritance"
            or value["reservation"] != "same-retained-inputs-allocation-and-full-bound-plan-digest"
            or value["units"] != ["unix-raw-bytes", "windows-big-endian-utf16-units"]):
        raise ValueError("unsupported launch data accounting grammar")
    _count(value["input_vector_null_slots"])
    for name in ["mac", "linux"]:
        _unix(value[name], "MacNativeImage" if name == "mac" else "LinuxNativeImage")
    windows = value["windows"]
    shape = {"profile", "unit_bytes", "status", "source_urls"}
    keys(windows, shape, shape)
    if (windows["profile"] != "WindowsNativeUnavailable" or type(windows["unit_bytes"]) is not int or windows["unit_bytes"] != 2
            or windows["status"] != "requires-native-key-equivalence-and-measured-target-commandline-binding"
            or not isinstance(windows["source_urls"], list) or not windows["source_urls"]
            or any(not isinstance(url, str) or not url.startswith("https://learn.microsoft.com/") for url in windows["source_urls"])):
        raise ValueError("Windows launch data cannot claim native compatibility")
    return value


def _count(value: object) -> None:
    if type(value) is not int or not 0 <= value <= 2**64 - 1:
        raise ValueError("invalid launch layout count")


def _unix(row: dict, expected: str) -> None:
    shape = {"profile", "vector_null_slots", "align_string_tail", "charge_exec_filename", "source_revision", "source_version", "source_path", "source_url"}
    keys(row, shape, shape)
    _count(row["vector_null_slots"])
    if (row["profile"] != expected
            or type(row["align_string_tail"]) is not bool or type(row["charge_exec_filename"]) is not bool
            or not isinstance(row["source_revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", row["source_revision"])
            or any(not isinstance(row[key], str) or not row[key] for key in ["source_version", "source_path", "source_url"])
            or not row["source_url"].startswith("https://")):
        raise ValueError("launch layout requires a pinned source and closed rules")


def render(model: dict) -> str:
    return "\n".join([
        "// Generated launch DATA accounting layout; no OS observation or reservation issuer.",
        f"pub(super) const INPUT_VECTOR_NULL_SLOTS: u64 = {model['input_vector_null_slots']};",
        f"pub(super) const MAC_VECTOR_NULL_SLOTS: u64 = {model['mac']['vector_null_slots']};",
        f"pub(super) const MAC_ALIGN_STRING_TAIL: bool = {str(model['mac']['align_string_tail']).lower()};",
        f"pub(super) const MAC_CHARGE_EXEC_FILENAME: bool = {str(model['mac']['charge_exec_filename']).lower()};",
        f"pub(super) const LINUX_VECTOR_NULL_SLOTS: u64 = {model['linux']['vector_null_slots']};",
        f"pub(super) const LINUX_ALIGN_STRING_TAIL: bool = {str(model['linux']['align_string_tail']).lower()};",
        f"pub(super) const LINUX_CHARGE_EXEC_FILENAME: bool = {str(model['linux']['charge_exec_filename']).lower()};",
        f"pub(super) const WINDOWS_UNIT_BYTES: usize = {model['windows']['unit_bytes']};", "",
    ])
