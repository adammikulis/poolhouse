"""Server-owned Studio node specifications."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from poolhouse.contracts import recipes


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "additionalProperties": False}


def _node(kind: str, title: str, family: str, inputs: dict[str, str],
          outputs: dict[str, str], **options: Any) -> dict[str, Any]:
    return {"type": kind, "version": 1, "title": title, "family": family,
            "inputs": [{"name": k, "type": v, "required": True} for k, v in inputs.items()],
            "outputs": [{"name": k, "type": v} for k, v in outputs.items()],
            "parameters": _schema(options.pop("properties", {})), "capabilities": [], "bindings": {}, "adapter": kind,
            "side_effects": False, "implementation": f"poolhouse.studio/{kind}/1", **options}


def node_catalog() -> list[dict[str, Any]]:
    """Return the fixed node catalogue with training fields from recipe contracts."""
    text = {"type": "string", "default": ""}
    catalog = [
        _node("dataset", "Dataset", "Inputs", {}, {"dataset": "dataset"}),
        _node("model", "Model", "Inputs", {}, {"model": "model"}),
        _node("data.validate", "Validate data", "Data", {"dataset": "dataset"}, {"dataset": "dataset", "report": "report"}),
        _node("data.split", "Split data", "Data", {"dataset": "dataset"}, {"train": "dataset", "test": "dataset"},
              properties={"fraction": {"type": "number", "default": 0.8, "exclusiveMinimum": 0, "exclusiveMaximum": 1},
               "seed": {"type": "integer", "default": 0}}),
        _node("prompt", "Prompt template", "Inference", {}, {"prompt": "text"}, properties={"template": text}),
        _node("generate", "Generate", "Inference", {"model": "model", "prompt": "text"}, {"text": "text", "response": "json"},
              properties={"temperature": {"type": "number", "default": 0.7, "minimum": 0, "maximum": 2}}, capabilities=["inference"]),
        _node("evaluate", "Evaluate model", "Evaluation", {"model": "model", "dataset": "dataset"}, {"metrics": "metrics", "report": "report"},
              properties={"suite": {"type": "string", "default": "holdout"}}, capabilities=["evaluation"]),
        _node("compare", "Compare metrics", "Results", {"base": "metrics", "trained": "metrics"}, {"metrics": "metrics", "report": "report"}),
        _node("chart", "Metrics chart", "Results", {"metrics": "metrics"}, {"report": "report"}),
        _node("export", "Export model", "Results", {"model": "model"}, {"artifact": "artifact"},
              properties={"format": {"type": "string", "enum": ["checkpoint", "adapter"], "default": "checkpoint"}}),
        _node("report", "Report", "Results", {"report": "report"}, {"artifact": "artifact"}),
        _node("tools.call", "Authorized tool", "Inference", {"arguments": "json"}, {"result": "json"},
              properties={"tool": text}, capabilities=["tools"], side_effects=True),
        _node("data.synthesize", "Synthesize tool examples", "Data", {"model": "model", "dataset": "dataset"}, {"dataset": "dataset"}, capabilities=["inference"]),
        _node("data.harvest", "Harvest benchmark traces", "Data", {}, {"dataset": "dataset"}),
        _node("gym.recordings", "Reviewed Gym recordings", "Data", {}, {"dataset": "dataset"}, capabilities=["gym"]),
        _node("gym.train", "Gym policy training", "Training", {"dataset": "dataset"}, {"model": "model", "metrics": "metrics"}, capabilities=["gym"], side_effects=True),
        _node("gym.evaluate", "Evaluate Gym policy", "Evaluation", {"model": "model"}, {"metrics": "metrics", "report": "report"}, capabilities=["gym"]),
    ]
    catalog.extend([
        _node("generate.structured", "Structured response", "Inference", {"model": "model", "prompt": "text"},
              {"text": "text", "response": "json"}, properties={"temperature": {"type": "number", "default": 0.7, "minimum": 0, "maximum": 2},
              "json_schema": {"type": "string", "default": '{"type":"object"}'}}, capabilities=["inference"]),
        _node("train.decision", "Train decision model", "Training", {"model": "model", "dataset": "dataset"},
              {"model": "model", "metrics": "metrics", "checkpoint": "artifact"}, properties={
              "steps": {"type": "integer", "default": 60, "minimum": 1}, "batch_size": {"type": "integer", "default": 4, "minimum": 1},
              "lr": {"type": "number", "default": 0.0002, "exclusiveMinimum": 0}, "max_tokens": {"type": "integer", "default": 1024, "minimum": 1}},
              capabilities=["training"], side_effects=True),
        _node("evaluate.decision", "Evaluate decision model", "Evaluation", {"model": "model", "dataset": "dataset"},
              {"metrics": "metrics", "report": "report"}, capabilities=["evaluation"]),
    ])
    for recipe in recipes():
        fields: dict[str, Any] = {}
        for field in recipe.get("fields", []):
            item = {"type": {"int": "integer", "float": "number", "bool": "boolean", "text": "string"}[field["type"]],
                    "title": field.get("label", field["name"]), "default": field.get("default")}
            for before, after in (("min", "minimum"), ("max", "maximum")):
                if before in field:
                    item[after] = field[before]
            fields[field["name"]] = item
        fields.update({"size": {"type": "string", "enum": ["", *recipe.get("sizes", {})], "default": ""},
                       "framework": {"type": "string", "enum": ["", "torch", "mlx"], "default": ""},
                       "mode": {"type": "string", "enum": ["train", "smoke"], "default": "train"}})
        catalog.append(_node(f"train.{recipe['id']}", recipe["title"], "Training", {"dataset": "dataset"},
                             {"model": "model", "metrics": "metrics", "checkpoint": "artifact"}, properties=fields,
                             capabilities=["training"], side_effects=True, recipe=recipe["id"]))
    for spec in catalog:
        if spec["type"] in ("dataset", "model", "data.harvest", "gym.recordings"):
            resource = {"data.harvest": "traces", "gym.recordings": "recordings"}.get(spec["type"], spec["type"])
            spec["bindings"] = {resource: {"kind": resource, "required": True}}
        if spec["type"] == "data.harvest":
            spec["bindings"] = {"traces": {"kind": "dataset", "required": True}}
        if spec["type"] == "gym.recordings":
            spec["bindings"] = {"trajectory": {"kind": "dataset", "required": True}, "reviews": {"kind": "dataset", "required": True}}
        if spec["type"] == "gym.train":
            spec["parameters"] = _schema({"environment": {"type": "string", "enum": ["", "drone", "car", "traffic-driving", "warehouse", "traffic"], "default": ""},
                "timesteps": {"type": "integer", "default": 2048, "minimum": 1}, "seed": {"type": "integer", "default": 0}})
        if spec["type"] == "gym.evaluate":
            spec["parameters"] = _schema({"episodes": {"type": "integer", "default": 5, "minimum": 1}})
        if spec["type"].startswith("train.") and spec["type"] != "train.decision":
            spec["inputs"].append({"name": "model", "type": "model", "required": False})
    return deepcopy(catalog)


def node_spec(kind: str, version: int = 1) -> dict[str, Any]:
    """Return a server-owned node version or reject an unknown implementation."""
    if type(version) is not int:
        raise ValueError("Node version must be an integer")
    for spec in node_catalog():
        if spec["type"] == kind and spec["version"] == version:
            return spec
    raise ValueError(f"Unknown node version: {kind}@{version}")
