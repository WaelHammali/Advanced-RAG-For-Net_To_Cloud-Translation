from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from groq import Groq

from config import PLAN_MODEL


ALLOWED_TASK_TYPES = {
    "install_packages",
    "start_service",
    "enable_service",
    "run_command",
    "shell",
    "copy_content",
    "restart_service",
}


def _extract_json(text: str) -> Dict[str, Any]:
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

    raise ValueError("Could not parse JSON from Ansible planner response.")


def _host_ids_from_architecture(architecture: Dict[str, Any]) -> List[str]:
    hosts = []

    for c in architecture.get("components", []) or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") in {"pc", "server"} and isinstance(c.get("id"), str):
            hosts.append(c["id"])

    return sorted(set(hosts))




def _router_ids_from_architecture(architecture: Dict[str, Any]) -> List[str]:
    domain_plan = architecture.get("domain_plan", {}) or {}
    routers = domain_plan.get("routers", {}) or {}

    if isinstance(routers, dict):
        return sorted(str(rid) for rid in routers.keys())

    out = []
    if isinstance(routers, list):
        for r in routers:
            if isinstance(r, dict) and isinstance(r.get("router_id"), str):
                out.append(r["router_id"])

    return sorted(set(out))


def _ospf_requested(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in [
        "ospf",
        "frr",
        "frrouting",
        "vyos",
        "dynamic routing",
        "routing protocol",
    ])


def _normalize_targets(value: Any, valid_hosts: List[str]) -> List[str]:
    valid = set(valid_hosts)

    if not isinstance(value, list):
        return valid_hosts

    out = []
    for x in value:
        if not isinstance(x, str):
            continue
        x = x.strip()
        if x in valid:
            out.append(x)

    return sorted(set(out)) if out else valid_hosts


def _normalize_tasks(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    tasks = []

    for t in value:
        if not isinstance(t, dict):
            continue

        task_type = t.get("type")
        if task_type not in ALLOWED_TASK_TYPES:
            continue

        task = {
            "type": task_type,
            "name": str(t.get("name") or task_type).strip(),
            "packages": [],
            "service": None,
            "command": None,
            "dest": None,
            "content": None,
        }

        packages = t.get("packages", [])
        if isinstance(packages, list):
            task["packages"] = [str(p).strip() for p in packages if str(p).strip()]

        if isinstance(t.get("service"), str):
            task["service"] = t["service"].strip()

        if isinstance(t.get("command"), str):
            task["command"] = t["command"].strip()

        if isinstance(t.get("dest"), str):
            task["dest"] = t["dest"].strip()

        if isinstance(t.get("content"), str):
            task["content"] = t["content"]

        tasks.append(task)

    return tasks


def _heuristic_plan(ansible_prompt: str, architecture: Dict[str, Any]) -> Dict[str, Any]:
    lower = ansible_prompt.lower()

    if _ospf_requested(ansible_prompt):
        routers = _router_ids_from_architecture(architecture)
        ospf_conf = """frr defaults traditional
service integrated-vtysh-config

router ospf
 ospf router-id 1.1.1.1
 network 10.0.0.0/8 area 0
 network 172.16.0.0/12 area 0
 network 192.168.0.0/16 area 0
"""
        return {
            "target_hosts": routers,
            "become": True,
            "tasks": [
                {
                    "type": "run_command",
                    "name": "Enable IPv4 forwarding now",
                    "packages": [],
                    "service": None,
                    "command": "sysctl -w net.ipv4.ip_forward=1",
                    "dest": None,
                    "content": None,
                },
                {
                    "type": "copy_content",
                    "name": "Persist IPv4 forwarding",
                    "packages": [],
                    "service": None,
                    "command": None,
                    "dest": "/etc/sysctl.d/99-net2tf-forwarding.conf",
                    "content": "net.ipv4.ip_forward = 1\n",
                },
                {
                    "type": "run_command",
                    "name": "Apply sysctl settings",
                    "packages": [],
                    "service": None,
                    "command": "sysctl --system",
                    "dest": None,
                    "content": None,
                },
                {
                    "type": "install_packages",
                    "name": "Install FRRouting",
                    "packages": ["frr"],
                    "service": None,
                    "command": None,
                    "dest": None,
                    "content": None,
                },
                {
                    "type": "shell",
                    "name": "Enable OSPF daemon",
                    "packages": [],
                    "service": None,
                    "command": "sed -i 's/^ospfd=no/ospfd=yes/' /etc/frr/daemons || true",
                    "dest": None,
                    "content": None,
                },
                {
                    "type": "copy_content",
                    "name": "Write generic FRR OSPF configuration",
                    "packages": [],
                    "service": None,
                    "command": None,
                    "dest": "/etc/frr/frr.conf",
                    "content": ospf_conf,
                },
                {
                    "type": "enable_service",
                    "name": "Enable FRR",
                    "packages": [],
                    "service": "frr",
                    "command": None,
                    "dest": None,
                    "content": None,
                },
                {
                    "type": "restart_service",
                    "name": "Restart FRR",
                    "packages": [],
                    "service": "frr",
                    "command": None,
                    "dest": None,
                    "content": None,
                },
                {
                    "type": "shell",
                    "name": "Show OSPF neighbor status",
                    "packages": [],
                    "service": None,
                    "command": "vtysh -c 'show ip ospf neighbor' || true",
                    "dest": None,
                    "content": None,
                },
            ],
            "notes": [
                "OSPF was requested in the Ansible input.",
                "Terraform creates EC2 router appliances with source_dest_check=false.",
                "Ansible configures Linux forwarding and FRRouting/ospfd on routers.",
                "Routers use PC1.pem by default.",
            ],
        }

    hosts = _host_ids_from_architecture(architecture)

    targets = []
    for h in hosts:
        if h.lower() in lower:
            targets.append(h)

    if not targets:
        targets = hosts

    tasks = []

    package_aliases = {
        "nginx": "nginx",
        "apache": "httpd",
        "httpd": "httpd",
        "docker": "docker",
        "git": "git",
        "python": "python3",
        "python3": "python3",
    }

    packages = []
    for word, package in package_aliases.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            packages.append(package)

    packages = sorted(set(packages))

    if packages:
        tasks.append({
            "type": "install_packages",
            "name": "Install requested packages",
            "packages": packages,
            "service": None,
            "command": None,
            "dest": None,
            "content": None,
        })

    service_candidates = ["nginx", "httpd", "docker"]
    for svc in service_candidates:
        if svc in packages or svc in lower:
            if "start" in lower or "run" in lower or "enable" in lower:
                tasks.append({
                    "type": "start_service",
                    "name": f"Start {svc}",
                    "packages": [],
                    "service": svc,
                    "command": None,
                    "dest": None,
                    "content": None,
                })
                tasks.append({
                    "type": "enable_service",
                    "name": f"Enable {svc}",
                    "packages": [],
                    "service": svc,
                    "command": None,
                    "dest": None,
                    "content": None,
                })

    if not tasks:
        tasks.append({
            "type": "run_command",
            "name": "Run requested shell command",
            "packages": [],
            "service": None,
            "command": ansible_prompt.strip(),
            "dest": None,
            "content": None,
        })

    return {
        "target_hosts": targets,
        "become": True,
        "tasks": tasks,
        "notes": ["Generated using heuristic fallback."],
    }


def plan_ansible_config(
    ansible_prompt: str,
    architecture: Dict[str, Any],
    client: Groq | None = None,
) -> Dict[str, Any]:
    if _ospf_requested(ansible_prompt):
        valid_hosts = _router_ids_from_architecture(architecture)
    else:
        valid_hosts = _host_ids_from_architecture(architecture)

    if not valid_hosts:
        return {
            "target_hosts": [],
            "become": True,
            "tasks": [],
            "notes": ["No EC2 hosts were found in the compiled architecture."],
        }

    if client is None:
        return _heuristic_plan(ansible_prompt, architecture)

    planner_prompt = f"""
You are an Ansible configuration planner.

The infrastructure has already been generated by Terraform.
You must convert the user's Ansible request into a strict JSON plan.

Valid target hosts:
{json.dumps(valid_hosts, indent=2)}

User Ansible request:
{ansible_prompt}

Return strict JSON only with this schema:

{{
  "target_hosts": ["PC1"],
  "become": true,
  "tasks": [
    {{
      "type": "install_packages",
      "name": "Install nginx",
      "packages": ["nginx"]
    }},
    {{
      "type": "start_service",
      "name": "Start nginx",
      "service": "nginx"
    }},
    {{
      "type": "enable_service",
      "name": "Enable nginx",
      "service": "nginx"
    }},
    {{
      "type": "run_command",
      "name": "Run custom command",
      "command": "echo hello"
    }},
    {{
      "type": "copy_content",
      "name": "Write a file",
      "dest": "/tmp/example.txt",
      "content": "hello"
    }}
  ],
  "notes": ["..."]
}}

Rules:
- target_hosts must only contain hosts from the valid target hosts list.
- If the user does not specify a host, target all valid hosts.
- Allowed task types are:
  install_packages, start_service, enable_service, restart_service, run_command, shell, copy_content
- Do not invent hosts.
- If the user asks for OSPF/FRRouting/VyOS, target routers only and configure FRRouting.
- Do not return markdown.
"""

    try:
        raw = client.chat.completions.create(
            model=PLAN_MODEL,
            messages=[{"role": "user", "content": planner_prompt}],
            temperature=0,
        ).choices[0].message.content

        data = _extract_json(raw)

        plan = {
            "target_hosts": _normalize_targets(data.get("target_hosts"), valid_hosts),
            "become": bool(data.get("become", True)),
            "tasks": _normalize_tasks(data.get("tasks")),
            "notes": [str(x) for x in data.get("notes", [])] if isinstance(data.get("notes"), list) else [],
        }

        if not plan["tasks"]:
            return _heuristic_plan(ansible_prompt, architecture)

        return plan

    except Exception as e:
        fallback = _heuristic_plan(ansible_prompt, architecture)
        fallback["notes"].append(f"LLM Ansible planning failed, used fallback: {str(e)}")
        return fallback
