"""Render only the closed native declaration grammar; never emit a proof issuer."""
from __future__ import annotations


def _index(rows):
    return {row["name"]: row for row in rows}


class Renderer:
    def __init__(self, source):
        self.source = source
        self.projections = _index(source["projection"])
        self.associations = _index(source["graph"]["associated"])
        self.effects = _index(source["effect_owner"]["associated"])
        self.leaves = _index(source["leaf"])
        self.views = _index(source["view"])
        self.retained = _index(source["representation"])
        self.phases = _index(source["phase"])
        self.roles = {row["role"]: "G::" + row["name"] for row in self.associations.values()}
        self.roles.update({row["role"]: row["name"] + "<G>" for row in self.phases.values()})

    def kind(self, name, lifetime="'_"):
        if name in self.associations:
            return "G::" + name
        if name in self.effects:
            return "E::" + name
        if name in self.phases or name in self.retained:
            return name + "<G>"
        if name in self.views:
            return name + "<" + lifetime + ">"
        return {"bytes": "u8", "bytes32": "[u8; 32]"}.get(name, name)

    def result(self, getter, lifetime=None):
        base = self.kind(getter["result"], lifetime or "'_")
        ownership = getter["ownership"]
        if ownership == "value":
            return base
        if ownership == "borrow-slice":
            base = "[" + base + "]"
        result = "&" + (lifetime + " " if lifetime else "") + base
        return "Option<" + result + ">" if getter["optional"] else result

    def getter_signature(self, getter):
        return "fn " + getter["name"] + "(&self) -> " + self.result(getter)

    def data_storage(self, field):
        base = "Vec<u8>" if field["type"] == "bytes" else self.kind(field["type"])
        return "Option<" + base + ">" if field["optional"] else base

    def leaves_source(self):
        output = []
        for name, row in self.leaves.items():
            kind = row["kind"]
            if kind in {"bytes", "bytes32"}:
                base = "Vec<u8>" if kind == "bytes" else "[u8; 32]"
                returned = "[u8]" if kind == "bytes" else base
                output += [f"#[derive(Debug)] pub(crate) struct {name}({base});",
                           f"impl {name} {{ pub(crate) fn bytes(&self) -> &{returned} {{ &self.0 }} }}"]
            elif kind == "record":
                fields = ", ".join(field["name"] + ": " + self.data_storage(field) for field in row["fields"])
                output += [f"#[derive(Debug)] pub(crate) struct {name} {{ {fields} }}", f"impl {name} {{"]
                for field in row["fields"]:
                    field_name, field_type = field["name"], field["type"]
                    if field_type in {"u32", "u64"}:
                        returned, body = field_type, "self." + field_name
                    elif field["optional"]:
                        returned, body = "Option<&" + self.kind(field_type) + ">", "self." + field_name + ".as_ref()"
                    else:
                        returned = "&[u8]" if field_type == "bytes" else "&" + self.kind(field_type)
                        body = "&self." + field_name
                    output.append(f"pub(crate) fn {field_name}(&self) -> {returned} {{ {body} }}")
                output.append("}")
            else:
                derive = "Clone, Copy, Debug, PartialEq, Eq" if row.get("copy") else "Debug"
                output += [f"#[derive({derive})] pub(crate) enum {name} {{"]
                for variant in row["variants"]:
                    fields = variant["fields"]
                    payload = " { " + ", ".join(field["name"] + ": " + self.data_storage(field) for field in fields) + " }" if fields else ""
                    if variant.get("style") == "tuple":
                        payload = "(" + ", ".join(self.data_storage(field) for field in fields) + ")"
                    output.append(variant["name"] + payload + ",")
                output.append("}")
        return output

    def views_source(self):
        output = []
        for name, row in self.views.items():
            if row["kind"] == "borrowed-record":
                fields = ", ".join(field["name"] + ": " + self.result(field, "'a") for field in row["fields"])
                output += [f"pub(crate) struct {name}<'a> {{ {fields} }}", f"impl<'a> {name}<'a> {{"]
                for field in row["fields"]:
                    output.append(f'pub(crate) fn {field["name"]}(&self) -> {self.result(field, chr(39) + "a")} {{ self.{field["name"]} }}')
                output.append("}")
            else:
                output += [f"pub(crate) enum {name}<'a> {{"]
                output.extend(f'{variant["name"]}({variant["type"]}<\'a>),' for variant in row["variants"])
                output.append("}")
        return output

    def retained_source(self):
        output = []
        for name, row in self.retained.items():
            if row["kind"] == "shared-resource":
                output += [f"enum {name}Owner<G: NativeGraph> {{"]
                output.extend(f'{variant["name"]}(Arc<{self.kind(variant["type"])}>),' for variant in row["variants"])
                output += ["}", f"pub(crate) struct {name}<G: NativeGraph> {{ owner: {name}Owner<G> }}",
                           f"pub(crate) enum {name}View<'a, G: NativeGraph> {{"]
                output.extend(f'{variant["name"]}(&\'a {self.kind(variant["type"])}),' for variant in row["variants"])
                output += ["}", f"impl<G: NativeGraph> {name}<G> {{",
                           f"pub(crate) fn view(&self) -> {name}View<'_, G> {{ match &self.owner {{"]
                output.extend(f'{name}Owner::{variant["name"]}(owner) => {name}View::{variant["name"]}(owner.as_ref()),' for variant in row["variants"])
                output += ["} }", "}"]
                continue
            fields, methods = [], []
            for field in row["fields"]:
                field_name, base = field["name"], self.kind(field["result"])
                if field["ownership"] == "borrow-slice":
                    storage, body = "Vec<" + base + ">", "&self." + field_name
                elif row["kind"] == "shared-record" and field["ownership"] == "borrow":
                    storage, body = "Arc<" + base + ">", "self." + field_name + ".as_ref()"
                else:
                    storage = base
                    body = ("" if field["ownership"] == "value" else "&") + "self." + field_name
                fields.append(field_name + ": " + storage)
                methods.append("pub(crate) " + self.getter_signature(field) + " { " + body + " }")
            output += [f"pub(crate) struct {name}<G: NativeGraph> {{ " + ", ".join(fields) + " }",
                       f"impl<G: NativeGraph> {name}<G> {{", *methods, "}"]
        return output

    def graphs_source(self):
        output = ["pub(crate) trait NativeGraph: native_seal::Graph + Sized {"]
        for row in self.associations.values():
            projection = row["projection"]
            bound = projection + ("" if self.projections[projection]["graph"] == "none" else "<Self>")
            output.append(f'type {row["name"]}: {bound};')
        output += ["}", "pub(crate) trait EffectGraph<G: NativeGraph>: effect_seal::Graph + Sized {"]
        for row in self.effects.values():
            bound = row["projection"] + ("<G>" if self.projections[row["projection"]]["graph"] == "native" else "<G, Self>")
            output.append(f'type {row["name"]}: {bound};')
        output.append("}")
        for name, row in self.projections.items():
            parameters = {"none": "", "native": "<G: NativeGraph>", "effect": "<G: NativeGraph, E: EffectGraph<G>>"}[row["graph"]]
            scope = "effect_seal" if row["seal_scope"] == "effect" else "native_seal"
            extra = "ChildProjection<G> + " if name in {"HeldProjection", "RunningProjection"} else ""
            output.append(f'pub(crate) trait {name}{parameters}: {extra}{scope}::{row["seal"]} {{')
            output.extend(self.getter_signature(getter) + ";" for getter in row["getters"])
            output.append("}")
        return output

    def phases_source(self):
        output = []
        child = self.projections["ChildProjection"]
        for row in self.phases.values():
            name = row["name"]
            seal = self.projections[row["projection"]]["seal"]
            output += [f"pub(crate) struct {name}<G: NativeGraph> {{ inner: G::ChildCustody }}",
                       f"impl<G: NativeGraph> native_seal::Child for {name}<G> {{}}",
                       f"impl<G: NativeGraph> native_seal::{seal} for {name}<G> {{}}",
                       f"impl<G: NativeGraph> ChildProjection<G> for {name}<G> {{"]
            output.extend(self.getter_signature(getter) + " { self.inner." + getter["name"] + "() }" for getter in child["getters"])
            output += ["}", f'impl<G: NativeGraph> {row["projection"]}<G> for {name}<G> {{',
                       f'fn phase(&self) -> {row["phase"]} {{ {row["phase"]}::{row["variant"]} }}', "}"]
        return output

    def operations_source(self):
        output = ["pub(crate) trait NativeCallerPlatform<G: NativeGraph>: native_seal::Platform {"]
        for method in self.source["method"]:
            arguments = [arg["name"] + ": " + ("&" if arg["ownership"] == "borrow" else "") + self.roles[arg["type"]] for arg in method["arguments"]]
            result = self.roles.get(method["result"], method["result"])
            failure = self.roles.get(method["failure"], method["failure"])
            suffix = ", " + ", ".join(arguments) if arguments else ""
            output.append(f'fn {method["name"]}(&mut self{suffix}) -> Result<{result}, {failure}>;')
        output += ["}", "pub(crate) trait NodeEffectOwner<G: NativeGraph, E: EffectGraph<G>>: effect_seal::Owner {",
                   self.getter_signature(self.source["effect_owner"]["identity"]) + ";",
                   "fn execute(&mut self, permit: E::Permit, operation: E::Operation) -> Result<E::Completion, E::Unknown>;", "}"]
        return output

    def render(self):
        output = ["// Dormant private declarations: no issuer, bridge, platform implementation or durable ledger.",
                  "mod native_projection {", "use std::sync::Arc;", "use super::{NativeAdapterError, ProcessObservation};",
                  "use super::super::contract::{AuditToken, InstallationId, ResourceGeneration, WindowsSid};"]
        for scope, base in (("native", {"Graph", "Platform"}), ("effect", {"Graph", "Owner"})):
            seals = base | {row["seal"] for row in self.projections.values() if row["seal_scope"] == scope}
            output += [f"mod {scope}_seal {{", *("pub trait " + name + " {}" for name in sorted(seals)), "}"]
        output += self.leaves_source() + self.views_source() + self.graphs_source()
        output += self.retained_source() + self.phases_source() + self.operations_source()
        output += ["pub(crate) const UNRESOLVED_NATIVE_PROFILES: &[(&str, &[&str])] = &["]
        for profile in self.source["profile"]:
            fields = ", ".join('"' + name + '"' for name in profile["fields"])
            output.append('("' + profile["name"] + '", &[' + fields + ']),')
        output.append("];")
        output += ["}", "pub(crate) use native_projection::*;", ""]
        return "\n".join(output)


def render(source: dict) -> str:
    return Renderer(source).render()
