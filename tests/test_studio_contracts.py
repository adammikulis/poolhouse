"""Studio graph contracts and inert package boundaries."""
import math

import pytest

from poolhouse.studio.catalog import node_catalog
from poolhouse.studio.contracts import validate_graph
from poolhouse.studio.packages import content_digest, export_package, import_package


def graph():
    return {"nodes": [{"id": "data", "type": "dataset", "bindings": {"dataset": {"kind": "dataset", "id": "examples.jsonl"}}},
                      {"id": "train", "type": "train.classify-text"}],
            "edges": [{"source": {"node": "data", "port": "dataset"}, "target": {"node": "train", "port": "dataset"}}]}


def test_recipe_fields_and_artifact_wiring():
    specs = {item["type"]: item for item in node_catalog()}
    assert specs["train.classify-text"]["parameters"]["properties"]["steps"]["minimum"] == 20
    result = validate_graph(graph())
    assert result["valid"]
    assert result["graph"]["nodes"][1]["parameters"]["steps"] == 1000
    value = graph()
    value["edges"][0]["source"]["port"] = "unknown"
    assert not validate_graph(value)["valid"]


def test_smoke_unknown_fields_dependencies_and_revocation():
    value = graph()
    value["nodes"][1]["parameters"] = {"mode": "smoke"}
    value["nodes"].append({"id": "out", "type": "export"})
    value["edges"].append({"source": {"node": "train", "port": "model"}, "target": {"node": "out", "port": "model"}})
    assert any("Smoke" in e["message"] for e in validate_graph(value)["errors"])
    assert any(e["code"] == "dependency" for e in validate_graph(graph(), capabilities=set())["errors"])
    assert any(e["code"] == "unauthorized" for e in validate_graph(graph(), authorize=lambda _: False)["errors"])
    value = graph()
    value["nodes"][1]["parameters"] = {"env": {"TOKEN": "secret"}}
    assert not validate_graph(value)["valid"]
    value["nodes"][1]["parameters"] = {"learning_rate": math.nan}
    assert not validate_graph(value)["valid"]


def test_groups_expand_and_recursive_groups_refused():
    value = graph()
    inner = value["nodes"].pop()
    value["groups"] = {"recipe": {"nodes": [inner], "edges": [], "inputs": {"data": {"node": "train", "port": "dataset"}},
                                  "outputs": {"model": {"node": "train", "port": "model"}},
                                  "parameters": {"steps": {"node": "train", "parameter": "steps"}}}}
    value["nodes"].append({"id": "group", "type": "group", "group": "recipe", "parameters": {"steps": 20}})
    value["edges"][0]["target"] = {"node": "group", "port": "data"}
    result = validate_graph(value)
    assert result["valid"]
    assert result["graph"]["nodes"][1]["id"] == "group/train"
    assert result["graph"]["nodes"][1]["parameters"]["steps"] == 20
    value["groups"]["recipe"]["nodes"] = [{"id": "loop", "type": "group", "group": "recipe"}]
    assert not validate_graph(value)["valid"]


def test_portable_round_trip_excludes_private_content():
    value = graph()
    value["nodes"][1]["parameters"] = {"steps": 20}
    value["layout"] = {"private": "/home/private"}
    package = export_package(value, package_id="example", version="1", name="Example")
    assert import_package(package) == package
    assert package["graph"]["nodes"][0]["bindings"] == {}
    assert "/home/private" not in str(package)
    assert "examples.jsonl" not in str(package)
    package["graph"]["nodes"][0]["bindings"] = {"dataset": {"kind": "dataset", "id": "/private/data"}}
    _resign(package)
    with pytest.raises(ValueError, match="private"):
        import_package(package)


def _resign(package):
    package.pop("digest")
    package["digest"] = content_digest(package)


def test_package_digest_substitution_and_executable_fields():
    package = export_package(graph(), package_id="example", version="1", name="Example")
    package["dependencies"][0]["digest"] = "0" * 64
    _resign(package)
    with pytest.raises(ValueError, match="dependencies"):
        import_package(package)
    package = export_package(graph(), package_id="example", version="1", name="Example")
    package["graph"]["nodes"][1]["argv"] = ["malicious"]
    _resign(package)
    with pytest.raises(ValueError, match="private"):
        import_package(package)


def test_free_text_is_not_exported():
    value = {"nodes": [{"id": "prompt", "type": "prompt", "parameters": {"template": "secret /private/path"}}], "edges": []}
    package = export_package(value, package_id="example", version="1", name="Example")
    assert package["graph"]["nodes"][0]["parameters"] == {}
