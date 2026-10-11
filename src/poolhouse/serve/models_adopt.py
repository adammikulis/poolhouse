"""``poolhouse-models adopt plan|run``: take the models kept outside the hub cache into it.

Three places keep models in a layout of their own (`models_adopt_sources`): the decider's pinned files, the
view of a checkpoint whose PLE table is read in place, and the quantized drafters. ``plan`` verifies every file
and prints what is linked into the cache, what is a duplicate of bytes the cache already holds, what stays and
what is refused; ``run`` does exactly that. Files are linked on the cache's volume, never copied: the extra disk
is zero, an old name is removed only once the cache's copy reads back as the same bytes, and a file nothing
vouches for stays where it is. Progress is appended to ``<state>/models-adopt.log``; a cut-off run is finished
by the next.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from poolhouse import home, hub
from poolhouse.hub import adopt, derived, hfstore, remote
from poolhouse.log import say, warn
from poolhouse.serve.models_adopt_sources import Draft, decide_folders, drafters, weight_views
from poolhouse.serve.models_migrate import append_log
from poolhouse.units import human_bytes

__all__ = ["Part", "Report", "command", "run"]

LOG = "models-adopt.log"


@dataclass
class Part:
    """One folder or file taken in: what it is, the repository and commit it becomes, and what happens to each file."""

    kind: str
    repo: str
    revision: str
    where: str
    keep: bool = False
    rows: list[adopt.Row] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Report:
    """What a plan or run found, did and left."""

    mode: str
    cache: str
    parts: list[Part] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    @property
    def rows(self) -> list[adopt.Row]:
        return [row for part in self.parts for row in part.rows]

    @property
    def ok(self) -> bool:
        return not self.blocked and all(row.action != "blocked" for row in self.rows)

    def total(self, action: str) -> tuple[int, int]:
        """How many files and how many bytes have this action."""
        found = [row for row in self.rows if row.action == action]
        return len(found), sum(row.size for row in found)

    def as_dict(self) -> dict[str, object]:
        return {"mode": self.mode, "cache": self.cache, "extra_disk_bytes": 0, "notes": self.notes,
                "blocked": self.blocked,
                "parts": [{"kind": p.kind, "repo": p.repo, "revision": p.revision, "where": p.where,
                           "notes": p.notes, "files": [row.as_dict() for row in p.rows]}
                          for p in self.parts]}

    def lines(self) -> list[str]:
        free = human_bytes(shutil.disk_usage(hfstore.volume(hub.hub_cache())).free)
        out = [f"adopt {self.mode}", f"hub cache   {self.cache}",
               f"extra disk  0 B: files are linked on the cache's volume ({free} free), never copied", *self.notes]
        for part in self.parts:
            names = "names kept" if part.keep else "old names removed once read back"
            out += ["", f"{part.repo} @ {part.revision[:8]}  [{part.kind}: {names}]  {part.where}",
                    *[_line(row) for row in part.rows], *[f"  {note}" for note in part.notes]]
        return [*out, "", *_summary(self), *[f"blocked: {why}" for why in self.blocked]]


def _line(row: adopt.Row) -> str:
    note = row.why or row.how
    return f"  {row.action:8} {human_bytes(row.size):>9}  {row.rel}" + (f"  ({note})" if note else "")


def _summary(report: Report) -> list[str]:
    done = report.mode == "run"
    meanings = (("link", "linked" if done else "to link", "a new name in the cache for bytes it did not hold"),
                ("have", "duplicates", "byte for byte equal to bytes the cache holds; the old name "
                 + ("is removed" if done else "is removed by run")),
                ("adopted", "already in", "the same file as the cache's"),
                ("derived", "kept", "not the Hub's bytes; they stay where they are"),
                ("blocked", "refused", "they stay where they are; nothing is removed"))
    out = []
    for action, label, meaning in meanings:
        count, size = report.total(action)
        if count:
            out.append(f"{label:10} {count} file(s), {human_bytes(size)}: {meaning}")
    freed = human_bytes(report.total("have")[1])
    return [*out, f"extra disk {'used' if done else 'needed'}: 0 B; {'freed' if done else 'freed by run'}: {freed}"]


def _progress(folder: adopt.Folder, apply: bool) -> Callable[[adopt.Row], None]:
    def seen(row: adopt.Row) -> None:
        warn(f"  {row.action:8} {row.rel}")
        if apply:
            append_log(LOG, [f"{row.action} {row.path} -> {folder.repo}@{folder.revision[:8]}/{row.rel}"
                             + (f": {row.why}" if row.why else "")])
    return seen


def _point_main(folder: adopt.Folder, rows: list[adopt.Row], *, asked: bool) -> None:
    """``refs/main`` names the folder's commit when the Hub says ``main`` is that commit and nothing else
    names one."""
    if not asked or not any(row.action in ("link", "have", "adopted") for row in rows):
        return
    if (hfstore.repo_root(folder.repo) / "refs" / "main").exists():
        return
    try:
        main = remote.commit(folder.repo, "main")
    except (remote.RemoteError, OSError, ValueError):
        return
    if main == folder.revision:
        hfstore.point_ref(folder.repo, "main", main)


def _folder_part(kind: str, folder: adopt.Folder, *, apply: bool, offline: bool) -> Part:
    listed, why = (None, "the Hub was not asked (--offline)") if offline else adopt.listing(folder)
    part = Part(kind, folder.repo, folder.revision, str(folder.path), folder.keep)
    if listed is None:
        part.notes.append(f"{why}: files are vouched for by their download record or their structure")
    part.rows = adopt.take(folder, listed, apply=apply, on_row=_progress(folder, apply))
    if listed is not None:
        held = {row.rel for row in part.rows if row.action in ("link", "have", "adopted")}
        absent = [one for path, one in listed.items() if path not in held and not one.name.startswith(".git")]
        if absent and folder.keep:
            part.notes.append(f"the cache's snapshot still lacks {len(absent)} file(s) the Hub lists, "
                              f"{human_bytes(sum(o.size for o in absent))}: "
                              f"`poolhouse-models pull hf:{folder.repo}@{folder.revision}` fetches them")
    if apply:
        _point_main(folder, part.rows, asked=listed is not None)
    return part


def _draft_row(draft: Draft, *, apply: bool) -> adopt.Row:
    path = draft.path
    base = adopt.Row(path.name, str(path), path.stat().st_size, "blocked", "its structure")
    if draft.source is None:
        return replace(base, why=draft.why)
    root = hfstore.repo_root(derived.repo_id(draft.source, draft.recipe))
    if path.stat().st_dev != hfstore.volume(root).stat().st_dev:
        return replace(base, why="is on another volume than the hub cache and is not copied")
    if problem := adopt.structure_problem(path):
        return replace(base, why=problem)
    kept = derived.lookup(draft.source, draft.recipe)
    if kept is not None and not hfstore.same_bytes(kept, path):
        return replace(base, why="the cache holds another file made from this source by this recipe; not replaced")
    if apply:
        try:
            derived.place(draft.source, draft.recipe, path, origin=f"adopted from {path}")
        except OSError as exc:
            return replace(base, why=str(exc))
    return replace(base, action="have" if kept is not None else "link")


def _draft_part(draft: Draft, *, apply: bool) -> Part:
    source = draft.source
    part = Part("drafter", derived.repo_id(source, draft.recipe) if source else draft.path.name,
                derived.revision_of(source, draft.recipe) if source else "", str(draft.path))
    row = _draft_row(draft, apply=apply)
    part.rows.append(row)
    if source:
        part.notes.append(f"made from {source.repo} @ {source.revision[:8]} {source.file} by {draft.recipe}")
    if apply:
        append_log(LOG, [f"{row.action} {row.path} -> {part.repo}@{part.revision[:8]}"
                         + (f": {row.why}" if row.why else "")])
    return part


def run(*, apply: bool, offline: bool = False) -> Report:
    """Plan or run the adoption of every model kept outside the hub cache; ``offline`` does not ask the Hub."""
    cache = hub.hub_cache()
    report = Report("run" if apply else "plan", str(cache))
    if problem := hfstore.cache_problem(cache):
        report.blocked.append(problem)
        return report
    decided, odd_decide = decide_folders()
    viewed, odd_view = weight_views()
    report.notes += [f"not adopted: {one}" for one in (*odd_decide, *odd_view)]
    for kind, folders in (("decide", decided), ("view", viewed)):
        report.parts += [_folder_part(kind, folder, apply=apply, offline=offline) for folder in folders]
    if apply:
        hfstore.tidy(home.cache("decide", "snapshots"))
    report.parts += [_draft_part(draft, apply=apply) for draft in drafters()]
    if not report.parts:
        report.notes.append("nothing to adopt: no model is kept outside the hub cache")
    if apply:
        append_log(LOG, [f"done: {report.total('link')[0]} linked, {report.total('have')[0]} duplicates, "
                         f"{report.total('blocked')[0]} refused"])
    return report


def command(action: str, *, as_json: bool = False, offline: bool = False) -> int:
    """The ``adopt`` subcommand: print the report; exit 1 when anything is refused."""
    if action not in ("plan", "run"):
        raise SystemExit("poolhouse-models adopt takes plan or run")
    report = run(apply=action == "run", offline=offline)
    say(json.dumps(report.as_dict(), indent=2) if as_json else "\n".join(report.lines()))
    return 0 if report.ok else 1
