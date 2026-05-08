from __future__ import annotations

import subprocess
from typing import Dict, Tuple


def run_cmd(cmd: list[str], cwd: str) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def run_ansible_syntax_check(ansible_dir: str) -> Dict[str, object]:
    rc, out, err = run_cmd(
        ["ansible-playbook", "-i", "inventory.ini", "--syntax-check", "playbook.yml"],
        ansible_dir,
    )
    return {
        "syntax_ok": rc == 0,
        "returncode": rc,
        "stdout": out,
        "stderr": err,
        "output": out + err,
    }


def run_ansible_playbook(ansible_dir: str) -> Dict[str, object]:
    rc, out, err = run_cmd(
        ["ansible-playbook", "-i", "inventory.ini", "playbook.yml"],
        ansible_dir,
    )
    return {
        "run_ok": rc == 0,
        "returncode": rc,
        "stdout": out,
        "stderr": err,
        "output": out + err,
    }
