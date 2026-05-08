from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from groq import Groq

from config import PLAN_MODEL

ALLOWED_CONNECTIVITY = {"none", "peering", "tgw"}
ALLOWED_STRATEGY = {
    "single_subnet",
    "split_public_private",
    "multi_subnet_single_vpc",
    "multi_vpc_peering",
    "multi_vpc_tgw",
}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("Planner did not return JSON.")
    return json.loads(m.group(0))


def _normalize_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if isinstance(x, (str, int, float, bool))]


def _normalize_bool(value: Any) -> bool:
    return bool(value)


def _normalize_connectivity(value: Any) -> str:
    if not isinstance(value, str):
        return "none"
    v = value.strip().lower()
    return v if v in ALLOWED_CONNECTIVITY else "none"


def _normalize_strategy(value: Any) -> str:
    if not isinstance(value, str):
        return "single_subnet"
    v = value.strip().lower()
    return v if v in ALLOWED_STRATEGY else "single_subnet"


def _normalize_confidence(value: Any) -> str:
    if not isinstance(value, str):
        return "medium"
    v = value.strip().lower()
    return v if v in ALLOWED_CONFIDENCE else "medium"


def _count_routers(extracted_arch: Dict[str, Any]) -> int:
    comps = extracted_arch.get("components", []) or []
    return sum(1 for c in comps if isinstance(c, dict) and c.get("type") == "router")


def _count_switches(extracted_arch: Dict[str, Any]) -> int:
    comps = extracted_arch.get("components", []) or []
    return sum(1 for c in comps if isinstance(c, dict) and c.get("type") == "switch")


def _has_router_links(extracted_arch: Dict[str, Any]) -> bool:
    comps = {
        c.get("id"): c.get("type")
        for c in (extracted_arch.get("components", []) or [])
        if isinstance(c, dict)
    }
    for e in extracted_arch.get("edges", []) or []:
        if not isinstance(e, dict):
            continue
        a = e.get("from")
        b = e.get("to")
        if comps.get(a) == "router" and comps.get(b) == "router":
            return True
    return False


def _prompt_has_bastion(prompt: str) -> bool:
    return "bastion" in prompt.lower()


def _prompt_has_private_needs_internet(prompt: str) -> bool:
    lower = prompt.lower()
    return (
        "needs internet" in lower
        or "needs internet access" in lower
        or "needs outbound internet" in lower
        or "private but needs internet" in lower
    )


def _prompt_has_public_private_split(prompt: str) -> bool:
    lower = prompt.lower()
    return (
        ("public" in lower and "private" in lower)
        or ("bastion" in lower and "private" in lower)
    )


def _derive_expected_fields(prompt: str, extracted_arch: Dict[str, Any]) -> Dict[str, Any]:
    router_count = _count_routers(extracted_arch)
    switch_count = _count_switches(extracted_arch)
    has_router_links = _has_router_links(extracted_arch)

    bastion_required = _prompt_has_bastion(prompt)
    nat_required = _prompt_has_private_needs_internet(prompt)

    if router_count <= 1 and not has_router_links:
        connectivity_mode = "none"
    elif router_count <= 2:
        connectivity_mode = "peering"
    else:
        connectivity_mode = "tgw"

    if connectivity_mode == "peering":
        strategy = "multi_vpc_peering"
    elif connectivity_mode == "tgw":
        strategy = "multi_vpc_tgw"
    elif _prompt_has_public_private_split(prompt):
        strategy = "split_public_private"
    elif router_count == 1 and switch_count > 1:
        strategy = "multi_subnet_single_vpc"
    else:
        strategy = "single_subnet"

    return {
        "connectivity_mode": connectivity_mode,
        "public_private_strategy": strategy,
        "nat_required": nat_required,
        "bastion_required": bastion_required,
    }


def plan_with_rag(
    prompt: str,
    extracted_arch: Dict[str, Any],
    retrieved_chunks: List[Dict[str, str]],
    client: Groq,
) -> Dict[str, Any]:
    context_blocks = []
    for i, ch in enumerate(retrieved_chunks, start=1):
        context_blocks.append(
            f"[Chunk {i}]\\nSource: {ch['source']}\\nHeading: {ch['heading']}\\n{ch['text']}"
        )
    kb_context = "\\n\\n".join(context_blocks)

    derived = _derive_expected_fields(prompt, extracted_arch)

    planner_prompt = f"""
You are a cloud network planner.

You are given:
1. A user prompt
2. An extracted architecture JSON
3. Retrieved knowledge-base chunks
4. Deterministic topology hints

Your job:
- choose the best cloud interpretation
- identify assumptions
- output strict JSON only

Return exactly this schema:

{{
  "deployment_pattern": "<short string>",
  "confidence": "low|medium|high",
  "connectivity_mode": "none|peering|tgw",
  "public_private_strategy": "single_subnet|split_public_private|multi_subnet_single_vpc|multi_vpc_peering|multi_vpc_tgw",
  "nat_required": true,
  "bastion_required": false,
  "assumptions": ["..."],
  "recommended_actions": ["..."],
  "plan_notes": ["..."]
}}

Important:
- deterministic topology hints take priority if they conflict with your preferences
- do not invent devices or edges
- do not change routed-domain interpretation

Deterministic topology hints:
{json.dumps(derived, indent=2)}

User prompt:
{prompt}

Extracted architecture JSON:
{json.dumps(extracted_arch, indent=2)}

Retrieved knowledge base:
{kb_context}
"""

    raw = client.chat.completions.create(
        model=PLAN_MODEL,
        messages=[{"role": "user", "content": planner_prompt}],
        temperature=0,
    ).choices[0].message.content

    data = _extract_json(raw)

    result = {
        "deployment_pattern": str(data.get("deployment_pattern", "derived_plan")).strip() or "derived_plan",
        "confidence": _normalize_confidence(data.get("confidence")),
        "connectivity_mode": _normalize_connectivity(data.get("connectivity_mode")),
        "public_private_strategy": _normalize_strategy(data.get("public_private_strategy")),
        "nat_required": _normalize_bool(data.get("nat_required")),
        "bastion_required": _normalize_bool(data.get("bastion_required")),
        "assumptions": _normalize_list(data.get("assumptions")),
        "recommended_actions": _normalize_list(data.get("recommended_actions")),
        "plan_notes": _normalize_list(data.get("plan_notes")),
        "planner_used": True,
    }

    # Deterministic compiler remains authoritative
    result["connectivity_mode"] = derived["connectivity_mode"]
    result["public_private_strategy"] = derived["public_private_strategy"]
    result["nat_required"] = derived["nat_required"]
    result["bastion_required"] = derived["bastion_required"]

    return result
