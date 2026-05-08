from __future__ import annotations

from typing import Any, Dict, List


def _as_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj

    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True)

    if hasattr(obj, "dict"):
        return obj.dict(by_alias=True)

    return {}


def _iter_routers(architecture: Dict[str, Any]) -> List[Dict[str, Any]]:
    domain_plan = architecture.get("domain_plan", {}) or {}
    routers = domain_plan.get("routers", {}) or {}

    if isinstance(routers, dict):
        return [_as_dict(router) for router in routers.values()]

    if isinstance(routers, list):
        return [_as_dict(router) for router in routers]

    return []


def _compiled_summary(architecture: Dict[str, Any]) -> Dict[str, Any]:
    architecture = _as_dict(architecture)

    domain_plan = architecture.get("domain_plan", {}) or {}
    connectivity_mode = domain_plan.get("connectivity_mode", "none") or "none"

    routers = _iter_routers(architecture)

    public_count = 0
    private_count = 0
    nat_required = False

    public_host_exists = False
    private_host_exists = False
    explicit_bastion_exists = False

    for router in routers:
        for subnet in router.get("subnets", []) or []:
            subnet = _as_dict(subnet)

            subnet_is_public = bool(subnet.get("public"))
            subnet_needs_nat = bool(subnet.get("needs_nat"))

            if subnet_is_public:
                public_count += 1
            else:
                private_count += 1

            if subnet_needs_nat:
                nat_required = True

            for hp in subnet.get("host_placements", []) or []:
                hp = _as_dict(hp)

                exposure = hp.get("exposure")
                needs_outbound = bool(hp.get("needs_outbound_internet"))
                is_bastion = bool(hp.get("is_bastion"))

                if exposure == "public":
                    public_host_exists = True

                if exposure == "private":
                    private_host_exists = True

                if needs_outbound:
                    nat_required = True

                if is_bastion:
                    explicit_bastion_exists = True

    # Strategy detection.
    if connectivity_mode == "tgw":
        public_private_strategy = "multi_vpc_tgw"
    elif connectivity_mode == "peering":
        public_private_strategy = "multi_vpc_peering"
    elif public_count > 0 and private_count > 0:
        public_private_strategy = "split_public_private"
    elif public_host_exists and private_host_exists:
        public_private_strategy = "split_public_private"
    else:
        public_private_strategy = "single_subnet"

    # Bastion detection.
    # In this project, a public PC can act as bastion for private hosts.
    bastion_required = explicit_bastion_exists

    return {
        "connectivity_mode": connectivity_mode,
        "public_private_strategy": public_private_strategy,
        "nat_required": nat_required,
        "bastion_required": bastion_required,
    }


def compare_plan_to_compiled(
    rag_plan: Dict[str, Any],
    architecture: Dict[str, Any],
) -> Dict[str, Any]:
    rag_plan = rag_plan or {}
    architecture = architecture or {}

    compiled = _compiled_summary(architecture)
    mismatches: List[str] = []

    planner_summary = {
        "connectivity_mode": rag_plan.get("connectivity_mode"),
        "public_private_strategy": rag_plan.get("public_private_strategy"),
        "nat_required": bool(rag_plan.get("nat_required")),
        "bastion_required": bool(rag_plan.get("bastion_required")),
    }

    for key in [
        "connectivity_mode",
        "public_private_strategy",
        "nat_required",
        "bastion_required",
    ]:
        if planner_summary.get(key) != compiled.get(key):
            mismatches.append(
                f"{key}: planner={planner_summary.get(key)!r} compiled={compiled.get(key)!r}"
            )

    return {
        "planner_summary": planner_summary,
        "compiled_summary": compiled,
        "matches": len(mismatches) == 0,
        "mismatches": mismatches,
    }
