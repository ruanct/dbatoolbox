"""Ansible Inventory 构建与目标主机 Python 解释器探测。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Any

from .models import Host, HostAccount, HostIP

MIN_ANSIBLE_PYTHON = (3, 8)

_FIND_PYTHON_RAW = r"""
candidates=(
  /opt/rh/rh-python311/root/usr/bin/python3
  /opt/rh/rh-python39/root/usr/bin/python3
  /opt/rh/rh-python38/root/usr/bin/python3
  /usr/bin/python3.11
  /usr/bin/python3.10
  /usr/bin/python3.9
  /usr/bin/python3.8
  /usr/local/bin/python3.11
  /usr/local/bin/python3.10
  /usr/local/bin/python3.9
  /usr/local/bin/python3.8
  /usr/bin/python3
)
for c in "${candidates[@]}"; do
  [ -x "$c" ] || continue
  ver=$("$c" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || continue
  major=${ver%%.*}
  rest=${ver#*.}
  minor=${rest%%.*}
  if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 8 ]; }; then
    echo "${c}|${ver}"
    exit 0
  fi
done
default=$(command -v python3 2>/dev/null || true)
if [ -n "$default" ]; then
  ver=$("$default" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || ver="unknown"
  echo "ERROR|目标主机 Python 版本过低 (${ver}，需要 >= 3.8): ${default}"
  exit 1
fi
echo "ERROR|未找到可用的 Python 3.8+ 解释器，请先安装（CentOS7 可安装 rh-python38）"
exit 1
""".strip()


def build_inventory(
    host_ids: list[int],
    *,
    python_interpreter_by_host_id: dict[int, str] | None = None,
) -> tuple[str, dict[str, int]]:
    """构建 ansible inventory；可为每台主机指定 ansible_python_interpreter。"""
    lines = ["[targets]"]
    hosts: dict[str, int] = {}
    interpreter_map = python_interpreter_by_host_id or {}

    ip_qs = HostIP.objects.filter(host_id__in=host_ids).order_by("host_id", "id")
    ip_map: dict[int, str] = {}
    for ip in ip_qs:
        if ip.host_id not in ip_map:
            ip_map[ip.host_id] = ip.ip_address

    account_qs = HostAccount.objects.filter(host_id__in=host_ids, account_type="adm")
    account_map: dict[int, HostAccount] = {}
    for acct in account_qs:
        if acct.host_id not in account_map:
            account_map[acct.host_id] = acct

    for host in Host.objects.filter(id__in=host_ids):
        ip_addr = ip_map.get(host.id, "")
        acct = account_map.get(host.id)
        if not ip_addr:
            continue
        user = acct.account_name if acct else "root"
        pswd = acct.account_pswd if acct else ""
        port = host.ssh_port or 22
        line = (
            f'{host.hostname} ansible_host={ip_addr} ansible_user={user} '
            f'ansible_ssh_pass="{pswd}" ansible_port={port} '
            f'ansible_ssh_common_args="-o StrictHostKeyChecking=no'
            f' -o UserKnownHostsFile=/dev/null'
            f' -o HostKeyAlgorithms=+ssh-rsa,ssh-dss'
            f' -o ServerAliveInterval=30'
            f' -o ConnectTimeout=15"'
        )
        interpreter = interpreter_map.get(host.id)
        if interpreter:
            line += f' ansible_python_interpreter={interpreter}'
        lines.append(line)
        hosts[host.hostname] = host.id
    return "\n".join(lines), hosts


def _ansible_env() -> dict[str, str]:
    env = os.environ.copy()
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ANSIBLE_DEPRECATION_WARNINGS"] = "False"
    return env


def _parse_ansible_raw_line(line: str) -> tuple[str, bool, str]:
    """解析 ansible -o 输出的 raw 模块结果行，返回 (hostname, has_stdout, stdout)。"""
    prefix_match = re.match(r"^(\S+)\s+\|\s+(CHANGED|SUCCESS|FAILED!??|UNREACHABLE)", line)
    if not prefix_match:
        return "", False, ""

    hostname = prefix_match.group(1)
    json_match = re.search(r"=>\s*(\{.*\})\s*$", line)
    if json_match:
        try:
            payload = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            payload = {}
        stdout = str(payload.get("stdout") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        return hostname, bool(stdout), stdout

    text_match = re.search(r"\(stdout\)\s*(.*)$", line, re.DOTALL)
    if text_match:
        stdout = text_match.group(1)
        if " (stderr)" in stdout:
            stdout = stdout.split(" (stderr)", 1)[0]
        stdout = stdout.replace("\r\n", "\n").replace("\r", "\n").strip()
        return hostname, bool(stdout), stdout
    return hostname, False, ""


def _run_ansible_raw(host_ids: list[int], script: str, *, timeout: int = 30) -> dict[str, str]:
    inv_content, hostname_map = build_inventory(host_ids)
    if not hostname_map:
        return {}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, encoding="utf-8") as invf:
        invf.write(inv_content)
        inv_path = invf.name

    try:
        cmd = ["ansible", "targets", "-i", inv_path, "-m", "raw", "-a", script, "-o"]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_ansible_env(),
        )
        outputs: dict[str, str] = {}
        for line in (proc.stdout or "").splitlines():
            hostname, has_stdout, stdout = _parse_ansible_raw_line(line)
            if hostname and has_stdout:
                outputs[hostname] = stdout
        if not outputs and proc.stderr:
            outputs["__stderr__"] = proc.stderr.strip()
        if not outputs and proc.stdout:
            outputs["__raw__"] = proc.stdout.strip()
        return outputs
    except subprocess.TimeoutExpired:
        return {"__error__": f"SSH/Ansible 探测超时（>{timeout}s）"}
    finally:
        os.unlink(inv_path)


def _normalize_probe_stdout(stdout: str) -> str:
    text = stdout.replace("\\r\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _parse_python_probe_output(stdout: str) -> tuple[bool, str, str | None]:
    """解析探测脚本输出，返回 (ok, message, interpreter_path)。"""
    for line in reversed(_normalize_probe_stdout(stdout).splitlines()):
        text = line.strip()
        if not text:
            continue
        if "|" not in text:
            continue
        key, value = text.split("|", 1)
        key = key.strip()
        value = value.strip()
        if key == "ERROR":
            return False, value, None
        if key.startswith("/") or key.startswith("."):
            return True, value, key
    return False, "无法解析目标主机 Python 探测结果", None


def ensure_python_interpreter(host_id: int) -> tuple[bool, str, str | None]:
    """
    探测目标主机可用的 Python 3.8+ 解释器。

    返回 (成功与否, 描述信息, 解释器路径)。
    """
    try:
        host = Host.objects.get(id=host_id)
    except Host.DoesNotExist:
        return False, "目标主机不存在", None

    raw_outputs = _run_ansible_raw([host_id], _FIND_PYTHON_RAW)
    stdout = raw_outputs.get(host.hostname, "")
    if not stdout:
        stderr = raw_outputs.get("__stderr__") or raw_outputs.get("__error__") or raw_outputs.get("__raw__", "")
        hint = stderr or "请检查主机 IP、SSH 账号密码及网络连通性"
        return False, f"[{host.hostname}] 无法连接目标主机或执行探测: {hint}", None

    ok, message, interpreter = _parse_python_probe_output(stdout)
    if not ok:
        return False, f"[{host.hostname}] {message}", None
    return True, f"Python {message}", interpreter


def resolve_python_interpreters(host_ids: list[int]) -> tuple[dict[int, str], list[str]]:
    """
    批量解析主机 Python 解释器。

    返回 (host_id -> interpreter, 错误信息列表)。
    """
    interpreters: dict[int, str] = {}
    errors: list[str] = []
    for host_id in host_ids:
        ok, msg, interpreter = ensure_python_interpreter(host_id)
        if ok and interpreter:
            interpreters[host_id] = interpreter
        else:
            errors.append(msg)
    return interpreters, errors
