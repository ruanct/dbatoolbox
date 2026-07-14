"""部署任务 Ansible 执行封装。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.common.tasks import _build_inventory

_CALLBACK_PLUGINS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "callback_plugins"),
)


def _playbooks_root() -> Path:
    return Path(settings.BASE_DIR) / "deploy" / "playbooks"


def _format_playbook_output(result: dict[str, Any]) -> str:
    if not result:
        return "无输出"
    if result.get("_stderr"):
        return str(result["_stderr"])[:8000]
    if result.get("_raw"):
        return str(result["_raw"])[:8000]
    lines: list[str] = []
    for play in result.get("plays", []):
        for task in play.get("tasks", []):
            task_name = (task.get("task") or {}).get("name") or "?"
            hosts = task.get("hosts") or {}
            for host_name, host_result in hosts.items():
                if host_result.get("failed") or host_result.get("unreachable"):
                    msg = str(host_result.get("msg") or "")
                    if msg in {"non-zero return code", "未知错误"} or msg.startswith("non-zero"):
                        err = (
                            host_result.get("stderr")
                            or host_result.get("stdout")
                            or host_result.get("module_stderr")
                            or host_result.get("exception")
                            or msg
                            or "未知错误"
                        )
                    else:
                        err = (
                            msg
                            or host_result.get("stderr")
                            or host_result.get("module_stderr")
                            or host_result.get("exception")
                            or host_result.get("stdout")
                            or "未知错误"
                        )
                    lines.append(f"[{host_name}] FAILED ({task_name}): {err}")
                elif host_result.get("stdout"):
                    lines.append(f"[{host_name}] {host_result.get('stdout')}")
                elif host_result.get("msg"):
                    lines.append(f"[{host_name}] {host_result.get('msg')}")
    return "\n".join(lines)[:12000] if lines else json.dumps(result, ensure_ascii=False)[:8000]


def _playbook_failed(result: dict[str, Any]) -> bool:
    stats = result.get("stats") or {}
    for host_stats in stats.values():
        if host_stats.get("failures", 0) > 0 or host_stats.get("unreachable", 0) > 0:
            return True
    for play in result.get("plays", []):
        for task in play.get("tasks", []):
            for host_result in (task.get("hosts") or {}).values():
                if host_result.get("failed") or host_result.get("unreachable"):
                    return True
    return bool(result.get("_stderr"))


def run_deploy_playbook_step(
    *,
    host_id: int,
    playbook_relative: str,
    step_tag: str,
    deploy_vars: dict[str, Any],
    timeout: int = 3600,
    python_interpreter: str | None = None,
    inventory_groups: dict[str, list[int]] | None = None,
    python_interpreter_by_host_id: dict[int, str] | None = None,
) -> tuple[bool, str]:
    playbook_path = _playbooks_root() / playbook_relative
    if not playbook_path.exists():
        return False, f"Playbook 不存在: {playbook_path}"

    interpreter_map = dict(python_interpreter_by_host_id or {})
    if python_interpreter and host_id not in interpreter_map:
        interpreter_map[host_id] = python_interpreter
    if inventory_groups:
        inv_content, hostname_map = _build_inventory(
            [],
            python_interpreter_by_host_id=interpreter_map or None,
            host_groups=inventory_groups,
        )
    else:
        inv_content, hostname_map = _build_inventory(
            [host_id],
            python_interpreter_by_host_id=interpreter_map or None,
        )
    if not hostname_map:
        return False, "无法构建目标主机 Ansible Inventory（检查主机 IP 与账号）"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as vf:
        json.dump({"deploy": deploy_vars}, vf, ensure_ascii=False)
        vars_path = vf.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, encoding="utf-8") as invf:
        invf.write(inv_content)
        inv_path = invf.name

    try:
        env = os.environ.copy()
        env["ANSIBLE_STDOUT_CALLBACK"] = "dbatoolbox_json"
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
        env["ANSIBLE_DEPRECATION_WARNINGS"] = "False"
        env["ANSIBLE_CALLBACK_PLUGINS"] = _CALLBACK_PLUGINS_DIR

        cmd = [
            "ansible-playbook",
            "-i", inv_path,
            str(playbook_path),
            "--tags", step_tag,
            "-e", f"@{vars_path}",
        ]
        if python_interpreter and not inventory_groups:
            cmd.extend(["-e", f"ansible_python_interpreter={python_interpreter}"])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        stdout = proc.stdout.strip()
        try:
            result = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError:
            result = {"_raw": proc.stdout, "_stderr": proc.stderr}

        output = _format_playbook_output(result)
        if proc.returncode != 0 or _playbook_failed(result):
            if proc.stderr:
                output = (output + "\n" + proc.stderr).strip()
            return False, output or "Ansible 执行失败"
        return True, output or "执行成功"
    except subprocess.TimeoutExpired:
        return False, f"步骤执行超时（>{timeout}s）"
    finally:
        os.unlink(vars_path)
        os.unlink(inv_path)
