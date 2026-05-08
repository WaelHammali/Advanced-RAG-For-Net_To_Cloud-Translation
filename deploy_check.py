from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any


PROJECT_ROOT = Path("/kaggle/working/net2tf_v3")
GENERATED_DIR = PROJECT_ROOT / "generated"
ANSIBLE_DIR = GENERATED_DIR / "ansible"
PROMPT_FILE = PROJECT_ROOT / "prompt.txt"


def run_cmd(cmd: list[str], cwd: str | Path) -> Dict[str, Any]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
        }
    except FileNotFoundError as e:
        return {
            "ok": False,
            "returncode": 999,
            "stdout": "",
            "stderr": str(e),
        }


def check_prereqs() -> Dict[str, Any]:
    aws_env = {
        "AWS_ACCESS_KEY_ID": bool(os.environ.get("AWS_ACCESS_KEY_ID")),
        "AWS_SECRET_ACCESS_KEY": bool(os.environ.get("AWS_SECRET_ACCESS_KEY")),
        "AWS_DEFAULT_REGION": bool(os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")),
    }

    return {
        "terraform_in_path": shutil.which("terraform") is not None,
        "ansible_in_path": shutil.which("ansible-playbook") is not None,
        "generated_dir_exists": GENERATED_DIR.exists(),
        "main_tf_exists": (GENERATED_DIR / "main.tf").exists(),
        "prompt_exists": PROMPT_FILE.exists(),
        "aws_env": aws_env,
        "aws_ready": all(aws_env.values()),
    }


def ensure_generated() -> Dict[str, Any]:
    if (GENERATED_DIR / "main.tf").exists():
        return {
            "ok": True,
            "skipped": True,
            "reason": "generated/main.tf already exists",
        }

    if not PROMPT_FILE.exists():
        return {
            "ok": False,
            "skipped": False,
            "reason": "prompt.txt not found",
        }

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    gen = run_cmd(
        ["python", "app.py", "generate", "--input", "prompt.txt", "--out", "./generated"],
        PROJECT_ROOT,
    )

    return {
        "ok": gen["ok"] and (GENERATED_DIR / "main.tf").exists(),
        "skipped": False,
        "reason": "generation attempted",
        "generate_result": gen,
        "main_tf_exists_after": (GENERATED_DIR / "main.tf").exists(),
    }


def terraform_init() -> Dict[str, Any]:
    return run_cmd(["terraform", "init", "-input=false", "-no-color"], GENERATED_DIR)


def terraform_fmt() -> Dict[str, Any]:
    return run_cmd(["terraform", "fmt", "-recursive", "-no-color"], GENERATED_DIR)


def terraform_validate() -> Dict[str, Any]:
    return run_cmd(["terraform", "validate", "-no-color"], GENERATED_DIR)


def terraform_plan() -> Dict[str, Any]:
    return run_cmd(
        [
            "terraform",
            "plan",
            "-input=false",
            "-no-color",
            "-out=tfplan",
        ],
        GENERATED_DIR,
    )


def terraform_show_plan() -> Dict[str, Any]:
    return run_cmd(
        [
            "terraform",
            "show",
            "-json",
            "tfplan",
        ],
        GENERATED_DIR,
    )


def terraform_apply() -> Dict[str, Any]:
    return run_cmd(
        [
            "terraform",
            "apply",
            "-input=false",
            "-no-color",
            "-auto-approve",
            "tfplan",
        ],
        GENERATED_DIR,
    )


def terraform_output_json() -> Dict[str, Any]:
    return run_cmd(
        [
            "terraform",
            "output",
            "-json",
        ],
        GENERATED_DIR,
    )


def save_terraform_outputs(outputs_result: Dict[str, Any]) -> Dict[str, Any]:
    if not outputs_result.get("ok"):
        return {
            "ok": False,
            "path": None,
            "reason": "terraform output failed",
        }

    try:
        data = json.loads(outputs_result.get("stdout", "{}"))
        output_path = GENERATED_DIR / "terraform_outputs.json"
        output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        return {
            "ok": True,
            "path": str(output_path),
            "keys": sorted(data.keys()),
        }
    except Exception as e:
        return {
            "ok": False,
            "path": None,
            "reason": str(e),
        }


def terraform_destroy() -> Dict[str, Any]:
    if not (GENERATED_DIR / "main.tf").exists():
        return {
            "ok": False,
            "returncode": 998,
            "stdout": "",
            "stderr": "generated/main.tf not found",
        }

    return run_cmd(
        [
            "terraform",
            "destroy",
            "-input=false",
            "-no-color",
            "-auto-approve",
        ],
        GENERATED_DIR,
    )


def summarize_outputs(outputs_json_text: str) -> Dict[str, Any]:
    try:
        data = json.loads(outputs_json_text)
    except Exception:
        return {"parsed": False, "keys": []}

    return {
        "parsed": True,
        "keys": sorted(list(data.keys())),
    }


def generate_ansible_after_apply(ansible_request: str) -> Dict[str, Any]:
    if not ansible_request.strip():
        return {
            "status": "skipped",
            "reason": "No Ansible request provided.",
        }

    result_file = GENERATED_DIR / "last_result.json"

    if not result_file.exists():
        return {
            "status": "error",
            "error": "generated/last_result.json not found. Use the updated notebook cell that saves last_result.json, or run app.py generate first.",
        }

    previous = json.loads(result_file.read_text(encoding="utf-8"))
    architecture = previous.get("architecture")

    if not architecture:
        return {
            "status": "error",
            "error": "No architecture found in generated/last_result.json.",
        }

    from app import generate_ansible_config

    return generate_ansible_config(
        ansible_prompt=ansible_request,
        architecture=architecture,
        out_dir=str(ANSIBLE_DIR),
        terraform_generated_dir=str(GENERATED_DIR),
    )


def run_ansible_playbook() -> Dict[str, Any]:
    if not (ANSIBLE_DIR / "playbook.yml").exists():
        return {
            "ok": False,
            "returncode": 997,
            "stdout": "",
            "stderr": "generated/ansible/playbook.yml not found",
        }

    return run_cmd(["ansible-playbook", "playbook.yml"], ANSIBLE_DIR)


def run_plan_only() -> Dict[str, Any]:
    prereqs = check_prereqs()

    result = {
        "mode": "plan_only",
        "prereqs": prereqs,
        "ensure_generated": {},
        "fmt": {},
        "init": {},
        "validate": {},
        "plan": {},
        "plan_json": {},
        "overall": {},
    }

    if not prereqs["terraform_in_path"]:
        result["overall"] = {
            "ok": False,
            "decision": "Terraform is missing.",
        }
        return result

    result["ensure_generated"] = ensure_generated()
    if not result["ensure_generated"]["ok"]:
        result["overall"] = {
            "ok": False,
            "decision": "Terraform files are missing and regeneration failed.",
        }
        return result

    result["fmt"] = terraform_fmt()
    result["init"] = terraform_init()
    result["validate"] = terraform_validate()
    result["plan"] = terraform_plan()

    if result["plan"]["ok"]:
        result["plan_json"] = terraform_show_plan()
    else:
        result["plan_json"] = {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "plan failed",
        }

    result["overall"] = {
        "ok": (
            result["ensure_generated"]["ok"]
            and result["fmt"]["ok"]
            and result["init"]["ok"]
            and result["validate"]["ok"]
            and result["plan"]["ok"]
            and result["plan_json"]["ok"]
        ),
        "decision": (
            "PLAN READY"
            if (
                result["ensure_generated"]["ok"]
                and result["fmt"]["ok"]
                and result["init"]["ok"]
                and result["validate"]["ok"]
                and result["plan"]["ok"]
                and result["plan_json"]["ok"]
            )
            else "PLAN FAILED"
        ),
    }

    return result


def run_apply_and_verify(
    ansible_request: str = "",
    run_ansible: bool = False,
) -> Dict[str, Any]:
    prereqs = check_prereqs()

    result = {
        "mode": "apply_and_verify",
        "prereqs": prereqs,
        "ensure_generated": {},
        "fmt": {},
        "init": {},
        "validate": {},
        "plan": {},
        "apply": {},
        "outputs_raw": {},
        "outputs_saved": {},
        "outputs_summary": {},
        "ansible_generate": {},
        "ansible_run": {},
        "overall": {},
    }

    if not prereqs["terraform_in_path"]:
        result["overall"] = {
            "ok": False,
            "decision": "Terraform is missing.",
        }
        return result

    if not prereqs["aws_ready"]:
        result["overall"] = {
            "ok": False,
            "decision": "AWS credentials/region are not fully configured.",
        }
        return result

    if ansible_request and not prereqs["ansible_in_path"]:
        result["overall"] = {
            "ok": False,
            "decision": "Ansible request was provided but ansible-playbook is missing.",
        }
        return result

    result["ensure_generated"] = ensure_generated()
    if not result["ensure_generated"]["ok"]:
        result["overall"] = {
            "ok": False,
            "decision": "Terraform files are missing and regeneration failed.",
        }
        return result

    result["fmt"] = terraform_fmt()
    result["init"] = terraform_init()
    result["validate"] = terraform_validate()
    result["plan"] = terraform_plan()

    if not result["plan"]["ok"]:
        result["apply"] = {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "plan failed",
        }
        result["overall"] = {
            "ok": False,
            "decision": "PLAN FAILED",
        }
        return result

    result["apply"] = terraform_apply()

    if result["apply"]["ok"]:
        result["outputs_raw"] = terraform_output_json()
        result["outputs_saved"] = save_terraform_outputs(result["outputs_raw"])

        if result["outputs_raw"]["ok"]:
            result["outputs_summary"] = summarize_outputs(result["outputs_raw"]["stdout"])
        else:
            result["outputs_summary"] = {"parsed": False, "keys": []}
    else:
        result["outputs_raw"] = {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "apply failed",
        }
        result["outputs_saved"] = {
            "ok": False,
            "path": None,
            "reason": "apply failed",
        }
        result["outputs_summary"] = {"parsed": False, "keys": []}

    if result["apply"]["ok"] and ansible_request:
        result["ansible_generate"] = generate_ansible_after_apply(ansible_request)

        if run_ansible and result["ansible_generate"].get("status") == "ok":
            result["ansible_run"] = run_ansible_playbook()
        elif run_ansible:
            result["ansible_run"] = {
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": "Ansible generation failed or was skipped.",
            }
        else:
            result["ansible_run"] = {
                "ok": True,
                "skipped": True,
                "reason": "Ansible playbook execution not requested.",
            }
    else:
        result["ansible_generate"] = {
            "status": "skipped",
            "reason": "No Ansible request provided or Terraform apply failed.",
        }
        result["ansible_run"] = {
            "ok": True,
            "skipped": True,
            "reason": "No Ansible execution requested.",
        }

    terraform_ok = (
        result["ensure_generated"]["ok"]
        and result["fmt"]["ok"]
        and result["init"]["ok"]
        and result["validate"]["ok"]
        and result["plan"]["ok"]
        and result["apply"]["ok"]
        and result["outputs_raw"]["ok"]
        and result["outputs_summary"]["parsed"]
    )

    ansible_ok = True
    if ansible_request:
        ansible_ok = result["ansible_generate"].get("status") == "ok"
        if run_ansible:
            ansible_ok = ansible_ok and result["ansible_run"].get("ok") is True

    result["overall"] = {
        "ok": terraform_ok and ansible_ok,
        "decision": (
            "APPLY + ANSIBLE SUCCESS"
            if terraform_ok and ansible_ok and ansible_request
            else "APPLY SUCCESS"
            if terraform_ok and not ansible_request
            else "APPLY OR ANSIBLE FAILED"
        ),
    }

    return result


def run_destroy_only() -> Dict[str, Any]:
    prereqs = check_prereqs()

    result = {
        "mode": "destroy_only",
        "prereqs": prereqs,
        "destroy": {},
        "overall": {},
    }

    if not prereqs["terraform_in_path"]:
        result["overall"] = {
            "ok": False,
            "decision": "Terraform is missing.",
        }
        return result

    if not prereqs["aws_ready"]:
        result["overall"] = {
            "ok": False,
            "decision": "AWS credentials/region are not fully configured.",
        }
        return result

    result["destroy"] = terraform_destroy()
    result["overall"] = {
        "ok": result["destroy"]["ok"],
        "decision": "DESTROY SUCCESS" if result["destroy"]["ok"] else "DESTROY FAILED",
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["plan_only", "apply_and_verify", "destroy_only"])
    parser.add_argument("--ansible-request", default="", help="Optional Ansible configuration request after Terraform apply")
    parser.add_argument("--run-ansible", action="store_true", help="Actually run ansible-playbook after generation")
    args = parser.parse_args()

    if args.mode == "plan_only":
        report = run_plan_only()
    elif args.mode == "apply_and_verify":
        report = run_apply_and_verify(
            ansible_request=args.ansible_request,
            run_ansible=args.run_ansible,
        )
    else:
        report = run_destroy_only()

    print(json.dumps(report, indent=2))
