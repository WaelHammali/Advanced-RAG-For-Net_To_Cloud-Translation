from __future__ import annotations

import subprocess
from typing import Dict, Tuple


def run_cmd(cmd: list[str], cwd: str) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def run_quality_checks(generated_dir: str) -> Dict[str, object]:
    result = {}

    rc_fmt, out_fmt, err_fmt = run_cmd(
        ["terraform", "fmt", "-recursive", "-no-color"],
        generated_dir,
    )
    result["fmt_ok"] = (rc_fmt == 0)
    result["fmt_stdout"] = out_fmt
    result["fmt_stderr"] = err_fmt
    result["fmt_output"] = out_fmt + err_fmt

    rc_init, out_init, err_init = run_cmd(
        ["terraform", "init", "-input=false", "-no-color"],
        generated_dir,
    )
    result["init_ok"] = (rc_init == 0)
    result["init_stdout"] = out_init
    result["init_stderr"] = err_init
    result["init_output"] = out_init + err_init

    rc_val, out_val, err_val = run_cmd(
        ["terraform", "validate", "-no-color"],
        generated_dir,
    )
    result["validate_ok"] = (rc_val == 0)
    result["validate_stdout"] = out_val
    result["validate_stderr"] = err_val
    result["validate_output"] = out_val + err_val

    result["all_ok"] = (
        result["fmt_ok"]
        and result["init_ok"]
        and result["validate_ok"]
    )

    return result
