from __future__ import annotations

import argparse
import json
import os
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from groq import Groq

from extractor import extract_architecture
from validator import validate_architecture
from addressing import enrich_with_manual_addressing, build_domain_plan
from retriever import retrieve_context
from planner import plan_with_rag
from plan_guard import compare_plan_to_compiled
from spec_guard import evaluate_spec_compliance
from quality_checks import run_quality_checks
from response_renderer import build_rendered_response
from terraform_builder import render_project

from ansible_planner import plan_ansible_config
from ansible_builder import render_ansible_project
from ansible_check import run_ansible_syntax_check


ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"


def _as_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj

    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True)

    if hasattr(obj, "dict"):
        return obj.dict(by_alias=True)

    return {}


def _load_json_if_exists(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def _save_result(result: Dict[str, Any], out_dir: str) -> None:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    (out_path / "last_result.json").write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )


def _normalize_validation(validation_raw: Any) -> Dict[str, Any]:
    """
    validator.py returns a list:
      [] means valid
      ["issue"] means invalid

    This helper converts it to:
      {"ok": True/False, "issues": [...]}
    """
    if isinstance(validation_raw, list):
        return {
            "ok": len(validation_raw) == 0,
            "issues": validation_raw,
        }

    if isinstance(validation_raw, dict):
        return {
            "ok": bool(validation_raw.get("ok", False)),
            "issues": validation_raw.get("issues", []),
            **validation_raw,
        }

    return {
        "ok": False,
        "issues": [
            f"Unsupported validation result type: {type(validation_raw).__name__}"
        ],
    }


def _apply_firewall_default(architecture: Any, prompt: str = "") -> Any:
    """
    Default firewall mode to SG when a firewall exists and the user did not
    explicitly request a firewall appliance.
    """
    arch = _as_dict(architecture)
    components = arch.get("components", []) or []
    has_firewall = any(c.get("type") == "firewall" for c in components)

    if not has_firewall:
        return architecture

    prompt_lower = (prompt or "").lower()
    explicit_appliance = (
        "appliance" in prompt_lower
        or "firewall appliance" in prompt_lower
        or "firewall mode is appliance" in prompt_lower
    )

    if isinstance(architecture, dict):
        architecture.setdefault("firewall_policy", {})
        current_mode = architecture["firewall_policy"].get("mode")

        if current_mode is None or (current_mode == "appliance" and not explicit_appliance):
            architecture["firewall_policy"]["mode"] = "sg"

        return architecture

    firewall_policy = getattr(architecture, "firewall_policy", None)

    if firewall_policy is not None:
        current_mode = getattr(firewall_policy, "mode", None)

        if current_mode is None or (current_mode == "appliance" and not explicit_appliance):
            firewall_policy.mode = "sg"

    return architecture


def _load_terraform_outputs(terraform_generated_dir: str) -> Optional[Dict[str, Any]]:
    path = Path(terraform_generated_dir) / "terraform_outputs.json"
    return _load_json_if_exists(path)


def generate_ansible_config(
    ansible_prompt: str,
    architecture: Dict[str, Any],
    out_dir: str = "./generated/ansible",
    terraform_generated_dir: str = "./generated",
) -> Dict[str, Any]:
    terraform_outputs = _load_terraform_outputs(terraform_generated_dir)

    ansible_plan = plan_ansible_config(
        ansible_prompt=ansible_prompt,
        architecture=architecture,
    )

    generated_files = render_ansible_project(
        ansible_prompt=ansible_prompt,
        architecture=architecture,
        ansible_plan=ansible_plan,
        out_dir=out_dir,
        terraform_outputs=terraform_outputs,
    )

    syntax_check = run_ansible_syntax_check(
        out_dir
    )

    return {
        "status": "ok",
        "ansible_prompt": ansible_prompt,
        "ansible_plan": ansible_plan,
        "terraform_outputs_loaded": terraform_outputs is not None,
        "generated_files": generated_files,
        "syntax_check": syntax_check,
    }


def compile_prompt(prompt: str, out_dir: str = "./generated") -> Dict[str, Any]:
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

    try:
        # 1. Extract architecture using Groq.
        arch = extract_architecture(prompt, client=client)
        arch = enrich_with_manual_addressing(arch, prompt)
        arch = _apply_firewall_default(arch, prompt)

        extracted_arch = _as_dict(arch)

        # 2. Retrieve RAG context.
        retrieved_context = retrieve_context(prompt)

        # 3. Build RAG plan using the real planner.py signature.
        rag_plan = plan_with_rag(
            prompt=prompt,
            extracted_arch=extracted_arch,
            retrieved_chunks=retrieved_context,
            client=client,
        )

        # 4. Validate architecture.
        validation_raw = validate_architecture(arch)
        validation = _normalize_validation(validation_raw)

        if not validation.get("ok", False):
            result = {
                "status": "error",
                "error": "Architecture validation failed.",
                "validation": validation,
                "architecture": extracted_arch,
                "rag_plan": rag_plan,
                "retrieved_context": retrieved_context,
            }
            _save_result(result, out_dir)
            return result

        # 5. Compile final domain plan.
        arch = build_domain_plan(arch, prompt)
        architecture_dict = _as_dict(arch)

        # 6. Render Terraform.
        generated_files = render_project(
            architecture=arch,
            templates_dir=str(TEMPLATES_DIR),
            out_dir=out_dir,
        )

        # 7. Guards and checks.
        # Deterministic correction: explicit no-bastion prompt.
        # If the user says no bastion is required, the planner must not invent one.
        prompt_lower = prompt.lower()
        if (
            "no bastion is required" in prompt_lower
            or "no bastion required" in prompt_lower
            or "bastion is not required" in prompt_lower
            or "do not create bastion" in prompt_lower
            or "don\'t create bastion" in prompt_lower
        ):
            rag_plan["bastion_required"] = False

        plan_guard = compare_plan_to_compiled(
            rag_plan=rag_plan,
            architecture=architecture_dict,
        )

        quality = run_quality_checks(
            generated_dir=out_dir,
        )

        result = {
            "status": "ok",
            "architecture": architecture_dict,
            "rag_plan": rag_plan,
            "retrieved_context": retrieved_context,
            "validation": validation,
            "plan_guard": plan_guard,

            # Keep both names because eval_suite.py expects quality_checks,
            # while other code may use quality.
            "quality": quality,
            "quality_checks": quality,

            "generated_files": generated_files,
        }

        # Current spec_guard.py supports full result dict.
        result["spec_guard"] = evaluate_spec_compliance(result)

        # response_renderer.py expects the full result dict.
        result["rendered_response"] = build_rendered_response(result)

        _save_result(result, out_dir)
        return result

    except Exception as exc:
        architecture_dict = {}

        try:
            if "arch" in locals():
                architecture_dict = _as_dict(arch)
        except Exception:
            architecture_dict = {}

        result = {
            "status": "error",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "architecture": architecture_dict,
            "rendered_response": {
                "sections": [],
                "ssh_access_plan": {},
                "outputs": {},
                "notes_assumptions": [
                    f"Generation failed: {exc}"
                ],
            },
        }

        _save_result(result, out_dir)
        return result


def compile_intake_session(session: Any, out_dir: str = "./generated") -> Dict[str, Any]:
    prompt = session.to_prompt() if hasattr(session, "to_prompt") else str(session)

    # Guided intake explicitly authorizes automatic addressing when the
    # intake session did not provide manual CIDRs.
    if "automatic addressing is allowed" not in prompt.lower():
        prompt = prompt.rstrip() + "\nAutomatic addressing is allowed.\n"

    return compile_prompt(prompt=prompt, out_dir=out_dir)


def _cmd_generate(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    prompt = input_path.read_text(encoding="utf-8")

    result = compile_prompt(
        prompt=prompt,
        out_dir=args.out,
    )

    print(json.dumps(result, indent=2))


def _cmd_generate_ansible(args: argparse.Namespace) -> None:
    last_result_path = Path(args.generated_dir) / "last_result.json"

    if not last_result_path.exists():
        raise FileNotFoundError(
            f"{last_result_path} not found. Generate Terraform first."
        )

    last_result = json.loads(last_result_path.read_text(encoding="utf-8"))

    if last_result.get("status") != "ok":
        raise RuntimeError(
            "Last Terraform generation did not succeed. Cannot generate Ansible."
        )

    architecture = last_result["architecture"]

    result = generate_ansible_config(
        ansible_prompt=args.ansible_request,
        architecture=architecture,
        out_dir=str(Path(args.generated_dir) / "ansible"),
        terraform_generated_dir=args.generated_dir,
    )

    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="net2tf_v3 application CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate Terraform from a topology prompt.",
    )
    generate_parser.add_argument(
        "--input",
        required=True,
        help="Path to prompt.txt",
    )
    generate_parser.add_argument(
        "--out",
        default="./generated",
        help="Output directory for generated Terraform files.",
    )
    generate_parser.set_defaults(func=_cmd_generate)

    ansible_parser = subparsers.add_parser(
        "generate-ansible",
        help="Generate Ansible project from last generated architecture.",
    )
    ansible_parser.add_argument(
        "--generated-dir",
        default="./generated",
        help="Generated Terraform directory.",
    )
    ansible_parser.add_argument(
        "--ansible-request",
        required=True,
        help="User request for Ansible configuration.",
    )
    ansible_parser.set_defaults(func=_cmd_generate_ansible)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
