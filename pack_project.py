from __future__ import annotations

import json
import os
import shutil
import zipfile
from datetime import datetime, UTC
from pathlib import Path
from typing import Dict, List


BASE = Path("/kaggle/working/net2tf_v3")
DIST = BASE / "dist"
PACKAGE_ROOT = DIST / "net2tf_v3_package"

INCLUDE_PATHS = [
    "app.py",
    "config.py",
    "models.py",
    "extractor.py",
    "validator.py",
    "intake_models.py",
    "interactive_intake.py",
    "addressing.py",
    "retriever.py",
    "planner.py",
    "plan_guard.py",
    "response_renderer.py",
    "terraform_builder.py",
    "quality_checks.py",
    "spec_guard.py",

    # Ansible extension
    "ansible_planner.py",
    "ansible_builder.py",
    "ansible_check.py",

    "eval_suite.py",
    "eval_snapshots.py",
    "eval_retrieval.py",
    "eval_spec_guard.py",
    "eval_mesh_star.py",
    "eval_intake.py",
    "deploy_check.py",
    "requirements.txt",
    "README.md",
    "prompt.txt",
    "kb",
    "templates",
    "ansible_templates",
    "generated",
]

OPTIONAL_INCLUDE_PATHS = [
    "eval_runs",
    "snapshot_runs",
    "spec_guard_runs",
    "mesh_star_runs",
    "intake_eval_runs",
    "index",
]

IGNORE_DIR_NAMES = {
    ".terraform",
    "__pycache__",
    ".ipynb_checkpoints",
}

IGNORE_FILE_NAMES = {
    ".DS_Store",
}


def safe_remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _ignore_filter(directory: str, names: List[str]) -> List[str]:
    ignored = []
    for name in names:
        if name in IGNORE_DIR_NAMES or name in IGNORE_FILE_NAMES:
            ignored.append(name)
    return ignored


def copy_path(src: Path, dst: Path) -> None:
    if not src.exists():
        return

    if src.is_dir():
        shutil.copytree(
            src,
            dst,
            dirs_exist_ok=True,
            ignore=_ignore_filter,
        )
    else:
        if src.name in IGNORE_FILE_NAMES:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def collect_existing(paths: List[str]) -> List[str]:
    existing = []
    for rel in paths:
        if (BASE / rel).exists():
            existing.append(rel)
    return existing


def summarize_directory(path: Path) -> Dict[str, List[str]]:
    summary: Dict[str, List[str]] = {}
    if not path.exists():
        return summary

    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIR_NAMES]
        files = [f for f in files if f not in IGNORE_FILE_NAMES]

        rel_root = os.path.relpath(root, path)
        key = "." if rel_root == "." else rel_root
        summary[key] = sorted(files)

    return summary


def write_manifest(package_dir: Path) -> Path:
    manifest = {
        "project": "net2tf_v3",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "included_required_paths": collect_existing(INCLUDE_PATHS),
        "included_optional_paths": collect_existing(OPTIONAL_INCLUDE_PATHS),
        "ignored_directories": sorted(IGNORE_DIR_NAMES),
        "ignored_files": sorted(IGNORE_FILE_NAMES),
        "generated_files": summarize_directory(BASE / "generated"),
        "generated_ansible_files": summarize_directory(BASE / "generated" / "ansible"),
        "ansible_templates_files": summarize_directory(BASE / "ansible_templates"),
        "eval_runs_files": summarize_directory(BASE / "eval_runs"),
        "snapshot_runs_files": summarize_directory(BASE / "snapshot_runs"),
        "spec_guard_runs_files": summarize_directory(BASE / "spec_guard_runs"),
        "mesh_star_runs_files": summarize_directory(BASE / "mesh_star_runs"),
        "intake_eval_runs_files": summarize_directory(BASE / "intake_eval_runs"),
    }

    manifest_path = package_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def write_readme(package_dir: Path) -> Path:
    lines = [
        "# net2tf_v3 packaged export",
        "",
        "This package contains:",
        "- source code",
        "- guided intake layer",
        "- knowledge base",
        "- Terraform templates",
        "- generated Terraform",
        "- Ansible planner/builder/checker",
        "- generated Ansible files if they exist",
        "- evaluation scripts",
        "- optional evaluation outputs",
        "",
        "## Suggested Terraform usage",
        "",
        "```bash",
        "pip install -r requirements.txt",
        "python app.py generate --input prompt.txt --out ./generated",
        "cd generated",
        "terraform init",
        "terraform plan",
        "```",
        "",
        "## Suggested Ansible usage",
        "",
        "After Terraform apply, generate Ansible configuration with:",
        "",
        "```bash",
        "python app.py generate --input prompt.txt --out ./generated --ansible-request \"install nginx on PC1 and start it\"",
        "```",
        "",
        "Then run:",
        "",
        "```bash",
        "cd generated/ansible",
        "ansible-playbook --syntax-check playbook.yml",
        "ansible-playbook playbook.yml",
        "```",
        "",
        "## Notes",
        "- `.terraform/` is intentionally excluded from the package.",
        "- Terraform providers will be re-downloaded with `terraform init`.",
        "- Generated `.tf` files are included and unchanged.",
        "- Generated Ansible files are included under `generated/ansible` when present.",
        "- Cache folders such as `__pycache__` are excluded.",
        "",
    ]

    text = "\n".join(lines)
    readme_path = package_dir / "PACKAGE_README.md"
    readme_path.write_text(text, encoding="utf-8")
    return readme_path


def build_package() -> Dict[str, str]:
    DIST.mkdir(parents=True, exist_ok=True)
    safe_remove(PACKAGE_ROOT)
    PACKAGE_ROOT.mkdir(parents=True, exist_ok=True)

    for rel in INCLUDE_PATHS + OPTIONAL_INCLUDE_PATHS:
        src = BASE / rel
        dst = PACKAGE_ROOT / rel
        copy_path(src, dst)

    manifest_path = write_manifest(PACKAGE_ROOT)
    readme_path = write_readme(PACKAGE_ROOT)

    zip_path = DIST / "net2tf_v3_package.zip"
    safe_remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PACKAGE_ROOT):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIR_NAMES]
            files = [f for f in files if f not in IGNORE_FILE_NAMES]

            for name in files:
                full_path = Path(root) / name
                arcname = full_path.relative_to(DIST)
                zf.write(full_path, arcname.as_posix())

    return {
        "package_dir": str(PACKAGE_ROOT),
        "zip_file": str(zip_path),
        "manifest": str(manifest_path),
        "readme": str(readme_path),
    }


if __name__ == "__main__":
    result = build_package()
    print(json.dumps(result, indent=2))
