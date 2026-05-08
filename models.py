from __future__ import annotations

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


ComponentType = Literal["router", "switch", "server", "pc", "firewall"]
FirewallMode = Optional[Literal["sg", "aws_network_firewall", "appliance"]]
ExposureType = Literal["public", "private"]
AddressingMode = Optional[Literal["manual", "auto"]]
ConnectivityMode = Literal["none", "peering", "tgw"]
SubnetPurpose = Literal["lan", "transit"]


class Component(BaseModel):
    id: str
    type: ComponentType
    interfaces: Optional[int] = None


class Edge(BaseModel):
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class Addressing(BaseModel):
    mode: AddressingMode = None
    cidrs: List[str] = Field(default_factory=list)
    base_cidr: Optional[str] = None
    subnet_bindings: Dict[str, str] = Field(default_factory=dict)
    subnets: List[dict] = Field(default_factory=list)


class FirewallPolicy(BaseModel):
    mode: FirewallMode = None


class UserPolicies(BaseModel):
    allow_auto_addressing: bool = False


class HostPlacement(BaseModel):
    host_id: str
    private_ip: Optional[str] = None
    exposure: ExposureType = "private"
    needs_outbound_internet: bool = False
    is_bastion: bool = False


class RouterSubnet(BaseModel):
    name: str
    cidr: str
    switch: Optional[str] = None
    hosts: List[str] = Field(default_factory=list)
    host_placements: List[HostPlacement] = Field(default_factory=list)
    public: bool = False
    purpose: SubnetPurpose = "lan"
    needs_nat: bool = False


class RouterDomain(BaseModel):
    router_id: str
    vpc_cidr: str
    subnets: List[RouterSubnet] = Field(default_factory=list)
    attached_firewalls: List[str] = Field(default_factory=list)


class DomainPlan(BaseModel):
    routers: Dict[str, RouterDomain] = Field(default_factory=dict)
    router_links: List[List[str]] = Field(default_factory=list)
    connectivity_mode: ConnectivityMode = "none"


class Architecture(BaseModel):
    components: List[Component] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)
    addressing: Addressing = Field(default_factory=Addressing)
    firewall_policy: FirewallPolicy = Field(default_factory=FirewallPolicy)
    user_policies: UserPolicies = Field(default_factory=UserPolicies)
    domain_plan: DomainPlan = Field(default_factory=DomainPlan)
