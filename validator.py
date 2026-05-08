from __future__ import annotations

import ipaddress
from typing import Dict, List, Set, Tuple

from models import Architecture


def get_components_map(arch: Architecture) -> Dict[str, dict]:
    return {c.id: c.model_dump() for c in arch.components}


def build_adjacency(arch: Architecture) -> Dict[str, List[str]]:
    adj = {c.id: [] for c in arch.components}
    for e in arch.edges:
        if e.from_ in adj and e.to in adj:
            adj[e.from_].append(e.to)
            adj[e.to].append(e.from_)
    return adj


def _is_default_sg_firewall(arch: Architecture, component_id: str) -> bool:
    for c in arch.components:
        if c.id == component_id and c.type == "firewall":
            # firewall -> Security Group by default unless explicitly overridden
            return arch.firewall_policy.mode in {None, "sg"}
    return False


def validate_architecture(arch: Architecture) -> List[str]:
    issues: List[str] = []
    components = get_components_map(arch)
    adjacency = build_adjacency(arch)

    if not arch.components:
        issues.append("No components were provided.")

    # Duplicate component IDs
    seen_ids: Set[str] = set()
    for c in arch.components:
        if c.id in seen_ids:
            issues.append(f"Duplicate component id: {c.id}")
        seen_ids.add(c.id)

    comp_ids = set(components.keys())

    # Unknown edge endpoints + duplicate edges
    seen_edges: Set[Tuple[str, str]] = set()
    for e in arch.edges:
        if e.from_ not in comp_ids:
            issues.append(f"Unknown edge source: {e.from_}")
        if e.to not in comp_ids:
            issues.append(f"Unknown edge target: {e.to}")

        pair = (e.from_, e.to)
        if pair in seen_edges:
            issues.append(f"Duplicate edge: {e.from_} -> {e.to}")
        seen_edges.add(pair)

    for c in arch.components:
        # Firewall = Security Group by default, so it does not need physical edges
        # unless another firewall model is explicitly selected.
        if _is_default_sg_firewall(arch, c.id):
            continue

        if len(adjacency.get(c.id, [])) == 0:
            issues.append(f"What is {c.id} connected to?")

    if arch.addressing.mode == "manual":
        issues.extend(validate_manual_addressing(arch))

        switch_ids = {c.id for c in arch.components if c.type == "switch"}
        if switch_ids and len(arch.addressing.subnet_bindings) == 0:
            issues.append("Manual addressing is enabled but no subnet bindings were provided.")

    elif arch.addressing.mode == "auto":
        # auto mode is acceptable only when explicitly authorized
        if not arch.user_policies.allow_auto_addressing:
            issues.append("Automatic addressing is set but was not explicitly authorized.")

    else:
        if not arch.user_policies.allow_auto_addressing:
            issues.append("Addressing is missing. Provide CIDRs or say 'do it by yourself'.")

    out = []
    seen = set()
    for i in issues:
        if i not in seen:
            out.append(i)
            seen.add(i)
    return out


def validate_manual_addressing(arch: Architecture) -> List[str]:
    issues: List[str] = []
    components = get_components_map(arch)
    switch_ids = {c.id for c in arch.components if c.type == "switch"}
    bindings = arch.addressing.subnet_bindings
    base = arch.addressing.base_cidr

    parsed = {}
    for sw, cidr in bindings.items():
        try:
            parsed[sw] = ipaddress.ip_network(cidr, strict=True)
        except ValueError:
            issues.append(f"Invalid CIDR for {sw}: {cidr}")

    for sw in bindings:
        if sw not in components:
            issues.append(f"Manual addressing references unknown component/subnet: {sw}")
        elif sw not in switch_ids:
            issues.append(f"Manual addressing binds CIDR to {sw}, but {sw} is not a switch")

    if base:
        try:
            base_net = ipaddress.ip_network(base, strict=True)
            for sw, net in parsed.items():
                if not net.subnet_of(base_net):
                    issues.append(f"Subnet {net} for {sw} is outside base CIDR {base_net}")
        except ValueError:
            issues.append(f"Invalid base CIDR: {base}")

    items = list(parsed.items())
    for i in range(len(items)):
        sw1, net1 = items[i]
        for j in range(i + 1, len(items)):
            sw2, net2 = items[j]
            if net1.overlaps(net2):
                issues.append(f"Subnets for {sw1} ({net1}) and {sw2} ({net2}) overlap")

    return issues

