from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Tuple

from groq import Groq

from config import EXTRACT_MODEL
from models import Architecture

VALID_COMPONENT_TYPES = {"router", "switch", "server", "pc", "firewall"}
VALID_FIREWALL_MODES = {"sg", "aws_network_firewall", "appliance"}


def _extract_json_from_text(text: str) -> Dict[str, Any]:
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    obj = re.search(r"\{.*\}", text, re.DOTALL)
    if obj:
        return json.loads(obj.group(0))

    raise ValueError("Could not parse JSON from model response.")


def _safe_int(value: Any):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            return int(m.group(0))
    return None


def _normalize_components(raw_components: List[dict]) -> List[dict]:
    normalized = []
    seen_ids = set()

    for c in raw_components:
        if not isinstance(c, dict):
            continue

        cid = c.get("id") or c.get("name") or c.get("component_id")
        ctype = c.get("type")

        if not cid or not ctype:
            continue

        cid = str(cid).strip()
        ctype = str(ctype).lower().strip()

        if not cid or ctype not in VALID_COMPONENT_TYPES:
            continue
        if cid in seen_ids:
            continue

        item = {
            "id": cid,
            "type": ctype,
        }

        raw_ifaces = c.get("interfaces", c.get("interface_count"))
        iface_count = _safe_int(raw_ifaces)
        if iface_count is not None:
            item["interfaces"] = iface_count

        normalized.append(item)
        seen_ids.add(cid)

    return normalized


def _normalize_edges(raw_edges: List[dict]) -> List[dict]:
    normalized = []
    seen_edges: set[Tuple[str, str]] = set()

    for e in raw_edges:
        if not isinstance(e, dict):
            continue

        src = e.get("from") or e.get("source")
        dst = e.get("to") or e.get("target")

        if not src or not dst:
            continue

        src = str(src).strip()
        dst = str(dst).strip()

        if not src or not dst or src == dst:
            continue

        pair = (src, dst)
        if pair in seen_edges:
            continue

        normalized.append({
            "from": src,
            "to": dst,
        })
        seen_edges.add(pair)

    return normalized


def _normalize_addressing(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}

    mode = raw.get("mode")
    cidrs = raw.get("cidrs", [])
    base_cidr = raw.get("base_cidr")
    subnet_bindings = raw.get("subnet_bindings", {})

    if not isinstance(cidrs, list):
        cidrs = []
    cidrs = [str(x).strip() for x in cidrs if isinstance(x, (str, int, float))]

    if not isinstance(subnet_bindings, dict):
        subnet_bindings = {}

    clean_bindings = {}
    for k, v in subnet_bindings.items():
        if isinstance(k, str) and isinstance(v, str):
            key = k.strip()
            val = v.strip()
            if key and val:
                clean_bindings[key] = val

    clean_mode = mode if isinstance(mode, str) or mode is None else None
    if isinstance(clean_mode, str):
        clean_mode = clean_mode.strip().lower()

    return {
        "mode": clean_mode,
        "cidrs": cidrs,
        "base_cidr": base_cidr.strip() if isinstance(base_cidr, str) else None,
        "subnet_bindings": clean_bindings,
        "subnets": [],
    }


def _normalize_firewall_mode(value: Any):
    if value is None or not isinstance(value, str):
        return None

    v = value.strip().lower()
    aliases = {
        "network firewall": "aws_network_firewall",
        "aws network firewall": "aws_network_firewall",
        "firewall appliance": "appliance",
        "appliance mode": "appliance",
        "security group": "sg",
        "security-group": "sg",
    }
    v = aliases.get(v, v)
    return v if v in VALID_FIREWALL_MODES else None


def _normalize_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {}

    components = data.get("components", [])
    edges = data.get("edges", [])
    addressing = data.get("addressing", {})
    firewall_policy = data.get("firewall_policy", {})
    user_policies = data.get("user_policies", {})

    if not isinstance(components, list):
        components = []
    if not isinstance(edges, list):
        edges = []
    if not isinstance(firewall_policy, dict):
        firewall_policy = {}
    if not isinstance(user_policies, dict):
        user_policies = {}

    return {
        "components": _normalize_components(components),
        "edges": _normalize_edges(edges),
        "addressing": _normalize_addressing(addressing),
        "firewall_policy": {
            "mode": _normalize_firewall_mode(firewall_policy.get("mode"))
        },
        "user_policies": {
            "allow_auto_addressing": bool(user_policies.get("allow_auto_addressing", False))
        },
        "domain_plan": {
            "routers": {},
            "router_links": [],
            "connectivity_mode": "none",
        },
    }


def force_firewall_mode_from_text(data: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    lower = user_text.lower()

    if "firewall mode is appliance" in lower:
        data["firewall_policy"]["mode"] = "appliance"
    elif "firewall mode is aws_network_firewall" in lower:
        data["firewall_policy"]["mode"] = "aws_network_firewall"
    elif re.search(r"\bfirewall mode is sg\b", lower):
        data["firewall_policy"]["mode"] = "sg"

    return data


def force_auto_addressing_from_text(data: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    lower = user_text.lower()

    triggers = [
        "do it by yourself",
        "do the addressing by yourself",
        "assign the addressing by yourself",
        "choose the addressing yourself",
        "automatic addressing",
        "auto addressing",
    ]

    if any(t in lower for t in triggers):
        data["user_policies"]["allow_auto_addressing"] = True
        if not data["addressing"].get("mode"):
            data["addressing"]["mode"] = "auto"

    return data


def extract_architecture(user_text: str, client: Groq | None = None) -> Architecture:
    client = client or Groq(api_key=os.environ["GROQ_API_KEY"])

    prompt = f"""
You are a network architecture extractor.

Return strict JSON with exactly these top-level fields:
- components
- edges
- addressing
- firewall_policy
- user_policies

Rules:
- Normalize component types to: router, switch, server, pc, firewall
- For routers, use "interfaces" as an integer when available
- For edges, use exactly "from" and "to"
- Firewall mode must only be one of: sg, aws_network_firewall, appliance, or null
- Do not invent missing edges
- Do not invent subnet objects
- subnet_bindings must be a dictionary like: {{"SW1": "10.0.1.0/29"}}
- If user says "do it by yourself", set user_policies.allow_auto_addressing=true
- Return JSON only

User description:
{user_text}
"""

    raw = client.chat.completions.create(
        model=EXTRACT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    ).choices[0].message.content

    payload = _extract_json_from_text(raw)
    payload = _normalize_payload(payload)
    payload = force_firewall_mode_from_text(payload, user_text)
    payload = force_auto_addressing_from_text(payload, user_text)

    return Architecture.model_validate(payload)
