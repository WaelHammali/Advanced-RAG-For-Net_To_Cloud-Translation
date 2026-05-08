from __future__ import annotations

import ipaddress
import math
import re
from collections import deque
from typing import Dict, List, Set

from models import Architecture, DomainPlan, RouterDomain, RouterSubnet, HostPlacement, Component, Edge

DEFAULT_BASE_CIDR = "10.0.0.0/8"


def parse_manual_addressing(user_text: str) -> Dict[str, object]:
    subnet_bindings = {}
    base_cidr = None

    base_patterns = [
        r"base\s+cidr\s*(?:=|:)?\s*((?:\d{1,3}\.){3}\d{1,3}/\d{1,2})",
        r"network\s+base\s*(?:=|:)?\s*((?:\d{1,3}\.){3}\d{1,3}/\d{1,2})",
        r"global\s+cidr\s*(?:=|:)?\s*((?:\d{1,3}\.){3}\d{1,3}/\d{1,2})",
    ]

    for pat in base_patterns:
        m = re.search(pat, user_text, re.IGNORECASE)
        if m:
            base_cidr = str(ipaddress.ip_network(m.group(1), strict=True))
            break

    binding_pattern = r"\b([A-Za-z][A-Za-z0-9_-]*)\b\s*(?:=|:)\s*((?:\d{1,3}\.){3}\d{1,3}/\d{1,2})"

    for match in re.finditer(binding_pattern, user_text, re.IGNORECASE):
        subnet_bindings[match.group(1)] = str(ipaddress.ip_network(match.group(2), strict=True))

    return {
        "mode": "manual" if subnet_bindings else None,
        "base_cidr": base_cidr,
        "cidrs": list(subnet_bindings.values()),
        "subnet_bindings": subnet_bindings,
    }


def enrich_with_manual_addressing(arch: Architecture, user_text: str) -> Architecture:
    parsed = parse_manual_addressing(user_text)
    if parsed["mode"] == "manual":
        arch.addressing.mode = "manual"
        arch.addressing.base_cidr = parsed["base_cidr"]
        arch.addressing.cidrs = parsed["cidrs"]
        arch.addressing.subnet_bindings = parsed["subnet_bindings"]
    return arch


def _adjacency(arch: Architecture) -> Dict[str, List[str]]:
    adj = {c.id: [] for c in arch.components}
    for e in arch.edges:
        if e.from_ in adj and e.to in adj:
            adj[e.from_].append(e.to)
            adj[e.to].append(e.from_)
    return adj


def _components_by_type(arch: Architecture) -> Dict[str, str]:
    return {c.id: c.type for c in arch.components}


def _edge_pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted([a, b]))


def _edge_exists(arch: Architecture, a: str, b: str) -> bool:
    wanted = _edge_pair(a, b)
    for e in arch.edges:
        if _edge_pair(e.from_, e.to) == wanted:
            return True
    return False


def _add_edge_if_missing(arch: Architecture, a: str, b: str) -> None:
    if a == b:
        return
    if not _edge_exists(arch, a, b):
        arch.edges.append(Edge(from_=a, to=b))


def _unique_component_id(existing: Set[str], preferred: str) -> str:
    if preferred not in existing:
        return preferred
    i = 1
    while f"{preferred}{i}" in existing:
        i += 1
    return f"{preferred}{i}"


def _host_connected_to_switch(host_id: str, arch: Architecture) -> bool:
    adj = _adjacency(arch)
    types = _components_by_type(arch)
    return any(types.get(nb) == "switch" for nb in adj.get(host_id, []))


def _infer_logical_vpc_domain_if_missing(arch: Architecture) -> Architecture:
    """
    Infer only an AWS VPC routing domain when the user provides hosts/L2
    topology with no router.

    This is NOT a real EC2 router appliance.

    Examples:
      PC1 -- PC2
      PC1 -- SW1 -- PC2

    become logically:
      R1 -- SW1 -- PC1/PC2

    where R1 is only the cloud VPC owner used by domain planning:
      R1 -> one AWS VPC
      SW1 -> one AWS subnet
      PCs -> EC2 instances in that subnet
    """
    types = _components_by_type(arch)
    routers = sorted([cid for cid, ctype in types.items() if ctype == "router"])
    hosts = sorted([cid for cid, ctype in types.items() if ctype in {"pc", "server"}])
    switches = sorted([cid for cid, ctype in types.items() if ctype == "switch"])

    if routers or not hosts:
        return arch

    existing_ids = set(types.keys())
    router_id = _unique_component_id(existing_ids, "R1")
    arch.components.append(Component(id=router_id, type="router", interfaces=0))
    existing_ids.add(router_id)

    if not switches:
        switch_id = _unique_component_id(existing_ids, "SW1")
        arch.components.append(Component(id=switch_id, type="switch"))
        switches = [switch_id]
        existing_ids.add(switch_id)

        # Direct host-host links are treated as one LAN; attach every host
        # to the inferred switch/subnet.
        for host in hosts:
            _add_edge_if_missing(arch, host, switch_id)
    else:
        # If a switch exists but some host is only connected through a direct
        # host-host edge, place it on the first switch LAN.
        first_switch = switches[0]
        for host in hosts:
            if not _host_connected_to_switch(host, arch):
                _add_edge_if_missing(arch, host, first_switch)

    # Each switch/LAN becomes a subnet inside the inferred VPC domain.
    for switch_id in switches:
        _add_edge_if_missing(arch, switch_id, router_id)

    return arch


def _find_router_for_switch(switch_id: str, arch: Architecture) -> str | None:
    adj = _adjacency(arch)
    types = _components_by_type(arch)

    seen: Set[str] = {switch_id}
    q = deque([switch_id])

    while q:
        node = q.popleft()
        for nb in adj.get(node, []):
            if nb in seen:
                continue
            seen.add(nb)

            if types.get(nb) == "router":
                return nb

            if types.get(nb) in {"switch", "pc", "server", "firewall"}:
                q.append(nb)

    return None


def _find_router_for_host(host_id: str, arch: Architecture) -> str | None:
    adj = _adjacency(arch)
    types = _components_by_type(arch)

    seen: Set[str] = {host_id}
    q = deque([host_id])

    while q:
        node = q.popleft()
        for nb in adj.get(node, []):
            if nb in seen:
                continue
            seen.add(nb)

            if types.get(nb) == "router":
                return nb

            if types.get(nb) in {"switch", "pc", "server", "firewall"}:
                q.append(nb)

    return None


def _router_links(arch: Architecture) -> List[List[str]]:
    types = _components_by_type(arch)
    links = []
    seen = set()

    for e in arch.edges:
        a, b = e.from_, e.to
        if types.get(a) == "router" and types.get(b) == "router":
            pair = tuple(sorted([a, b]))
            if pair not in seen:
                seen.add(pair)
                links.append([pair[0], pair[1]])

    return links


def _hosts_behind_switch(switch_id: str, arch: Architecture) -> List[str]:
    adj = _adjacency(arch)
    types = _components_by_type(arch)
    hosts = []

    for nb in adj.get(switch_id, []):
        if types.get(nb) in {"pc", "server"}:
            hosts.append(nb)

    return sorted(hosts)


def _direct_hosts_for_router(router_id: str, arch: Architecture) -> List[str]:
    adj = _adjacency(arch)
    types = _components_by_type(arch)
    hosts = []

    for nb in adj.get(router_id, []):
        if types.get(nb) in {"pc", "server"}:
            hosts.append(nb)

    return sorted(hosts)


def _firewalls_attached_to_router(router_id: str, arch: Architecture) -> List[str]:
    adj = _adjacency(arch)
    types = _components_by_type(arch)
    out = []

    for nb in adj.get(router_id, []):
        if types.get(nb) == "firewall":
            out.append(nb)

    return sorted(out)


def _switches_for_router(router_id: str, arch: Architecture) -> List[str]:
    switches = []

    for c in arch.components:
        if c.type != "switch":
            continue

        owner = _find_router_for_switch(c.id, arch)
        if owner == router_id:
            switches.append(c.id)

    return sorted(switches)


def _manual_switch_cidrs_by_router(arch: Architecture) -> Dict[str, List[ipaddress.IPv4Network]]:
    grouped: Dict[str, List[ipaddress.IPv4Network]] = {}

    for sw, cidr in arch.addressing.subnet_bindings.items():
        owner = _find_router_for_switch(sw, arch)
        if owner is None:
            raise ValueError(f"Switch {sw} has a manual subnet but no owning router.")

        grouped.setdefault(owner, []).append(ipaddress.ip_network(cidr, strict=True))

    return grouped


def _smallest_covering_supernet(networks: List[ipaddress.IPv4Network]) -> ipaddress.IPv4Network:
    if not networks:
        raise ValueError("Cannot compute a covering supernet for an empty network list.")

    min_addr = min(int(net.network_address) for net in networks)
    max_addr = max(int(net.broadcast_address) for net in networks)

    xor = min_addr ^ max_addr
    prefix = 32

    while xor:
        xor >>= 1
        prefix -= 1

    return ipaddress.ip_network((min_addr, prefix), strict=False)


def _allocate_vpc_cidrs(router_ids: List[str], base_cidr: str) -> Dict[str, str]:
    base = ipaddress.ip_network(base_cidr, strict=True)
    target_prefix = 16 if base.prefixlen <= 16 else min(base.prefixlen + 4, 24)

    subnets = list(base.subnets(new_prefix=target_prefix))
    if len(subnets) < len(router_ids):
        raise ValueError("Not enough address space to allocate per-router VPC CIDRs.")

    return {rid: str(net) for rid, net in zip(sorted(router_ids), subnets)}


def _allocate_manual_vpc_cidrs(
    arch: Architecture,
    base_cidr: str,
    router_ids: List[str],
) -> Dict[str, str]:
    base = ipaddress.ip_network(base_cidr, strict=True)
    grouped = _manual_switch_cidrs_by_router(arch)

    if len(router_ids) == 1:
        rid = sorted(router_ids)[0]

        for net in grouped.get(rid, []):
            if not net.subnet_of(base):
                raise ValueError(
                    f"Manual subnet {net} is not contained in base CIDR {base_cidr}."
                )

        if not (16 <= base.prefixlen <= 28):
            raise ValueError(
                f"Base CIDR {base} is not valid for an AWS VPC. Use a prefix between /16 and /28."
            )

        return {rid: str(base)}

    vpc_map: Dict[str, ipaddress.IPv4Network] = {}
    fallback_needed = False

    for rid in router_ids:
        owned = grouped.get(rid, [])
        if not owned:
            fallback_needed = True
            continue

        vpc = _smallest_covering_supernet(owned)

        if not vpc.subnet_of(base):
            raise ValueError(
                f"Manual subnets for router {rid} are not contained in base CIDR {base_cidr}."
            )

        if vpc.prefixlen > 28:
            fallback_needed = True
            break

        vpc_map[rid] = vpc

    if fallback_needed:
        allocated = _allocate_vpc_cidrs(router_ids, base_cidr)

        for rid in router_ids:
            vpc_net = ipaddress.ip_network(allocated[rid], strict=True)

            for net in grouped.get(rid, []):
                if not net.subnet_of(vpc_net):
                    raise ValueError(
                        f"Manual subnet {net} for router {rid} does not fit inside allocated VPC {vpc_net}. "
                        f"Use a manual subnet aligned with the base CIDR allocation strategy."
                    )

        return allocated

    remaining = [rid for rid in router_ids if rid not in vpc_map]

    if remaining:
        auto_map = _allocate_vpc_cidrs(remaining, base_cidr)

        for rid, cidr in auto_map.items():
            auto_net = ipaddress.ip_network(cidr, strict=True)

            if any(auto_net.overlaps(existing) for existing in vpc_map.values()):
                raise ValueError(
                    f"Auto-allocated VPC {auto_net} for router {rid} overlaps manual-derived VPC space."
                )

            for net in grouped.get(rid, []):
                if not net.subnet_of(auto_net):
                    raise ValueError(
                        f"Manual subnet {net} for router {rid} does not fit inside allocated VPC {auto_net}."
                    )

            vpc_map[rid] = auto_net

    ordered = sorted(vpc_map.items(), key=lambda x: x[0])

    for i in range(len(ordered)):
        rid_a, net_a = ordered[i]
        for j in range(i + 1, len(ordered)):
            rid_b, net_b = ordered[j]
            if net_a.overlaps(net_b):
                raise ValueError(
                    f"Derived VPC CIDRs overlap: {rid_a} -> {net_a}, {rid_b} -> {net_b}"
                )

    return {rid: str(net) for rid, net in ordered}


def _auto_subnet_for_switch(vpc_cidr: str, index: int, host_count: int) -> str:
    vpc = ipaddress.ip_network(vpc_cidr, strict=True)

    # AWS subnet CIDR blocks must be between /16 and /28.
    # A /29 is invalid in AWS even if it has enough theoretical IPs.
    needed = max(host_count + 6, 16)
    bits = math.ceil(math.log2(needed))
    prefix = 32 - bits

    # Keep subnet inside the VPC and never smaller than /28.
    prefix = max(prefix, vpc.prefixlen + 1)
    prefix = min(prefix, 28)

    subnets = list(vpc.subnets(new_prefix=prefix))
    if index >= len(subnets):
        raise ValueError(f"Not enough subnets in {vpc_cidr} for switch allocation.")

    return str(subnets[index])


def _parse_public_hosts(user_text: str, host_components: Dict[str, str]) -> Set[str]:
    lower = user_text.lower()
    public_hosts = set()

    for cid in host_components.keys():
        cid_lower = cid.lower()
        patterns = [
            rf"\b{re.escape(cid_lower)}\b\s+(should\s+be|is)\s+public\b",
            rf"\bpublic\s+access\s+for\s+{re.escape(cid_lower)}\b",
            rf"\bmake\s+{re.escape(cid_lower)}\s+public\b",
        ]

        if any(re.search(p, lower) for p in patterns):
            public_hosts.add(cid)

    return public_hosts




def _parse_explicit_private_hosts(user_text: str, component_ids: Set[str]) -> Set[str]:
    """
    Lab default is: PCs are public/reachable by SSH.
    This parser allows the prompt to override that for specific hosts.
    """
    lower = user_text.lower()
    private_hosts = set()

    for cid in component_ids:
        cid_lower = cid.lower()
        patterns = [
            rf"\b{re.escape(cid_lower)}\b\s+(should\s+be|is)\s+private\b",
            rf"\bmake\s+{re.escape(cid_lower)}\s+private\b",
            rf"\bprivate\s+host\s+{re.escape(cid_lower)}\b",
        ]

        if any(re.search(p, lower) for p in patterns):
            private_hosts.add(cid)

    return private_hosts


def _parse_private_hosts_need_nat(user_text: str, component_ids: Set[str]) -> Set[str]:
    lower = user_text.lower()
    nat_hosts = set()

    for cid in component_ids:
        cid_lower = cid.lower()
        patterns = [
            rf"\b{re.escape(cid_lower)}\b\s+should\s+be\s+private\s+but\s+needs\s+internet\b",
            rf"\b{re.escape(cid_lower)}\b\s+should\s+be\s+private\s+but\s+needs\s+internet\s+access\b",
            rf"\b{re.escape(cid_lower)}\b\s+needs\s+internet\s+access\b",
            rf"\b{re.escape(cid_lower)}\b\s+needs\s+outbound\s+internet\b",
            rf"\b{re.escape(cid_lower)}\b\s+needs\s+outbound\s+internet\s+access\b",
            rf"\b{re.escape(cid_lower)}\b\s+should\s+be\s+private\s+but\s+needs\s+outbound\s+internet\b",
            rf"\b{re.escape(cid_lower)}\b\s+should\s+be\s+private\s+but\s+needs\s+outbound\s+internet\s+access\b",
            rf"\bprivate\s+subnet\s+with\s+internet\s+for\s+{re.escape(cid_lower)}\b",
        ]

        if any(re.search(p, lower) for p in patterns):
            nat_hosts.add(cid)

    return nat_hosts


def _parse_bastion_hosts(user_text: str, component_ids: Set[str]) -> Set[str]:
    lower = user_text.lower()
    bastions = set()

    for cid in component_ids:
        cid_lower = cid.lower()
        patterns = [
            rf"\b{re.escape(cid_lower)}\b\s+should\s+be\s+the\s+bastion\b",
            rf"\b{re.escape(cid_lower)}\b\s+should\s+be\s+bastion\b",
            rf"\bmake\s+{re.escape(cid_lower)}\s+the\s+bastion\b",
            rf"\bmake\s+{re.escape(cid_lower)}\s+bastion\b",
            rf"\bbastion\s+host\s+is\s+{re.escape(cid_lower)}\b",
            rf"\bonly\s+{re.escape(cid_lower)}\s+should\s+be\s+public\b",
        ]

        if any(re.search(p, lower) for p in patterns):
            bastions.add(cid)

    return bastions


def _assign_host_ips(
    subnet_cidr: str,
    hosts: List[str],
    public_hosts: Set[str],
    nat_hosts: Set[str],
    bastion_hosts: Set[str],
) -> List[HostPlacement]:
    net = ipaddress.ip_network(subnet_cidr, strict=True)
    usable = list(net.hosts())
    placements = []

    start_index = 3  # AWS-safe host placement starts at .4

    for i, host in enumerate(hosts):
        idx = start_index + i

        if idx >= len(usable):
            raise ValueError(
                f"Subnet {subnet_cidr} too small for AWS-safe host placement for hosts {hosts}"
            )

        placements.append(
            HostPlacement(
                host_id=host,
                private_ip=str(usable[idx]),
                exposure="public" if host in public_hosts else "private",
                needs_outbound_internet=(host in nat_hosts),
                is_bastion=(host in bastion_hosts),
            )
        )

    return placements


def _router_has_link(router_id: str, router_links: List[List[str]]) -> bool:
    return any(router_id in pair for pair in router_links)


def _allocate_transit_subnet(vpc_cidr: str) -> str:
    vpc = ipaddress.ip_network(vpc_cidr, strict=True)
    candidates = list(vpc.subnets(new_prefix=28))

    if not candidates:
        raise ValueError(f"No /28 transit subnet available inside {vpc_cidr}")

    return str(candidates[-1])


def _split_subnet_for_mixed_exposure(base_cidr: str) -> tuple[str, str]:
    net = ipaddress.ip_network(base_cidr, strict=True)

    if net.prefixlen >= 28:
        raise ValueError(
            f"Subnet {base_cidr} is too small for mixed public/private splitting. "
            f"Use a larger subnet such as /27."
        )

    new_prefix = net.prefixlen + 1
    children = list(net.subnets(new_prefix=new_prefix))

    if len(children) < 2:
        raise ValueError(f"Could not split subnet {base_cidr}")

    if children[0].prefixlen > 28 or children[1].prefixlen > 28:
        raise ValueError(
            f"Subnet {base_cidr} is too small for mixed public/private splitting. "
            f"Use a larger subnet such as /27."
        )

    return str(children[0]), str(children[1])


def build_domain_plan(arch: Architecture, user_text: str = "") -> Architecture:
    # KB-backed translation rule:
    # If the user provides only PCs or PCs+switches, infer a logical VPC
    # routing domain. This adds R1 with interfaces=0, meaning VPC owner only; the
    # Terraform template MUST NOT create an EC2 router appliance for it.
    arch = _infer_logical_vpc_domain_if_missing(arch)

    routers = sorted([c.id for c in arch.components if c.type == "router"])

    if not routers:
        return arch

    components = {c.id: c.type for c in arch.components}
    host_components = {
        cid: ctype
        for cid, ctype in components.items()
        if ctype in {"pc", "server"}
    }
    host_ids = set(host_components.keys())

    public_hosts = _parse_public_hosts(user_text, host_components)
    nat_hosts = _parse_private_hosts_need_nat(user_text, host_ids)
    bastion_hosts = _parse_bastion_hosts(user_text, host_ids)
    explicit_private_hosts = _parse_explicit_private_hosts(user_text, host_ids)

    # LAB DEFAULT:
    # PCs are public/reachable by SSH unless the prompt explicitly says private.
    # Servers remain private unless explicitly marked public.
    lab_public_pcs = {
        cid
        for cid, ctype in host_components.items()
        if ctype == "pc"
    }

    public_hosts |= lab_public_pcs
    public_hosts |= bastion_hosts
    public_hosts -= explicit_private_hosts

    base_cidr = arch.addressing.base_cidr or DEFAULT_BASE_CIDR

    if arch.addressing.mode == "manual" and arch.addressing.subnet_bindings:
        vpc_map = _allocate_manual_vpc_cidrs(arch, base_cidr, routers)
    else:
        vpc_map = _allocate_vpc_cidrs(routers, base_cidr)

    router_links = _router_links(arch)

    plan = DomainPlan()

    if len(router_links) == 0:
        plan.connectivity_mode = "none"
    elif len(routers) <= 2:
        plan.connectivity_mode = "peering"
    else:
        plan.connectivity_mode = "tgw"

    for rid in routers:
        domain = RouterDomain(
            router_id=rid,
            vpc_cidr=vpc_map[rid],
            subnets=[],
            attached_firewalls=_firewalls_attached_to_router(rid, arch),
        )

        switches = _switches_for_router(rid, arch)

        for idx, sw in enumerate(switches):
            hosts = _hosts_behind_switch(sw, arch)

            if arch.addressing.mode == "manual" and sw in arch.addressing.subnet_bindings:
                cidr = arch.addressing.subnet_bindings[sw]

                subnet_net = ipaddress.ip_network(cidr, strict=True)
                vpc_net = ipaddress.ip_network(domain.vpc_cidr, strict=True)

                if not subnet_net.subnet_of(vpc_net):
                    raise ValueError(
                        f"Manual subnet {cidr} for switch {sw} is outside VPC {domain.vpc_cidr} of router {rid}."
                    )
            else:
                cidr = _auto_subnet_for_switch(domain.vpc_cidr, idx, len(hosts) + 1)

            public_members = [h for h in hosts if h in public_hosts]
            private_members = [h for h in hosts if h not in public_hosts]

            if public_members and private_members:
                public_cidr, private_cidr = _split_subnet_for_mixed_exposure(cidr)

                public_placements = _assign_host_ips(
                    public_cidr,
                    public_members,
                    public_hosts,
                    nat_hosts,
                    bastion_hosts,
                )
                private_placements = _assign_host_ips(
                    private_cidr,
                    private_members,
                    public_hosts,
                    nat_hosts,
                    bastion_hosts,
                )

                domain.subnets.append(
                    RouterSubnet(
                        name=f"{sw}_PUBLIC",
                        cidr=public_cidr,
                        switch=sw,
                        hosts=public_members,
                        host_placements=public_placements,
                        public=True,
                        purpose="lan",
                        needs_nat=False,
                    )
                )

                domain.subnets.append(
                    RouterSubnet(
                        name=f"{sw}_PRIVATE",
                        cidr=private_cidr,
                        switch=sw,
                        hosts=private_members,
                        host_placements=private_placements,
                        public=False,
                        purpose="lan",
                        needs_nat=any(p.needs_outbound_internet for p in private_placements),
                    )
                )
            else:
                placements = _assign_host_ips(
                    cidr,
                    hosts,
                    public_hosts,
                    nat_hosts,
                    bastion_hosts,
                )
                subnet_public = any(p.exposure == "public" for p in placements)
                subnet_needs_nat = (
                    not subnet_public
                    and any(p.needs_outbound_internet for p in placements)
                )

                domain.subnets.append(
                    RouterSubnet(
                        name=sw,
                        cidr=cidr,
                        switch=sw,
                        hosts=hosts,
                        host_placements=placements,
                        public=subnet_public,
                        purpose="lan",
                        needs_nat=subnet_needs_nat,
                    )
                )

        direct_hosts = _direct_hosts_for_router(rid, arch)

        if direct_hosts:
            existing_hosts = {
                host
                for subnet in domain.subnets
                for host in subnet.hosts
            }

            direct_hosts = [h for h in direct_hosts if h not in existing_hosts]

            if direct_hosts:
                cidr = _auto_subnet_for_switch(
                    domain.vpc_cidr,
                    len(domain.subnets),
                    len(direct_hosts) + 1,
                )

                placements = _assign_host_ips(
                    cidr,
                    direct_hosts,
                    public_hosts,
                    nat_hosts,
                    bastion_hosts,
                )

                subnet_public = any(p.exposure == "public" for p in placements)
                subnet_needs_nat = (
                    not subnet_public
                    and any(p.needs_outbound_internet for p in placements)
                )

                domain.subnets.append(
                    RouterSubnet(
                        name=f"{rid}_DIRECT",
                        cidr=cidr,
                        switch=None,
                        hosts=direct_hosts,
                        host_placements=placements,
                        public=subnet_public,
                        purpose="lan",
                        needs_nat=subnet_needs_nat,
                    )
                )

        if (
            plan.connectivity_mode == "tgw"
            and len(domain.subnets) == 0
            and _router_has_link(rid, router_links)
        ):
            transit_cidr = _allocate_transit_subnet(domain.vpc_cidr)

            domain.subnets.append(
                RouterSubnet(
                    name=f"{rid}_TRANSIT",
                    cidr=transit_cidr,
                    switch=None,
                    hosts=[],
                    host_placements=[],
                    public=False,
                    purpose="transit",
                    needs_nat=False,
                )
            )

        plan.routers[rid] = domain

    plan.router_links = router_links
    arch.domain_plan = plan

    return arch
