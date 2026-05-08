from __future__ import annotations

import ipaddress
import re
from typing import Dict, List, Optional, Set

from intake_models import (
    IntakeComponent,
    IntakeDecision,
    IntakeEdge,
    IntakeSession,
)
from models import Architecture


_COMPONENT_ALIASES = {
    "router": "router",
    "switch": "switch",
    "server": "server",
    "pc": "pc",
    "firewall": "firewall",
    "lan": "switch",
    "vlan": "switch",
    "lan segment": "switch",
}

_FIREWALL_CHOICES = {"sg", "aws_network_firewall", "appliance"}

_AUTO_CHOICES = {
    "",
    "auto",
    "automatic",
    "do it by yourself",
    "do it yourself",
    "handle it yourself",
    "addressing alone",
    "auto addressing",
}

_MANUAL_CHOICES = {
    "yes",
    "manual",
    "i will do it",
    "i want manual",
    "manual addressing",
}


def start_intake_session() -> IntakeSession:
    session = IntakeSession()
    session.firewall_mode = "sg"
    session.last_question = (
        "Give me the components first. Example: "
        "router R1 with 2 interfaces, switch SW1, pc PC1, server S1."
    )
    return session


def _component_map(session: IntakeSession) -> Dict[str, IntakeComponent]:
    return {c.id: c for c in session.components}


def _switch_ids(session: IntakeSession) -> List[str]:
    return sorted([c.id for c in session.components if c.type == "switch"])


def _host_ids(session: IntakeSession) -> List[str]:
    return sorted([c.id for c in session.components if c.type in {"pc", "server"}])


def _has_firewall(session: IntakeSession) -> bool:
    return any(c.type == "firewall" for c in session.components)


def _default_sg_firewall_ids(session: IntakeSession) -> Set[str]:
    if session.firewall_mode in {"unknown", "sg"}:
        return {c.id for c in session.components if c.type == "firewall"}
    return set()


def _dedupe_components(items: List[IntakeComponent]) -> List[IntakeComponent]:
    out: Dict[str, IntakeComponent] = {}
    for item in items:
        if item.id not in out:
            out[item.id] = item
        else:
            existing = out[item.id]
            if existing.interfaces is None and item.interfaces is not None:
                existing.interfaces = item.interfaces
    return list(out.values())


def _dedupe_edges(items: List[IntakeEdge]) -> List[IntakeEdge]:
    seen = set()
    out = []
    for e in items:
        if e.from_id == e.to_id:
            continue
        key = tuple(sorted((e.from_id, e.to_id)))
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _dedupe_str_list(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_components_from_text(text: str) -> List[IntakeComponent]:
    items: List[IntakeComponent] = []

    p = re.compile(
        r'(?i)\b(router|switch|server|pc|firewall|lan|vlan)\b\s+([A-Za-z][A-Za-z0-9_-]*)'
        r'(?:\s+with\s+(\d+)\s+interfaces?)?'
    )

    for m in p.finditer(text):
        raw_type, cid, ifaces = m.group(1), m.group(2), m.group(3)
        ctype = _COMPONENT_ALIASES.get(raw_type.strip().lower())
        if not ctype:
            continue

        items.append(
            IntakeComponent(
                id=cid,
                type=ctype,
                interfaces=int(ifaces) if ifaces else None,
            )
        )

    return _dedupe_components(items)


def parse_edges_from_text(text: str, known_ids: Set[str]) -> List[IntakeEdge]:
    edges: List[IntakeEdge] = []

    patterns = [
        re.compile(r'(?i)\b([A-Za-z][A-Za-z0-9_-]*)\b\s+is\s+connected\s+to\s+\b([A-Za-z][A-Za-z0-9_-]*)\b'),
        re.compile(r'(?i)\b([A-Za-z][A-Za-z0-9_-]*)\b\s+connected\s+to\s+\b([A-Za-z][A-Za-z0-9_-]*)\b'),
        re.compile(r'(?i)\b([A-Za-z][A-Za-z0-9_-]*)\b\s*[-]{1,2}\s*\b([A-Za-z][A-Za-z0-9_-]*)\b'),
    ]

    for pat in patterns:
        for m in pat.finditer(text):
            a, b = m.group(1), m.group(2)
            if a in known_ids and b in known_ids and a != b:
                edges.append(IntakeEdge(from_id=a, to_id=b))

    return _dedupe_edges(edges)


def _parse_named_host_list(text: str, prefix_patterns: List[str], known_hosts: Set[str]) -> List[str]:
    lower = text.lower()
    found = []

    for host in known_hosts:
        h = host.lower()
        host_patterns = [
            rf"\b{re.escape(h)}\b\s+(should\s+be|is)\s+public\b",
            rf"\b{re.escape(h)}\b\s+(should\s+be|is)\s+private\b",
            rf"\b{re.escape(h)}\b\s+(should\s+be|is)\s+the\s+bastion\b",
            rf"\b{re.escape(h)}\b\s+(should\s+be|is)\s+bastion\b",
            rf"\b{re.escape(h)}\b\s+needs\s+internet\b",
            rf"\b{re.escape(h)}\b\s+needs\s+outbound\s+internet\b",
            rf"\b{re.escape(h)}\b\s+needs\s+internet\s+access\b",
        ]
        if any(re.search(p, lower) for p in host_patterns):
            found.append(host)

    for pat in prefix_patterns:
        m = re.search(pat, lower)
        if m:
            raw = m.group(1)
            for token in re.split(r"[,\s]+", raw):
                token = token.strip()
                if not token:
                    continue
                for host in known_hosts:
                    if token == host.lower():
                        found.append(host)

    return _dedupe_str_list(found)


def parse_public_hosts_from_text(text: str, known_hosts: Set[str]) -> List[str]:
    patterns = [
        r'public\s+hosts?\s*[:=]\s*(.+)',
        r'make\s+these\s+hosts?\s+public\s*[:=]?\s*(.+)',
    ]
    return _parse_named_host_list(text, patterns, known_hosts)


def parse_bastion_hosts_from_text(text: str, known_hosts: Set[str]) -> List[str]:
    patterns = [
        r'bastion\s+hosts?\s*[:=]\s*(.+)',
        r'bastion\s*[:=]\s*(.+)',
    ]
    return _parse_named_host_list(text, patterns, known_hosts)


def parse_nat_hosts_from_text(text: str, known_hosts: Set[str]) -> List[str]:
    patterns = [
        r'nat\s+hosts?\s*[:=]\s*(.+)',
        r'hosts?\s+that\s+need\s+internet\s*[:=]?\s*(.+)',
        r'private\s+hosts?\s+with\s+internet\s*[:=]?\s*(.+)',
    ]
    return _parse_named_host_list(text, patterns, known_hosts)


def _adjacency(session: IntakeSession) -> Dict[str, List[str]]:
    adj = {c.id: [] for c in session.components}
    for e in session.edges:
        if e.from_id in adj and e.to_id in adj:
            adj[e.from_id].append(e.to_id)
            adj[e.to_id].append(e.from_id)
    return adj


def find_isolated_components(session: IntakeSession) -> List[str]:
    adj = _adjacency(session)
    ignore_ids = _default_sg_firewall_ids(session)
    return sorted([
        cid for cid, neighbors in adj.items()
        if len(neighbors) == 0 and cid not in ignore_ids
    ])


def parse_base_cidr(text: str) -> Optional[str]:
    m = re.search(r'((?:\d{1,3}\.){3}\d{1,3}/\d{1,2})', text)
    if not m:
        return None
    try:
        return str(ipaddress.ip_network(m.group(1), strict=True))
    except ValueError:
        return None


def parse_switch_cidr_answer(text: str, expected_switch_id: str) -> Optional[str]:
    patterns = [
        rf'(?i)\b{re.escape(expected_switch_id)}\b\s*(?:=|:)\s*((?:\d{{1,3}}\.){{3}}\d{{1,3}}/\d{{1,2}})',
        r'((?:\d{1,3}\.){3}\d{1,3}/\d{1,2})',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                return str(ipaddress.ip_network(m.group(1), strict=True))
            except ValueError:
                return None
    return None


def parse_firewall_mode(text: str) -> Optional[str]:
    t = text.strip().lower()
    aliases = {
        "security group": "sg",
        "security-group": "sg",
        "network firewall": "aws_network_firewall",
        "aws network firewall": "aws_network_firewall",
        "firewall appliance": "appliance",
    }
    t = aliases.get(t, t)
    return t if t in _FIREWALL_CHOICES else None


def _is_auto_choice(text: str) -> bool:
    return text.strip().lower() in _AUTO_CHOICES


def _is_manual_choice(text: str) -> bool:
    return text.strip().lower() in _MANUAL_CHOICES


def session_to_prompt(session: IntakeSession) -> str:
    lines: List[str] = []

    comps = []
    for c in session.components:
        if c.interfaces is not None and c.type == "router":
            comps.append(f"{c.type} {c.id} with {c.interfaces} interfaces")
        else:
            comps.append(f"{c.type} {c.id}")
    if comps:
        lines.append("Components: " + ", ".join(comps) + ".")

    for e in session.edges:
        lines.append(f"{e.from_id} is connected to {e.to_id}.")

    if _has_firewall(session) and session.firewall_mode not in {"unknown", "sg"}:
        lines.append(f"Firewall mode is {session.firewall_mode}.")
    elif _has_firewall(session):
        lines.append("Firewall mode is sg.")

    if session.addressing.mode == "manual":
        if session.addressing.base_cidr:
            lines.append(f"base cidr {session.addressing.base_cidr}")
        for sw, cidr in sorted(session.addressing.subnet_bindings.items()):
            lines.append(f"{sw} = {cidr}")
    elif session.addressing.mode == "auto":
        lines.append("do it by yourself")

    for host in session.host_intent.public_hosts:
        lines.append(f"{host} should be public.")

    for host in session.host_intent.bastion_hosts:
        lines.append(f"{host} should be the bastion.")

    for host in session.host_intent.nat_hosts:
        lines.append(f"{host} needs outbound internet.")

    return "\\n".join(lines).strip()


def intake_session_to_architecture(session: IntakeSession) -> Architecture:
    if not session.ready_to_compile:
        raise ValueError("Session is not ready to compile.")

    payload = {
        "components": [
            {
                "id": c.id,
                "type": c.type,
                **({"interfaces": c.interfaces} if c.interfaces is not None else {}),
            }
            for c in session.components
        ],
        "edges": [
            {"from": e.from_id, "to": e.to_id}
            for e in session.edges
        ],
        "addressing": {
            "mode": "manual" if session.addressing.mode == "manual" else None,
            "cidrs": list(session.addressing.subnet_bindings.values()),
            "base_cidr": session.addressing.base_cidr,
            "subnet_bindings": dict(session.addressing.subnet_bindings),
            "subnets": [],
        },
        "firewall_policy": {
            "mode": "sg" if session.firewall_mode in {"unknown", "sg"} else session.firewall_mode,
        },
        "user_policies": {
            "allow_auto_addressing": session.addressing.mode == "auto",
        },
        "domain_plan": {
            "routers": {},
            "router_links": [],
            "connectivity_mode": "none",
        },
    }
    return Architecture.model_validate(payload)


def process_intake_turn(session: IntakeSession, user_text: str) -> IntakeDecision:
    text = user_text.strip()

    if session.stage == "collect_components":
        new_components = parse_components_from_text(text)
        if not new_components:
            q = (
                "I did not detect valid components. Give me components first. Example: "
                "router R1 with 2 interfaces, switch SW1, pc PC1, server S1."
            )
            session.last_question = q
            return IntakeDecision(
                can_advance=False,
                next_stage=session.stage,
                question=q,
                blocking_issues=["No valid components detected."],
            )

        session.components = _dedupe_components(session.components + new_components)

        if _has_firewall(session) and session.firewall_mode == "unknown":
            session.firewall_mode = "sg"

        session.stage = "collect_edges"
        q = (
            "Good. Now give me all edges. Example: "
            "PC1 is connected to SW1. SW1 is connected to R1."
        )
        session.last_question = q
        return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

    if session.stage in {"collect_edges", "resolve_missing_edges"}:
        known_ids = set(_component_map(session).keys())
        new_edges = parse_edges_from_text(text, known_ids)

        if not new_edges and session.stage == "collect_edges":
            q = (
                "I did not detect valid edges. Give me edges like: "
                "PC1 connected to SW1, SW1 connected to R1."
            )
            session.last_question = q
            return IntakeDecision(
                can_advance=False,
                next_stage=session.stage,
                question=q,
                blocking_issues=["No valid edges detected."],
            )

        session.edges = _dedupe_edges(session.edges + new_edges)
        isolated = find_isolated_components(session)
        session.missing_edge_components = isolated

        if isolated:
            session.stage = "resolve_missing_edges"
            target = isolated[0]
            q = f"{target} has no edge yet. Give me its connection."
            session.last_question = q
            return IntakeDecision(
                can_advance=False,
                next_stage=session.stage,
                question=q,
                blocking_issues=[f"{target} is still isolated."],
            )

        session.stage = "ask_addressing_mode"
        q = (
            "Topology is complete. Press Enter for automatic addressing, "
            "or type yes/manual if you want to enter CIDRs one by one."
        )
        session.last_question = q
        return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

    if session.stage == "ask_firewall_mode":
        mode = parse_firewall_mode(text)
        if not mode:
            q = "Invalid firewall mode. Choose exactly: sg, aws_network_firewall, or appliance."
            session.last_question = q
            return IntakeDecision(
                can_advance=False,
                next_stage=session.stage,
                question=q,
                blocking_issues=["Firewall mode is required."],
            )

        session.firewall_mode = mode
        session.stage = "ask_addressing_mode"
        q = (
            "Topology is complete. Press Enter for automatic addressing, "
            "or type yes/manual if you want to enter CIDRs one by one."
        )
        session.last_question = q
        return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

    if session.stage == "ask_addressing_mode":
        if _is_auto_choice(text):
            session.addressing.mode = "auto"
            if _host_ids(session):
                session.stage = "collect_host_intent"
                q = (
                    "Optional host intent: say which hosts should be public, bastion, or need outbound internet. "
                    "Example: PC1 should be public. S1 should be private but needs internet. "
                    "Press Enter to skip."
                )
                session.last_question = q
                return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

            session.ready_to_compile = True
            session.stage = "ready_to_compile"
            q = "Ready to compile."
            session.last_question = q
            return IntakeDecision(
                can_advance=True,
                next_stage=session.stage,
                question=q,
                ready_to_compile=True,
            )

        if _is_manual_choice(text):
            session.addressing.mode = "manual"
            session.stage = "collect_base_cidr"
            q = "Give me the base CIDR first. Example: 10.0.0.0/16"
            session.last_question = q
            return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

        q = (
            "I need a clear choice. Press Enter for automatic addressing, "
            "or type yes/manual for manual CIDRs."
        )
        session.last_question = q
        return IntakeDecision(
            can_advance=False,
            next_stage=session.stage,
            question=q,
            blocking_issues=["Addressing mode not clear."],
        )

    if session.stage == "collect_base_cidr":
        base = parse_base_cidr(text)
        if not base:
            q = "Invalid base CIDR. Give me something like 10.0.0.0/16"
            session.last_question = q
            return IntakeDecision(
                can_advance=False,
                next_stage=session.stage,
                question=q,
                blocking_issues=["Base CIDR missing or invalid."],
            )

        session.addressing.base_cidr = base
        session.pending_subnet_components = _switch_ids(session)

        if not session.pending_subnet_components:
            if _host_ids(session):
                session.stage = "collect_host_intent"
                q = (
                    "Optional host intent: say which hosts should be public, bastion, or need outbound internet. "
                    "Press Enter to skip."
                )
                session.last_question = q
                return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

            session.ready_to_compile = True
            session.stage = "ready_to_compile"
            q = "Ready to compile."
            session.last_question = q
            return IntakeDecision(
                can_advance=True,
                next_stage=session.stage,
                question=q,
                ready_to_compile=True,
            )

        session.stage = "collect_subnet_cidrs"
        target = session.pending_subnet_components[0]
        q = f"Give me the CIDR for {target}. Example: {target} = 10.0.1.0/24"
        session.last_question = q
        return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

    if session.stage == "collect_subnet_cidrs":
        if not session.pending_subnet_components:
            if _host_ids(session):
                session.stage = "collect_host_intent"
                q = (
                    "Optional host intent: say which hosts should be public, bastion, or need outbound internet. "
                    "Press Enter to skip."
                )
                session.last_question = q
                return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

            session.ready_to_compile = True
            session.stage = "ready_to_compile"
            q = "Ready to compile."
            session.last_question = q
            return IntakeDecision(
                can_advance=True,
                next_stage=session.stage,
                question=q,
                ready_to_compile=True,
            )

        target = session.pending_subnet_components[0]
        cidr = parse_switch_cidr_answer(text, target)
        if not cidr:
            q = f"Invalid CIDR for {target}. Give me something like {target} = 10.0.1.0/24"
            session.last_question = q
            return IntakeDecision(
                can_advance=False,
                next_stage=session.stage,
                question=q,
                blocking_issues=[f"CIDR missing or invalid for {target}."],
            )

        session.addressing.subnet_bindings[target] = cidr
        session.pending_subnet_components.pop(0)

        if session.pending_subnet_components:
            nxt = session.pending_subnet_components[0]
            q = f"Good. Now give me the CIDR for {nxt}."
            session.last_question = q
            return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

        if _host_ids(session):
            session.stage = "collect_host_intent"
            q = (
                "Optional host intent: say which hosts should be public, bastion, or need outbound internet. "
                "Press Enter to skip."
            )
            session.last_question = q
            return IntakeDecision(can_advance=True, next_stage=session.stage, question=q)

        session.ready_to_compile = True
        session.stage = "ready_to_compile"
        q = "Ready to compile."
        session.last_question = q
        return IntakeDecision(
            can_advance=True,
            next_stage=session.stage,
            question=q,
            ready_to_compile=True,
        )

    if session.stage == "collect_host_intent":
        known_hosts = set(_host_ids(session))

        if text:
            public_hosts = parse_public_hosts_from_text(text, known_hosts)
            bastion_hosts = parse_bastion_hosts_from_text(text, known_hosts)
            nat_hosts = parse_nat_hosts_from_text(text, known_hosts)

            session.host_intent.public_hosts = _dedupe_str_list(
                session.host_intent.public_hosts + public_hosts + bastion_hosts
            )
            session.host_intent.bastion_hosts = _dedupe_str_list(
                session.host_intent.bastion_hosts + bastion_hosts
            )
            session.host_intent.nat_hosts = _dedupe_str_list(
                session.host_intent.nat_hosts + nat_hosts
            )

        session.ready_to_compile = True
        session.stage = "ready_to_compile"
        q = "Ready to compile."
        session.last_question = q
        return IntakeDecision(
            can_advance=True,
            next_stage=session.stage,
            question=q,
            ready_to_compile=True,
        )

    q = "Session is already ready to compile."
    session.last_question = q
    return IntakeDecision(
        can_advance=True,
        next_stage=session.stage,
        question=q,
        ready_to_compile=session.ready_to_compile,
    )
