from __future__ import annotations

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


ComponentType = Literal["router", "switch", "server", "pc", "firewall"]
StageType = Literal[
    "collect_components",
    "collect_edges",
    "resolve_missing_edges",
    "ask_firewall_mode",
    "ask_addressing_mode",
    "collect_base_cidr",
    "collect_subnet_cidrs",
    "collect_host_intent",
    "ready_to_compile",
]

AddressingModeType = Literal["unknown", "auto", "manual"]
FirewallModeType = Literal["unknown", "sg", "aws_network_firewall", "appliance"]


class IntakeComponent(BaseModel):
    id: str
    type: ComponentType
    interfaces: Optional[int] = None


class IntakeEdge(BaseModel):
    from_id: str
    to_id: str


class IntakeAddressing(BaseModel):
    mode: AddressingModeType = "unknown"
    base_cidr: Optional[str] = None
    subnet_bindings: Dict[str, str] = Field(default_factory=dict)


class IntakeHostIntent(BaseModel):
    public_hosts: List[str] = Field(default_factory=list)
    bastion_hosts: List[str] = Field(default_factory=list)
    nat_hosts: List[str] = Field(default_factory=list)


class IntakeSession(BaseModel):
    stage: StageType = "collect_components"
    components: List[IntakeComponent] = Field(default_factory=list)
    edges: List[IntakeEdge] = Field(default_factory=list)

    firewall_mode: FirewallModeType = "unknown"
    addressing: IntakeAddressing = Field(default_factory=IntakeAddressing)
    host_intent: IntakeHostIntent = Field(default_factory=IntakeHostIntent)

    missing_edge_components: List[str] = Field(default_factory=list)
    pending_subnet_components: List[str] = Field(default_factory=list)

    last_question: Optional[str] = None
    ready_to_compile: bool = False


class IntakeDecision(BaseModel):
    can_advance: bool
    next_stage: StageType
    question: Optional[str] = None
    blocking_issues: List[str] = Field(default_factory=list)
    ready_to_compile: bool = False
