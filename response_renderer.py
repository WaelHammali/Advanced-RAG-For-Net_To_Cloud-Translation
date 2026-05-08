from __future__ import annotations

from typing import Any, Dict


def build_rendered_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a simple structured response object for generated infrastructure.

    app.py passes the full result dict here.
    """

    architecture = result.get("architecture", {}) or {}
    generated_files = result.get("generated_files", {}) or {}
    rag_plan = result.get("rag_plan", {}) or {}
    quality = result.get("quality", {}) or {}
    spec_guard = result.get("spec_guard", {}) or {}

    domain_plan = architecture.get("domain_plan", {}) or {}
    routers = domain_plan.get("routers", {}) or {}

    sections = []

    sections.append(
        {
            "title": "Generation Summary",
            "items": [
                f"Status: {result.get('status')}",
                f"Connectivity mode: {domain_plan.get('connectivity_mode', 'none')}",
                f"Routers: {len(routers)}",
                f"Generated files: {len(generated_files)}",
            ],
        }
    )

    if rag_plan:
        sections.append(
            {
                "title": "RAG Plan",
                "items": [
                    f"Connectivity mode: {rag_plan.get('connectivity_mode')}",
                    f"Public/private strategy: {rag_plan.get('public_private_strategy')}",
                    f"NAT required: {rag_plan.get('nat_required')}",
                    f"Bastion required: {rag_plan.get('bastion_required')}",
                ],
            }
        )

    if quality:
        sections.append(
            {
                "title": "Quality Checks",
                "items": [
                    f"Terraform validate: {quality.get('terraform_validate_ok', quality.get('ok', 'unknown'))}",
                ],
            }
        )

    if spec_guard:
        sections.append(
            {
                "title": "Spec Guard",
                "items": [
                    f"Passed: {spec_guard.get('passed', spec_guard.get('ok'))}",
                    f"Issues: {len(spec_guard.get('issues', []) or [])}",
                    f"Warnings: {len(spec_guard.get('warnings', []) or [])}",
                ],
            }
        )

    ssh_access_plan = {}

    for rid, router in routers.items():
        for subnet in router.get("subnets", []) or []:
            for hp in subnet.get("host_placements", []) or []:
                host_id = hp.get("host_id")
                exposure = hp.get("exposure")
                if host_id and exposure == "public":
                    key = host_id.lower()
                    ssh_access_plan[host_id] = {
                        "pem_file_output": f"{key}_pem_file",
                        "public_ip_output": f"{key}_public_ip",
                        "ssh_command_output": f"{key}_ssh_command",
                    }

    return {
        "sections": sections,
        "ssh_access_plan": ssh_access_plan,
        "outputs": {},
        "notes_assumptions": [
            "Generated Terraform files are saved in the selected output directory.",
            "Run terraform apply before using generated Ansible inventory with real public IPs.",
        ],
    }
