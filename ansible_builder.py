from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple


def _read_outputs(terraform_dir: str | Path) -> Dict[str, Any]:
    terraform_dir = Path(terraform_dir)
    p = terraform_dir / "terraform_outputs.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

    out = {}
    for k, v in data.items():
        if isinstance(v, dict) and "value" in v:
            out[k] = v["value"]
        else:
            out[k] = v
    return out


def _wants_ospf(text: str) -> bool:
    t = (text or "").lower()
    return any(x in t for x in ["ospf", "frr", "frrouting", "vyos"])


def _wants_nginx(text: str) -> bool:
    return "nginx" in (text or "").lower()


def _extract_pc_targets(text: str) -> List[str]:
    pcs = sorted(set(re.findall(r"\bpc\d+\b", (text or "").lower())))
    return [x.upper() for x in pcs]


def _output(outputs: Dict[str, Any], name: str, default: Any = None) -> Any:
    return outputs.get(name, default)


def _inventory_line(name: str, public_ip: str, pem_file: str, extra: str = "") -> str:
    extra = f" {extra}" if extra else ""
    return (
        f"{name} ansible_host={public_ip} ansible_user=ec2-user "
        f"ansible_ssh_private_key_file=../{pem_file}{extra}"
    )


def render_ansible_project(
    ansible_prompt: str,
    terraform_dir: str | Path = "generated",
    out_dir: str | Path | None = None,
) -> Dict[str, Any]:
    terraform_dir = Path(terraform_dir)
    out_dir = Path(out_dir) if out_dir else terraform_dir / "ansible"
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = _read_outputs(terraform_dir)
    lower = (ansible_prompt or "").lower()

    wants_nginx = _wants_nginx(ansible_prompt)
    wants_ospf = _wants_ospf(ansible_prompt)

    pc_targets = _extract_pc_targets(ansible_prompt)
    if wants_nginx and not pc_targets:
        pc_targets = ["PC1"]

    inventory_lines: List[str] = []

    if pc_targets:
        inventory_lines.append("[pcs]")
        for pc in pc_targets:
            key = pc.lower()
            public_ip = _output(outputs, f"{key}_public_ip", "PUBLIC_IP_AFTER_DEPLOY")
            pem_file = _output(outputs, f"{key}_pem_file", f"{pc}.pem")
            inventory_lines.append(_inventory_line(pc, public_ip, pem_file))
        inventory_lines.append("")

    router_targets: List[Tuple[str, str, str, str, str]] = []
    if wants_ospf:
        for idx in range(1, 9):
            rid = f"R{idx}"
            key = rid.lower()

            public_ip = (
                _output(outputs, f"{key}_router_public_ip")
                or _output(outputs, f"{key}_public_ip")
            )
            if not public_ip:
                continue

            pem_file = (
                _output(outputs, f"{key}_router_pem_file")
                or _output(outputs, f"{key}_pem_file")
                or "PC1.pem"
            )
            private_ip = (
                _output(outputs, f"{key}_router_private_ip")
                or _output(outputs, f"{key}_private_ip")
            )

            if isinstance(private_ip, str) and len(private_ip.split(".")) == 4:
                a, b, c, _ = private_ip.split(".")
                network = f"{a}.{b}.{c}.0/27"
            else:
                network = f"10.{idx - 1}.0.0/16"

            router_targets.append((rid, public_ip, pem_file, f"{idx}.{idx}.{idx}.{idx}", network))

        if router_targets:
            inventory_lines.append("[routers]")
            for rid, public_ip, pem_file, router_id, network in router_targets:
                extra = f"router_id={router_id} ospf_network={network}"
                inventory_lines.append(_inventory_line(rid, public_ip, pem_file, extra))
            inventory_lines.append("")

    inventory_lines.append("[all:vars]")
    inventory_lines.append("ansible_ssh_common_args='-o StrictHostKeyChecking=no'")

    inventory_text = "\n".join(inventory_lines) + "\n"

    playbook_parts: List[str] = ["---"]

    if wants_nginx and pc_targets:
        playbook_parts.append("""
- name: Install and start nginx on selected PCs
  hosts: pcs
  become: true
  tasks:
    - name: Install nginx
      ansible.builtin.package:
        name: nginx
        state: present

    - name: Start and enable nginx
      ansible.builtin.service:
        name: nginx
        state: started
        enabled: true

    - name: Test nginx locally
      ansible.builtin.command: curl -I http://127.0.0.1
      register: nginx_check
      changed_when: false
      failed_when: false

    - name: Show nginx result
      ansible.builtin.debug:
        var: nginx_check.stdout_lines
""")

    if wants_ospf and router_targets:
        playbook_parts.append("""
- name: Configure FRRouting OSPF on router appliances
  hosts: routers
  become: true
  tasks:
    - name: Enable Linux IPv4 forwarding
      ansible.posix.sysctl:
        name: net.ipv4.ip_forward
        value: "1"
        state: present
        reload: true

    - name: Install FRRouting
      ansible.builtin.package:
        name: frr
        state: present

    - name: Enable ospfd daemon
      ansible.builtin.lineinfile:
        path: /etc/frr/daemons
        regexp: '^ospfd='
        line: 'ospfd=yes'
        create: true

    - name: Write FRR OSPF config
      ansible.builtin.copy:
        dest: /etc/frr/frr.conf
        owner: frr
        group: frr
        mode: "0640"
        content: |
          frr version 8.0
          frr defaults traditional
          hostname {{ inventory_hostname }}
          service integrated-vtysh-config
          !
          router ospf
           ospf router-id {{ router_id }}
           network {{ ospf_network }} area 0
          !
          line vty
          !

    - name: Restart FRRouting
      ansible.builtin.service:
        name: frr
        state: restarted
        enabled: true

    - name: Show OSPF neighbors
      ansible.builtin.command: vtysh -c "show ip ospf neighbor"
      register: ospf_neighbors
      changed_when: false
      failed_when: false

    - name: Print OSPF neighbors
      ansible.builtin.debug:
        var: ospf_neighbors.stdout_lines
""")

    if len(playbook_parts) == 1:
        playbook_parts.append("""
- name: Placeholder for unsupported Ansible request
  hosts: all
  gather_facts: false
  tasks:
    - name: Show request
      ansible.builtin.debug:
        msg: "No supported task detected. Try nginx, ospf, frr, frrouting, or vyos."
""")

    playbook_text = "\n".join(playbook_parts).strip() + "\n"

    ansible_cfg = """[defaults]
host_key_checking = False
inventory = inventory.ini
retry_files_enabled = False
stdout_callback = yaml
"""

    plan = {
        "status": "ok",
        "ansible_prompt": ansible_prompt,
        "target_hosts": pc_targets,
        "router_targets": [x[0] for x in router_targets],
        "uses_real_outputs": bool(outputs),
        "notes": [
            "PCs use their own PEM files.",
            "Routers use router PEM output if present, otherwise PC1.pem.",
            "If Terraform outputs are missing, inventory uses PUBLIC_IP_AFTER_DEPLOY placeholders.",
        ],
    }

    (out_dir / "inventory.ini").write_text(inventory_text, encoding="utf-8")
    (out_dir / "playbook.yml").write_text(playbook_text, encoding="utf-8")
    (out_dir / "ansible.cfg").write_text(ansible_cfg, encoding="utf-8")
    (out_dir / "ansible_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    (out_dir / "README.md").write_text(
        "# Generated Ansible\n\nRun with:\n\n```bash\nansible-playbook -i inventory.ini playbook.yml\n```\n",
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "generated_files": {
            "inventory.ini": str(out_dir / "inventory.ini"),
            "playbook.yml": str(out_dir / "playbook.yml"),
            "ansible.cfg": str(out_dir / "ansible.cfg"),
            "ansible_plan.json": str(out_dir / "ansible_plan.json"),
            "README.md": str(out_dir / "README.md"),
        },
        "ansible_plan": plan,
    }


def generate_ansible_config(*args, **kwargs):
    """
    Compatibility wrapper for app.py / notebook calls.
    Accepts:
    - generate_ansible_config(ansible_prompt, terraform_dir=..., out_dir=...)
    - generate_ansible_config(terraform_dir=..., request=..., out_dir=...)
    """
    ansible_prompt = kwargs.pop("request", None) or kwargs.pop("ansible_prompt", None)

    terraform_dir = kwargs.pop("terraform_dir", None) or kwargs.pop("generated_dir", None) or "generated"
    out_dir = kwargs.pop("out_dir", None)

    if args:
        if ansible_prompt is None:
            ansible_prompt = args[0]
        elif terraform_dir == "generated":
            terraform_dir = args[0]

    if ansible_prompt is None:
        ansible_prompt = ""

    return render_ansible_project(
        ansible_prompt=ansible_prompt,
        terraform_dir=terraform_dir,
        out_dir=out_dir,
    )
