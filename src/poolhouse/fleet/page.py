"""Fleet page components and their shared script modules."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from pathlib import Path

from poolhouse.ui import Component, assemble, load

from . import notice_copy, vocabulary
from .updates import GIT_URL, current_version

WEB = Path(__file__).parent / "web"
COMPONENTS_DIR = WEB / "components"
#: the page, in the order the elements wire themselves up
COMPONENTS = ("fleet-model", "experimental-notice", "vocabulary-model", "fleet-nav", "sign-in", "launch-needed", "startup-models", "pool-actions", "first-run", "fleet-benchmark", "pool-view",
              "output-token-slider", "chat-stream", "chat-markdown", "chat-view", "board-view", "projects-view", "wired-memory", "feature-flags", "models-library", "model-browser", "models-view", "theme-editor", "vocabulary-settings", "settings-view", "fit-model", "fit-view",
              "fit-charts", "rates-view", "telemetry-view",
              "workspace-jobs", "history-view", "tasks-view", "data-discover", "data-view", "knowledge-view",
              "studio-styles", "studio-model", "studio-persistence", "studio-bindings", "studio-editor", "studio-sweeps", "studio-targets", "studio-setup", "studio-managed", "studio-live", "studio-delivery", "studio-webhook", "studio-producer", "studio-records", "studio-branches", "studio-groups", "studio-runs", "studio-sharing", "studio-nodes", "studio-insertion", "studio-placement", "studio-canvas", "studio-view", "training-view", "tools-view", "benchmarks-view", "gym-scene-controls",
              "gym-drone-camera", "gym-scene", "gym-recordings",
              "gym-world-options", "gym-model-options", "gym-view", "close-sheet")
MODULES = {"studio-header": frozenset({"studio-view"}),
           "studio-record-fields": frozenset({"studio-model"}),
           "settings-state": frozenset({"settings-view"}),
           "data-preview": frozenset({"data-discover", "data-view"}),
           "data-studio": frozenset({"data-view"}),
           "studio-n8n": frozenset({"studio-editor", "studio-view"}),
           "task-publishing": frozenset({"tasks-view"}),
           "task-review-quality": frozenset({"tasks-view"}),
           "gym-drone-geometry": frozenset({"gym-scene"}),
           "setup-recovery": frozenset({"first-run", "pool-actions"}),
           "chat-model-picker": frozenset({"chat-view"}),
           "chat-runtime": frozenset({"chat-view", "settings-view"}),
           "chat-coding": frozenset({"chat-view"}), "workspace-model": frozenset({
    "workspace-jobs", "history-view", "tasks-view", "data-view", "knowledge-view", "studio-styles", "studio-model", "studio-persistence", "studio-bindings", "studio-editor", "studio-sweeps", "studio-targets", "studio-setup", "studio-managed", "studio-live", "studio-delivery", "studio-webhook", "studio-producer", "studio-records", "studio-branches", "studio-groups", "studio-runs", "studio-sharing", "studio-nodes", "studio-insertion", "studio-placement", "studio-canvas", "studio-view", "training-view", "tools-view", "benchmarks-view",
    "gym-world-options", "gym-recordings", "gym-model-options", "gym-view",
})}
#: components from the experimental layer; a build without their files leaves them out
EXPERIMENTAL = frozenset({"gym-scene-controls", "gym-drone-camera", "gym-scene", "gym-recordings", "gym-world-options",
                          "gym-model-options", "gym-view", "gym-drone-geometry", "benchmarks-view", "fleet-benchmark",
                          "training-view"})
#: the fit screen on its own, for a machine running no daemon
FIT_ONLY = ("fleet-model", "fit-model", "fit-view", "fit-charts", "rates-view",
            "telemetry-view")


def _absent(part: str | Component) -> bool:
    return not isinstance(part, Component) and part in EXPERIMENTAL and not (COMPONENTS_DIR / f"{part}.html").is_file()


def components(names: Sequence[str | Component] = COMPONENTS) -> list[Component]:
    """Return page components with shared modules inserted before their consumers."""
    parts = [part if isinstance(part, Component) else load(COMPONENTS_DIR, [part])[0]
             for part in names if not _absent(part)]
    present = {part.name for part in parts}
    for module, consumers in MODULES.items():
        if module in present:
            continue
        index = next((index for index, part in enumerate(parts) if part.name in consumers), None)
        if index is not None:
            parts.insert(index, load(COMPONENTS_DIR, [module])[0])
    return parts



def render(parts: Sequence[str | Component] = COMPONENTS) -> str:
    """The whole page, as one string."""
    shell = (WEB / "shell.html").read_text(encoding="utf-8")
    for name in sorted(EXPERIMENTAL):
        if _absent(name):
            shell = shell.replace(f"<{name}></{name}>", "")
    notice = "".join(f"<p>{escape(text)}</p>" for text in notice_copy.PARAGRAPHS)
    names = {part.name if isinstance(part, Component) else part for part in parts}
    calculation = (f'<aside class="card wide calculation-notice" aria-label="Experimental software"><h1>{escape(notice_copy.TITLE)}</h1>'
                   + notice + f'<a href="{escape(GIT_URL + "/issues")}" target="_blank" rel="noopener noreferrer">Report bugs on GitHub</a></aside>') \
        if "fit-view" in names and "experimental-notice" not in names else ""
    shell = shell.replace("__CALCULATION_NOTICE__", calculation)
    shell = shell.replace("__RUNNING_VERSION__", escape(current_version() or "version unavailable"))
    page = assemble(shell, components(parts)).replace("__EXPERIMENTAL_COPY__", notice).replace("__EXPERIMENTAL_TITLE__", escape(notice_copy.TITLE))
    return page.replace("__VOCABULARY_DATA__", vocabulary.payload()).replace("__VOCAB__", vocabulary.default_vocabulary())
