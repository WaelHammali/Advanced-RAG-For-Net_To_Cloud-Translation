from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple


REQUIRED_RESPONSE_ORDER = [
    "Interpretation",
    "Mapping Table",
    "AWS Design Choice",
    "CIDR Plan",
    "SSH Access Plan",
    "Terraform Code",
    "Outputs",
    "Notes / Assumptions",
]


def _components_by_type(architecture: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for c in architecture.get("components", []) or []:
        cid = c.get("id")
        ctype = c.get("type")
        if isinstance(cid, str) and isinstance(ctype, str):
            out[cid] = ctype
    return out


def _edges(architecture: Dict[str, Any]) -> List[Tuple[str, str]]:
    out = []
    for e in architecture.get("edges", []) or []:
        a = e.get("from")
        b = e.get("to")
        if isinstance(a, str) and isinstance(b, str):
            out.append((a, b))
    return out


def _router_domains(architecture: Dict[str, Any]) -> Dict[str, Any]:
    domain_plan = architecture.get("domain_plan", {}) or {}
    return domain_plan.get("routers", {}) or {}


def _router_links(architecture: Dict[str, Any]) -> List[Tuple[str, str]]:
    domain_plan = architecture.get("domain_plan", {}) or {}
    raw = domain_plan.get("router_links", []) or []
    out = []
    for pair in raw:
        if isinstance(pair, list) and len(pair) == 2:
            a, b = pair
            if isinstance(a, str) and isinstance(b, str):
                out.append((a, b))
    return out


def _switch_to_router_from_compiled(architecture: Dict[str, Any]) -> Dict[str, str]:
    mapping = {}
    for rid, router in _router_domains(architecture).items():
        for subnet in router.get("subnets", []) or []:
            sw = subnet.get("switch")
            if isinstance(sw, str):
                mapping[sw] = rid
    return mapping


def _hosts_from_subnets(architecture: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for _, router in _router_domains(architecture).items():
        for subnet in router.get("subnets", []) or []:
            for hp in subnet.get("host_placements", []) or []:
                hid = hp.get("host_id")
                if isinstance(hid, str):
                    out.add(hid)
    return out


def _public_host_count(architecture: Dict[str, Any]) -> int:
    count = 0
    for _, router in _router_domains(architecture).items():
        for subnet in router.get("subnets", []) or []:
            for hp in subnet.get("host_placements", []) or []:
                if hp.get("exposure") == "public":
                    count += 1
    return count


def _public_host_ids(architecture: Dict[str, Any]) -> List[str]:
    out = []
    for _, router in _router_domains(architecture).items():
        for subnet in router.get("subnets", []) or []:
            for hp in subnet.get("host_placements", []) or []:
                if hp.get("exposure") == "public" and isinstance(hp.get("host_id"), str):
                    out.append(hp["host_id"])
    return sorted(set(out))


def _host_count(architecture: Dict[str, Any]) -> int:
    comps = _components_by_type(architecture)
    return sum(1 for _, ctype in comps.items() if ctype in {"pc", "server"})


def _pem_expected_count(architecture: Dict[str, Any]) -> int:
    return _host_count(architecture)


def _has_firewall(architecture: Dict[str, Any]) -> bool:
    comps = _components_by_type(architecture)
    return any(v == "firewall" for v in comps.values())


def _allow_auto_addressing(architecture: Dict[str, Any]) -> bool:
    user_policies = architecture.get("user_policies", {}) or {}
    return bool(user_policies.get("allow_auto_addressing", False))


def _addressing_mode(architecture: Dict[str, Any]) -> str | None:
    addressing = architecture.get("addressing", {}) or {}
    mode = addressing.get("mode")
    return mode if isinstance(mode, str) else None


def _subnet_bindings(architecture: Dict[str, Any]) -> Dict[str, str]:
    addressing = architecture.get("addressing", {}) or {}
    bindings = addressing.get("subnet_bindings", {}) or {}
    out = {}
    for k, v in bindings.items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = v
    return out


def _rendered_response_sections(result: Dict[str, Any]) -> List[str]:
    rendered = result.get("rendered_response")
    if not isinstance(rendered, dict):
        return []
    sections = rendered.get("sections")
    if not isinstance(sections, list):
        return []
    out = []
    for s in sections:
        if isinstance(s, dict) and isinstance(s.get("title"), str):
            out.append(s["title"])
    return out


def _ssh_plan(result: Dict[str, Any]) -> Dict[str, Any]:
    rendered = result.get("rendered_response")
    if not isinstance(rendered, dict):
        return {}
    ssh_plan = rendered.get("ssh_access_plan")
    return ssh_plan if isinstance(ssh_plan, dict) else {}


def _outputs_block(result: Dict[str, Any]) -> Dict[str, Any]:
    rendered = result.get("rendered_response")
    if not isinstance(rendered, dict):
        return {}
    outputs = rendered.get("outputs")
    return outputs if isinstance(outputs, dict) else {}


def _notes_block(result: Dict[str, Any]) -> List[str]:
    rendered = result.get("rendered_response")
    if not isinstance(rendered, dict):
        return []
    notes = rendered.get("notes_assumptions")
    if not isinstance(notes, list):
        return []
    return [str(x) for x in notes]


def evaluate_spec_compliance(result: Dict[str, Any]) -> Dict[str, Any]:
    status = result.get("status")
    architecture = result.get("architecture", {}) or {}

    checks: Dict[str, bool] = {}
    notes: List[str] = []
    violations: List[str] = []

    comps = _components_by_type(architecture)
    edges = _edges(architecture)
    routers = {cid for cid, ctype in comps.items() if ctype == "router"}
    switches = {cid for cid, ctype in comps.items() if ctype == "switch"}
    hosts = {cid for cid, ctype in comps.items() if ctype in {"pc", "server"}}
    domains = _router_domains(architecture)
    switch_router_map = _switch_to_router_from_compiled(architecture)
    compiled_hosts = _hosts_from_subnets(architecture)
    router_links = _router_links(architecture)

    checks["router_equals_vpc"] = len(domains) == len(routers)
    if not checks["router_equals_vpc"]:
        violations.append(
            f"Expected one VPC/domain per router, got routers={len(routers)} domains={len(domains)}"
        )

    compiled_switches = set(switch_router_map.keys())
    checks["switch_equals_subnet"] = switches.issubset(compiled_switches)
    if not checks["switch_equals_subnet"]:
        missing = sorted(switches - compiled_switches)
        violations.append(f"Missing subnet mapping for switches: {missing}")

    checks["hosts_placed_in_subnets"] = hosts.issubset(compiled_hosts)
    if not checks["hosts_placed_in_subnets"]:
        missing = sorted(hosts - compiled_hosts)
        violations.append(f"Hosts not placed in compiled subnets: {missing}")

    unique_vpc_cidrs = {
        router.get("vpc_cidr")
        for _, router in domains.items()
        if isinstance(router.get("vpc_cidr"), str)
    }
    checks["different_routers_different_vpcs"] = len(unique_vpc_cidrs) == len(domains)
    if not checks["different_routers_different_vpcs"]:
        violations.append("Router domains do not map cleanly to distinct VPC CIDRs")

    multi_switch_router_ok = True
    for rid in routers:
        owned_switches = [sw for sw, owner in switch_router_map.items() if owner == rid]
        if len(owned_switches) > 1:
            router_subnets = domains.get(rid, {}).get("subnets", []) or []
            if len(router_subnets) < len(owned_switches):
                multi_switch_router_ok = False
                violations.append(
                    f"Router {rid} has {len(owned_switches)} switches but only {len(router_subnets)} compiled subnets"
                )
    checks["same_router_many_lans_one_vpc_many_subnets"] = multi_switch_router_ok

    explicit_router_links_ok = True
    input_router_links = set()
    for a, b in edges:
        if comps.get(a) == "router" and comps.get(b) == "router":
            input_router_links.add(tuple(sorted((a, b))))
    compiled_router_links = {tuple(sorted((a, b))) for a, b in router_links}
    if input_router_links != compiled_router_links:
        explicit_router_links_ok = False
        violations.append(
            f"Router-link mismatch. input={sorted(input_router_links)} compiled={sorted(compiled_router_links)}"
        )
    checks["router_links_explicit"] = explicit_router_links_ok

    connectivity_mode = ((architecture.get("domain_plan", {}) or {}).get("connectivity_mode")) or "none"
    if len(routers) <= 1:
        checks["connectivity_mode_reasonable"] = connectivity_mode == "none"
    elif len(routers) == 2:
        checks["connectivity_mode_reasonable"] = connectivity_mode == "peering"
    else:
        checks["connectivity_mode_reasonable"] = connectivity_mode == "tgw"
    if not checks["connectivity_mode_reasonable"]:
        violations.append(
            f"Unexpected connectivity_mode={connectivity_mode!r} for router_count={len(routers)}"
        )

    mode = _addressing_mode(architecture)
    has_bindings = len(_subnet_bindings(architecture)) > 0
    auto_ok = _allow_auto_addressing(architecture)
    if status == "ok":
        checks["addressing_rule_respected"] = (mode == "manual" and has_bindings) or auto_ok
    else:
        checks["addressing_rule_respected"] = True
    if not checks["addressing_rule_respected"]:
        violations.append("Generation succeeded without manual addressing or explicit auto-addressing authorization")

    firewall_mode = ((architecture.get("firewall_policy", {}) or {}).get("mode"))
    if _has_firewall(architecture):
        if status == "need_more_info" and firewall_mode is None:
            checks["firewall_missing_mode_blocks"] = True
        elif status == "ok" and firewall_mode is not None:
            checks["firewall_missing_mode_blocks"] = True
        else:
            checks["firewall_missing_mode_blocks"] = False
            violations.append("Firewall handling does not respect missing-mode blocking rule")
    else:
        checks["firewall_missing_mode_blocks"] = True

    compiled_router_ids = set(domains.keys())
    checks["no_invented_routers"] = compiled_router_ids.issubset(routers)
    if not checks["no_invented_routers"]:
        violations.append(f"Compiled routers not present in input: {sorted(compiled_router_ids - routers)}")

    checks["no_invented_switches"] = compiled_switches.issubset(switches)
    if not checks["no_invented_switches"]:
        violations.append(f"Compiled switches not present in input: {sorted(compiled_switches - switches)}")

    public_hosts = _public_host_count(architecture)
    checks["public_access_not_hidden"] = True
    if public_hosts > 0:
        notes.append("Public hosts detected; this should correspond to explicit user intent in the prompt.")

    sections = _rendered_response_sections(result)
    if sections:
        checks["response_order_compliance"] = sections == REQUIRED_RESPONSE_ORDER
        if not checks["response_order_compliance"]:
            violations.append(
                f"Rendered response sections out of order. got={sections} expected={REQUIRED_RESPONSE_ORDER}"
            )
    else:
        checks["response_order_compliance"] = True
        notes.append("No rendered_response.sections found; skipping strict response-order compliance check.")

    ssh_plan = _ssh_plan(result)
    if ssh_plan:
        declared_public_hosts = ssh_plan.get("public_hosts", [])
        if not isinstance(declared_public_hosts, list):
            declared_public_hosts = []

        checks["ssh_public_host_alignment"] = sorted(declared_public_hosts) == _public_host_ids(architecture)
        if not checks["ssh_public_host_alignment"]:
            violations.append(
                f"SSH plan public hosts mismatch. ssh_plan={sorted(declared_public_hosts)} compiled={_public_host_ids(architecture)}"
            )

        uses_admin_cidr = bool(ssh_plan.get("uses_admin_cidr", False))
        if public_hosts > 0:
            checks["ssh_secure_default_rule"] = uses_admin_cidr
            if not checks["ssh_secure_default_rule"]:
                violations.append("Public SSH exists but ssh_access_plan does not declare admin_cidr usage")
        else:
            checks["ssh_secure_default_rule"] = True
    else:
        checks["ssh_public_host_alignment"] = True
        checks["ssh_secure_default_rule"] = True
        notes.append("No rendered ssh_access_plan found; skipping strict SSH presentation compliance check.")

    outputs = _outputs_block(result)
    if outputs:
        pem_map = outputs.get("pem_map", {})
        if not isinstance(pem_map, dict):
            pem_map = {}

        checks["pem_output_coverage"] = len(pem_map) == _pem_expected_count(architecture)
        if not checks["pem_output_coverage"]:
            violations.append(
                f"Expected PEM outputs for {_pem_expected_count(architecture)} hosts, got {len(pem_map)}"
            )

        if public_hosts > 0:
            public_ip_map = outputs.get("public_ip_map", {})
            if not isinstance(public_ip_map, dict):
                public_ip_map = {}
            checks["public_ip_output_when_public_access"] = len(public_ip_map) == public_hosts
            if not checks["public_ip_output_when_public_access"]:
                violations.append(
                    f"Expected public IP outputs for {public_hosts} public hosts, got {len(public_ip_map)}"
                )
        else:
            checks["public_ip_output_when_public_access"] = True
    else:
        checks["pem_output_coverage"] = True
        checks["public_ip_output_when_public_access"] = True
        notes.append("No rendered outputs block found; skipping strict output-format compliance check.")

    notes_block = _notes_block(result)
    checks["notes_assumptions_present"] = len(notes_block) > 0 if status == "ok" else True
    if not checks["notes_assumptions_present"]:
        notes.append("No rendered notes/assumptions block found.")

    passed = all(checks.values())

    return {
        "passed": passed,
        "checks": checks,
        "violations": violations,
        "notes": notes,
        "summary": {
            "router_count": len(routers),
            "switch_count": len(switches),
            "host_count": len(hosts),
            "compiled_domain_count": len(domains),
            "connectivity_mode": connectivity_mode,
            "public_host_count": public_hosts,
        },
    }
