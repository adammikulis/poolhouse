"""Generate small semantic API modules with one frozen public-type registry."""
from __future__ import annotations

import pprint
import re
from collections import defaultdict
from pathlib import Path

from . import python


def group(name: str) -> str:
    if name.startswith("Inference"):
        return inference_group(name)
    if name.startswith("BoardContent"):
        return board_content_group(name)
    if name.startswith("TaskExtension"):
        if "Scope" in name or "ExecutionConfig" in name:
            return "task_scope"
        if any(word in name for word in ("Record", "Patch", "Integration", "Attempt", "Event", "Evidence", "Runner")):
            return "task_records"
        return "task_extensions"
    if name.startswith("CoordinationReservation"):
        return "task_reservations"
    if name.startswith("CoordinationClaim"):
        return "task_claims"
    if name.startswith("Task") and name not in {"TaskId", "TaskRef", "TaskExecutionId", "TaskExecutionState"}:
        return task_group(name)
    if name.startswith("CatalogBoard"):
        return "projects_boards"
    if name.startswith("Project") and name != "ProjectId":
        for words, owner in (
            (("Location",), "projects_locations"),
            (("Creation", "Create", "Default"), "projects_creation"),
            (("Selection",), "projects_selection"),
            (("Source",), "projects_sources"),
            (("Board",), "projects_boards"),
        ):
            if any(word in name for word in words):
                return owner
        return "projects_catalog"
    if name.startswith("Connectivity") or name in {"MembershipGeneration", "TransportGeneration"}:
        for words, owner in (
            (("Sync", "Connection", "ConnectRequest", "Disconnect", "Interest", "Direction", "Transport"), "connectivity_transport"),
            (("Join", "Cancellation"), "connectivity_join"),
            (("Candidate", "Endpoint", "Address", "Hint", "Provider", "Interface", "KeyProof", "Advertisement"), "connectivity_discovery"),
            (("Device", "Pool", "Membership", "Removal"), "connectivity_membership"),
        ):
            if any(word in name for word in words):
                return owner
        return "connectivity"
    if name.startswith("Startup"):
        return "startup"
    if name.startswith("Setup"):
        return "setup"
    if name.startswith("Settings") or name in {"InterpreterId", "FeatureId", "UpdatePolicyId"}:
        if any(word in name for word in ("Theme", "Appearance", "Color", "Colour", "Density", "Font")):
            return "settings_appearance"
        if any(word in name for word in ("Execution", "Download", "Update", "Interpreter", "Feature", "Availability", "Schedule", "Weekday", "Minute")):
            return "settings_policy"
        return "settings_local"
    if name.startswith(("Approval", "Authority")):
        if any(word in name for word in ("Intent", "Mutation", "Resource", "Grant", "Threshold", "Hold")):
            return "approval_intent"
        if any(word in name for word in ("Key", "Signer", "Signature", "Descriptor", "Authority", "Root", "Assurance")):
            return "approval_identity"
        return "approval_view"
    if name.startswith("Model") or name in {"AssetId", "OfferId", "TransferId", "StorageReservationId", "ServingId", "ServingLeaseId", "ResourceDomainId"}:
        if name.startswith(("ModelList", "ModelSearch", "ModelRecommend", "ModelLocate", "ModelComponentsRequest", "ModelComponentsResponse", "ModelStartupChoices")):
            return "model_query"
        if name.startswith("ModelWatch"):
            return "model_watch"
        if name.startswith(("ModelServing", "ModelLease", "Serving")):
            return "model_serving"
        if name.startswith(("ModelAsset", "ModelDownload", "ModelTransfer", "ModelPartial", "ModelImport", "ModelStorage", "Asset", "Offer", "Transfer", "StorageReservation")):
            return "model_assets"
        if name.startswith(("ModelResource", "ModelMemory", "ModelPhysical", "ModelPlacement", "ResourceDomain")):
            return "model_resources"
        if name.startswith(("ModelFit", "ModelPoolRequirement")):
            return "model_fit"
        if any(word in name for word in ("Observed", "Known", "Quantity", "Ratio", "Margin", "Unknown", "Observation")):
            return "model_observation"
        return "model_catalogue"
    if name.startswith("Job"):
        return "jobs"
    if name.startswith(("Agent", "Task")) or name == "BoardId":
        return "agents"
    return "core"


def board_content_group(name: str) -> str:
    for words, owner in (
        (("File", "Upload", "Blob", "PlainSize", "BytePart"), "board_content_files"),
        (("Link",), "board_content_links"),
        (("Room", "Member", "Actors"), "board_content_rooms"),
        (("Delivery", "Subscribe", "Unsubscribe", "Outbox", "Sent"), "board_content_delivery"),
        (("Note", "Verification"), "board_content_notes"),
        (("Watch", "Read", "Filter", "Position", "Vector", "Query", "Entry", "Entries", "Document", "Presentation", "Ack"), "board_content_read"),
    ):
        if any(word in name for word in words):
            return owner
    return "board_content"


def task_group(name: str) -> str:
    if name.endswith("Request"):
        return "task_requests"
    if name.startswith(("TaskStream", "TaskSnapshot")) or name == "TaskReadEvent":
        return "task_streams"
    for words, owner in (
        (("Review", "Rating", "Quality", "Assessment", "Recorded", "Artifact", "Submission", "Provenance", "Digest", "Proposal"), "task_review"),
        (("Allocation", "Lease", "Heartbeat", "Checkpoint", "Execution"), "task_allocation"),
        (("Specification", "Criteria", "Capabilities", "Dependencies"), "task_specification"),
    ):
        if any(word in name for word in words):
            return owner
    return "tasks"


def inference_group(name: str) -> str:
    suffix = name.removeprefix("Inference")
    if suffix.startswith(("Content", "CodingContent", "LiteralJson", "Stage", "SourceLimit", "Prepar", "Prepare")):
        return "inference_content"
    if suffix.startswith(("Turn", "ToolResult")):
        return "inference_chat"
    if suffix.startswith(("Event", "TextDelta", "ToolDelta", "ProviderEvent", "Terminal", "Cursor", "OptionalCursor", "Watch")):
        return "inference_streams"
    if suffix.startswith(("ToolProposal", "ToolPolicy", "Effect", "OptionalEffect")):
        return "inference_effects"
    if suffix.endswith(("Input", "Request", "Options", "TokenBudget")) or suffix in {"Binding", "Delivery", "Lifetime"}:
        return "inference_requests"
    if suffix.endswith(("Result", "Results")) or suffix.startswith(("Rank", "Logprob", "LogProbability", "TokenLogprob", "Timing", "Duration", "Known", "Unknown", "Observation", "Usage", "Result", "Partial", "Finish", "State")):
        return "inference_results"
    return "inference"


def symbol_order(value: str, *, fold: bool = True) -> tuple:
    return (not value.lstrip("_").isupper(),
            tuple(int(part) if part.isdigit() else part for part in re.split(r"([0-9]+)", value.casefold() if fold else value)))


def imported(module: str, names: list[str], *, export: bool = False) -> str:
    symbols = [name + (" as " + name if export else "") for name in sorted(names, key=symbol_order)]
    return f"from {module} import (\n" + "".join("    " + name + ",\n" for name in symbols) + ")\n"


def module_source(rows: list[dict], owners: dict[str, str]) -> str:
    local = {row["name"] for row in rows}
    bases = {"scalar": "_Scalar", "record": "_Record", "union": "_Union", "list": "_List", "optional": "_Optional"}
    required = {"_Boolean" if row.get("encoding") == "bool" else bases[row["kind"]] for row in rows} | {"_register"}
    if any(row.get("encoding") == "finite-f64-bits" for row in rows):
        required.update({"_finite_float_bits", "_float_from_bits"})
    references = {field["type"] for row in rows for field in row.get("fields", [])} - local
    typing = []
    if references:
        typing.append("TYPE_CHECKING")
    if any(row["kind"] in {"record", "union"} for row in rows):
        typing.append("ClassVar")
    if any(row.get("unit") == "unix-microseconds" or row.get("encoding") == "finite-f64-bits" for row in rows):
        typing.append("Self")
    output = ['# Generated by scripts/generate_node_contract.py. Decoded values are untrusted data.\nfrom __future__ import annotations\n\n']
    if any(row["kind"] == "record" for row in rows):
        output.append("from dataclasses import dataclass\n")
    if typing:
        output.append(imported("typing", typing))
    output.append("\n" + imported("._codec", sorted(required)))
    if references:
        output.append("\nif TYPE_CHECKING:\n")
        by_owner = defaultdict(list)
        for name in references:
            by_owner[owners[name]].append(name)
        for owner, names in sorted(by_owner.items()):
            output.append("\n".join("    " + line for line in imported("._" + owner, names).splitlines()) + "\n")
    return "".join(output) + "\n\n" + "\n\n".join(python.render_type(row) for row in rows) + "\n"


def render_package(root: Path, meta: dict, rows: list[dict], digest: str) -> dict[Path, str]:
    public = [row for row in rows if row["visibility"] == "public"]
    owners = {row["name"]: group(row["name"]) for row in public}
    groups = defaultdict(list)
    for row in public:
        groups[owners[row["name"]]].append(row)
    path = root / "src/poolhouse/api/node_contract"
    outputs = {path / "_codec.py": python.HEADER + python.REGISTRY}
    facade = ['# Generated by scripts/generate_node_contract.py. Decoded values are untrusted data.\nfrom . import (\n']
    for owner in sorted(groups):
        facade.append(f"    _{owner} as _{owner}_module,\n")
    facade.append(")\n")
    imports = []
    for owner, members in sorted(groups.items()):
        outputs[path / ("_" + owner + ".py")] = module_source(members, owners)
        imports.append(imported("._" + owner, [row["name"] for row in members]))
    facade.append("from ._codec import _finalize\n\n")
    facade.append(f'CONTRACT_VERSION = {meta["version"]}\nSCHEMA_SHA256 = "{digest}"\n')
    namespaces = ", ".join("_" + owner + "_module.__dict__" for owner in sorted(groups))
    exported = ["CONTRACT_VERSION", "SCHEMA_SHA256", *(row["name"] for row in public)]
    exports = pprint.pformat(tuple(sorted(exported, key=lambda value: symbol_order(value, fold=False))), width=100, compact=True)
    facade.append("_EXPORTS = " + exports + "\n")
    facade.append('_finalize(tuple(name for name in _EXPORTS if name not in {"CONTRACT_VERSION", "SCHEMA_SHA256"}),\n'
                  f'          (globals(), {namespaces},))\n')
    facade.append("__all__: tuple[str, ...]\nglobals().update(__all__=_EXPORTS)\n")
    outputs[path / "__init__.py"] = "".join(facade)
    stub = "# Generated public interface; runtime exports are validated and frozen before publication.\n"
    stub += "from typing import Literal as _Literal\n\n" + "".join(imports)
    stub += f'\nCONTRACT_VERSION: _Literal[{meta["version"]}]\nSCHEMA_SHA256: _Literal["{digest}"]\n'
    outputs[path / "__init__.pyi"] = stub + "__all__ = " + exports + "\n"
    return outputs
