from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def _to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True)
    if hasattr(obj, "dict"):
        return obj.dict(by_alias=True)
    raise TypeError(f"Unsupported architecture type: {type(obj).__name__}")


def _build_context(architecture: Any) -> Dict[str, Any]:
    arch = _to_dict(architecture)

    domain_plan = arch.get("domain_plan", {}) or {}
    routers = domain_plan.get("routers", {}) or {}
    router_links = domain_plan.get("router_links", []) or []
    connectivity_mode = domain_plan.get("connectivity_mode", "none") or "none"

    components = arch.get("components", []) or []
    component_by_id = {
        c.get("id"): c
        for c in components
        if isinstance(c, dict) and c.get("id")
    }

    real_router_ids = []
    inferred_router_ids = []
    for rid in sorted(routers.keys()):
        comp = component_by_id.get(rid, {}) or {}
        # Inferred missing-router domains are marked by addressing.py as interfaces=0.
        # Real routers from the user prompt have interfaces None or >0.
        if comp.get("type") == "router" and comp.get("interfaces") == 0:
            inferred_router_ids.append(rid)
        else:
            real_router_ids.append(rid)

    router_interface_subnets = {}
    for rid, router in routers.items():
        subnets = router.get("subnets", []) or []
        router_interface_subnets[rid] = [
            s.get("name")
            for s in subnets
            if isinstance(s, dict) and s.get("name")
        ]

    def _edge_endpoints(edge: Any):
        if isinstance(edge, dict):
            for a_key, b_key in [
                ("source", "target"),
                ("from", "to"),
                ("src", "dst"),
                ("a", "b"),
                ("node1", "node2"),
                ("u", "v"),
            ]:
                if edge.get(a_key) and edge.get(b_key):
                    return str(edge.get(a_key)), str(edge.get(b_key))
            eps = edge.get("endpoints")
            if isinstance(eps, (list, tuple)) and len(eps) >= 2:
                return str(eps[0]), str(eps[1])
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            return str(edge[0]), str(edge[1])
        return None, None

    router_edge_counts = {rid: 0 for rid in routers.keys()}
    for edge in arch.get("edges", []) or []:
        a, b = _edge_endpoints(edge)
        if not a or not b:
            continue
        if a in router_edge_counts:
            router_edge_counts[a] += 1
        if b in router_edge_counts:
            router_edge_counts[b] += 1

    # Router instance sizing rule:
    # choose the router EC2 class by the number of physical edges/interfaces.
    # Fall back to the number of planned router subnets if edge data is incomplete.
    def _router_instance_type(edge_count: int, subnet_count: int) -> str:
        n = max(edge_count or 0, subnet_count or 0, 1)
        if n <= 2:
            return "t3.micro"
        if n == 3:
            return "t3.small"
        if n <= 5:
            return "t3.medium"
        return "t3.large"

    router_instance_types = {}
    router_interface_counts = {}
    for rid in routers.keys():
        subnet_count = len(router_interface_subnets.get(rid, []))
        edge_count = router_edge_counts.get(rid, 0)
        router_interface_counts[rid] = max(edge_count or 0, subnet_count or 0, 1)
        router_instance_types[rid] = _router_instance_type(edge_count, subnet_count)

    has_pc1 = any(
        c.get("id", "").lower() == "pc1" and c.get("type") == "pc"
        for c in components
        if isinstance(c, dict)
    )

    return {
        "architecture": arch,
        "arch": arch,
        "domain_plan": domain_plan,
        "routers": routers,
        "router_links": router_links,
        "connectivity_mode": connectivity_mode,
        "components": components,
        "component_by_id": component_by_id,
        "real_router_ids": real_router_ids,
        "inferred_router_ids": inferred_router_ids,
        "router_interface_subnets": router_interface_subnets,
        "router_edge_counts": router_edge_counts,
        "router_interface_counts": router_interface_counts,
        "router_instance_types": router_instance_types,
        "has_pc1": has_pc1,
        "edges": arch.get("edges", []) or [],
        "addressing": arch.get("addressing", {}) or {},
        "firewall_policy": arch.get("firewall_policy", {}) or {},
        "user_policies": arch.get("user_policies", {}) or {},
    }


def _render_template(env: Environment, template_name: str, context: Dict[str, Any]) -> str:
    template = env.get_template(template_name)
    return template.render(**context)


def render_project(architecture: Any, templates_dir: str, out_dir: str) -> Dict[str, str]:
    templates_path = Path(templates_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(templates_path)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    context = _build_context(architecture)

    template_to_output = {
        "main.tf.j2": "main.tf",
        "variables.tf.j2": "variables.tf",
        "outputs.tf.j2": "outputs.tf",
        "terraform.tfvars.example.j2": "terraform.tfvars.example",
        "README.md.j2": "README.md",
    }

    written: Dict[str, str] = {}
    for template_name, output_name in template_to_output.items():
        if not (templates_path / template_name).exists():
            continue
        rendered = _render_template(env, template_name, context)
        out_file = output_path / output_name
        out_file.write_text(rendered, encoding="utf-8")
        written[output_name] = str(out_file)

    return written
