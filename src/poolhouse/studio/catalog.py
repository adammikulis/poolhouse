"""Server-owned Studio node specifications."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from poolhouse.contracts import recipes

MODEL_TYPES = frozenset({"model", "language-model", "classifier-model", "decision-model", "gym-policy"})


def is_model_type(kind: str) -> bool:
    """Return whether a declared port carries a supported model resource."""
    return kind in MODEL_TYPES


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
        _node("stream.record", "Record", "Inputs", {}, {"record": "json"},
              properties={"sample": {"type": "string", "default": "{}"}}),
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
              properties={"suite": {"type": "string", "enum": ["holdout", "tool-calls"], "default": "holdout"},
                          "recipe": {"type": "string", "enum": ["classify-text", "text-lm", "tool-calls"], "default": "tool-calls"},
                          "framework": {"type": "string", "enum": ["", "mlx", "torch"], "default": ""},
                          "temperature": {"type": "number", "default": 0, "minimum": 0, "maximum": 2}}, capabilities=["evaluation"]),
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
        _node("gym.train", "Gym policy training", "Training", {}, {"model": "model", "metrics": "metrics"}, capabilities=["gym"], side_effects=True),
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
    decision_properties = {
        "question": {"type": "string", "default": "Which option best describes this record?"},
        "options": {"type": "string", "default": '["accept","review"]'},
        "abstain_below": {"type": "number", "default": 0.8, "minimum": 0, "maximum": 1},
        "min_margin": {"type": "number", "default": 0.2, "minimum": 0, "maximum": 1},
    }
    catalog.extend([
        _node("decision.predict", "Decide on a record", "Inference", {"record": "json", "model": "decision-model"},
              {"decision": "decision", "record": "json", "report": "report"}, properties=decision_properties,
              capabilities=["inference"]),
        _node("decision.route", "Route a decision", "Inference", {"decision": "decision"},
              {"allow": "gate", "review": "gate", "block": "gate", "report": "report"},
              properties={"label": {"type": "string", "default": "accept"}}),
        _node("guard.injection", "Assess prompt injection", "Inference", {"record": "json", "model": "decision-model"},
              {"signal": "json", "record": "json", "report": "report", "allow": "gate", "review": "gate", "block": "gate"},
              properties={"mode": {"type": "string", "enum": ["direct", "indirect"], "default": "indirect"},
                          "trusted_task": {"type": "string", "default": ""},
                          "allow_threshold": {"type": "number", "default": 0.8, "minimum": 0.5, "maximum": 1},
                          "block_threshold": {"type": "number", "default": 0.8, "minimum": 0.5, "maximum": 1},
                          "min_margin": {"type": "number", "default": 0.2, "minimum": 0, "maximum": 1}},
              capabilities=["inference"]),
    ])
    for recipe in recipes():
        fields: dict[str, Any] = {}
        for field in recipe.get("fields", []):
            if field["name"] == "base":
                continue
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
        if spec["type"] in ("generate", "generate.structured"):
            spec["parameters"]["properties"]["max_tokens"] = {"type": "integer", "default": 512, "minimum": 1, "maximum": 32768}
        if spec["type"] in ("dataset", "model", "data.harvest", "gym.recordings"):
            resource = {"data.harvest": "traces", "gym.recordings": "recordings"}.get(spec["type"], spec["type"])
            spec["bindings"] = {resource: {"kind": resource, "required": True}}
        if spec["type"] == "data.harvest":
            spec["bindings"] = {"traces": {"kind": "dataset", "required": True}}
        if spec["type"] == "gym.recordings":
            spec["bindings"] = {"trajectory": {"kind": "dataset", "required": True}, "reviews": {"kind": "dataset", "required": True}}
        if spec["type"] == "gym.train":
            spec["parameters"] = _schema({"environment": {"type": "string", "enum": ["warehouse", "drone", "car", "traffic-driving", "traffic"], "default": "warehouse"},
                "timesteps": {"type": "integer", "default": 2048, "minimum": 1}, "seed": {"type": "integer", "default": 0}})
            spec["parameters"]["required"] = ["environment"]
        if spec["type"] == "gym.evaluate":
            spec["parameters"] = _schema({"episodes": {"type": "integer", "default": 5, "minimum": 1}})
        if spec["type"].startswith("train.") and spec["type"] != "train.decision":
            spec["inputs"].append({"name": "model", "type": "model", "required": spec["type"] == "train.tool-calls"})
    _model_ports(catalog)
    return deepcopy(catalog)


def _model_ports(catalog: list[dict]) -> None:
    outputs = {"train.classify-text": "classifier-model", "train.text-lm": "language-model",
               "train.tool-calls": "language-model", "train.decision": "decision-model", "gym.train": "gym-policy"}
    accepted = {"evaluate": ["model", "language-model", "classifier-model"],
                "evaluate.decision": ["model", "decision-model"],
                "decision.predict": ["model", "decision-model"], "guard.injection": ["model", "decision-model"], "gym.evaluate": ["model", "gym-policy"],
                "export": ["model", "language-model", "classifier-model"]}
    for spec in catalog:
        for port in spec["outputs"]:
            if port["name"] == "model":
                port["type"] = outputs.get(spec["type"], "model")
        for port in spec["inputs"]:
            if port["name"] == "model":
                port["accepted_types"] = accepted.get(spec["type"], ["model", "language-model"])


def node_spec(kind: str, version: int = 1) -> dict[str, Any]:
    """Return a server-owned node version or reject an unknown implementation."""
    if type(version) is not int:
        raise ValueError("Node version must be an integer")
    for spec in node_catalog():
        if spec["type"] == kind and spec["version"] == version:
            return spec
    raise ValueError(f"Unknown node version: {kind}@{version}")
