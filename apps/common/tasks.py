import json
import os
import re
import subprocess
import tempfile

from celery import shared_task
from django.utils import timezone

from .models import BatchTask, BatchTaskHost, FileDistTask, FileDistTaskHost, HostAccount, HostIP


def _build_inventory(host_ids):
    """构建 ansible inventory 内容"""
    lines = ["[targets]"]
    hosts = {}
    ip_qs = HostIP.objects.filter(
        host_id__in=host_ids,
    ).order_by("host_id", "id").select_related("host")
    ip_map = {}
    for ip in ip_qs:
        if ip.host_id not in ip_map:
            ip_map[ip.host_id] = ip.ip_address
    account_qs = HostAccount.objects.filter(
        host_id__in=host_ids, account_type="adm",
    ).select_related("host")
    account_map = {}
    for acct in account_qs:
        if acct.host_id not in account_map:
            account_map[acct.host_id] = acct
    from .models import Host
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
            f'ansible_ssh_args="-C -o ControlMaster=auto"'
            f' ansible_ssh_common_args="-o StrictHostKeyChecking=no'
            f' -o UserKnownHostsFile=/dev/null -o HostKeyAlgorithms=+ssh-rsa,ssh-dss'
            f' -o ServerAliveInterval=30"'
        )
        lines.append(line)
        hosts[host.hostname] = host.id
    return "\n".join(lines), hosts


# 项目根目录下的自定义 callback 插件路径
_CALLBACK_PLUGINS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "callback_plugins")
)


def _run_ansible_ad_hoc(inventory_content, script_path):
    """执行 ansible script 模块（自动上传脚本到远程 /tmp/ 并设置可执行权限）"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, encoding="utf-8") as f:
        f.write(inventory_content)
        inv_path = f.name

    try:
        env = os.environ.copy()
        env["ANSIBLE_STDOUT_CALLBACK"] = "dbatoolbox_json"
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
        env["ANSIBLE_DEPRECATION_WARNINGS"] = "False"
        env["ANSIBLE_CALLBACK_PLUGINS"] = _CALLBACK_PLUGINS_DIR
        
        # cmd=ansible, 该模式为Ad-Hoc模式，需要显式加载CALLBACK插件
        env["ANSIBLE_LOAD_CALLBACK_PLUGINS"] = "1"  
        
        # script 模块: 将本地脚本文件上传到远程 /tmp/，自动 chmod +x，执行后清理
        cmd = [
            "ansible", 
            "targets", 
            "-i", inv_path,
            "-m", "script", 
            "-a", script_path,
        ]

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            env=env,
        )
   
        try:
            return json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            return {"_raw": proc.stdout, "_stderr": proc.stderr}
    finally:
        os.unlink(inv_path)


def _run_ansible_playbook(inventory_content, script_path):
    """通过 ansible-playbook + 自定义 JSON callback 执行脚本"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, encoding="utf-8") as f:
        f.write(inventory_content)
        inv_path = f.name

    try:
        playbook_yaml = (
            "- hosts: targets\n"
            "  gather_facts: no\n"
            "  tasks:\n"
            f"    - script: {script_path}\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False, encoding="utf-8") as pf:
            pf.write(playbook_yaml)
            playbook_path = pf.name

        try:
            env = os.environ.copy()
            env["ANSIBLE_STDOUT_CALLBACK"] = "dbatoolbox_json"
            env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
            env["ANSIBLE_DEPRECATION_WARNINGS"] = "False"
            env["ANSIBLE_CALLBACK_PLUGINS"] = _CALLBACK_PLUGINS_DIR

            cmd = [
                "ansible-playbook", "-i", inv_path, playbook_path,
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                env=env,
            )
            
            try:
                return json.loads(proc.stdout.strip()) if proc.stdout.strip() else {}
            except json.JSONDecodeError:
                return {"_raw": proc.stdout, "_stderr": proc.stderr}
        finally:
            os.unlink(playbook_path)
    finally:
        os.unlink(inv_path)


@shared_task(bind=True, max_retries=1)
def run_batch_task(self, task_id):
    """异步批量执行脚本"""
    try:
        task = BatchTask.objects.get(id=task_id)
    except BatchTask.DoesNotExist:
        return

    task.status = "running"
    task.started_at = timezone.now()
    task.save(update_fields=["status", "started_at"])

    host_objs = list(BatchTaskHost.objects.filter(task_id=task_id).select_related("host"))
    host_ids = [h.host_id for h in host_objs]

    # 标记所有主机为执行中
    now = timezone.now()
    BatchTaskHost.objects.filter(task_id=task_id).update(
        status="running", started_at=now,
    )

    try:
        # 写脚本到临时文件
        ext_map = {"shell": ".sh", "python": ".py", "sql": ".sql", "bat": ".bat", "perl": ".pl"}
        suffix = ext_map.get(task.script.script_type, ".sh")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8",
        ) as sf:
            sf.write(task.script.content)
            script_path = sf.name

        try:
            inv_content, hostname_map = _build_inventory(host_ids)
            if not hostname_map:
                task.status = "failed"
                task.save(update_fields=["status"])
                BatchTaskHost.objects.filter(task_id=task_id).update(
                    status="failed", output="无法获取主机连接信息",
                    finished_at=timezone.now(),
                )
                return

            # 两个方法都行: _run_ansible_ad_hoc OR _run_ansible_playbook
            result = _run_ansible_playbook(inv_content, script_path)
            
            # 解析结果
            success_count = 0
            fail_count = 0

            if "plays" in result:
                for play in result.get("plays", []):
                    for t in play.get("tasks", []):
                        for hostname, host_result in t.get("hosts", {}).items():
                            host_id = hostname_map.get(hostname)
                            if not host_id:
                                continue
                            unreachable = host_result.get("unreachable", False)
                            rc = host_result.get("rc")
                            is_ok = (not unreachable) and (rc == 0)
                            out_parts = []
                            if is_ok and host_result.get("stdout"):
                                out_parts.append(host_result["stdout"])
                            if not is_ok and host_result.get("stderr"):
                                out_parts.append(host_result["stderr"])
                            if not is_ok and host_result.get("msg"):
                                out_parts.append(host_result["msg"])
                            output = "\n".join(out_parts)
                            st = "success" if is_ok else "failed"
                            BatchTaskHost.objects.filter(task_id=task_id, host_id=host_id).update(
                                status=st, output=output, finished_at=timezone.now(),
                            )
                            if is_ok:
                                success_count += 1
                            else:
                                fail_count += 1
            else:
                # ansible 返回非 JSON 输出（连接级错误如缺少 sshpass）
                raw = result.get("_raw", "")
                _stderr = result.get("_stderr", "")
                combined = (raw + "\n" + _stderr).strip()[:3000]
                # 尝试按主机名拆分输出
                host_outputs = {}
                pattern = re.compile(r'^(\S+)\s+\|\s+(FAILED|UNREACHABLE).*', re.MULTILINE)
                current_host = None
                for line in (raw + "\n" + _stderr).split("\n"):
                    m = pattern.match(line)
                    if m:
                        current_host = m.group(1)
                        if current_host not in host_outputs:
                            host_outputs[current_host] = []
                    if current_host:
                        host_outputs[current_host].append(line)
                for h in host_objs:
                    host_output = "\n".join(host_outputs.get(h.host.hostname, []))
                    BatchTaskHost.objects.filter(task_id=task_id, host_id=h.host_id).update(
                        status="failed", output=host_output[:2000] or combined[:2000],
                        finished_at=timezone.now(),
                    )
                    fail_count += 1

            task.success_count = success_count
            task.fail_count = fail_count
            task.status = "completed"
            task.finished_at = timezone.now()
            task.save()

        finally:
            os.unlink(script_path)

    except Exception as exc:
        task.status = "failed"
        task.save(update_fields=["status"])
        BatchTaskHost.objects.filter(task_id=task_id, status="running").update(
            status="failed", output=str(exc)[:2000], finished_at=timezone.now(),
        )


_SSH_OPTS = (
    "-o StrictHostKeyChecking=no "
    "-o UserKnownHostsFile=/dev/null "
    "-o HostKeyAlgorithms=+ssh-rsa,ssh-dss "
    "-o ServerAliveInterval=30 "
    "-o ConnectTimeout=15"
)


def _scp_fetch(ip, user, pswd, port, remote_path, local_dest):
    """从远程主机拉取文件到本地，返回 (success, output_or_local_path)"""
    remote = f"{user}@{ip}"
    cmd = (
        f"sshpass -p '{pswd}' scp -P {port} {_SSH_OPTS} "
        f"'{remote}:{remote_path}' '{local_dest}'"
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if proc.returncode == 0:
        return True, local_dest
    err = (proc.stderr or proc.stdout or "").strip()
    return False, err or f"scp 返回码 {proc.returncode}"


def _scp_send(ip, user, pswd, port, src_path, dest_path, backup=False):
    """scp 传输本地文件到远程主机，返回 (success, output)"""
    remote = f"{user}@{ip}"
    dest_file = os.path.join(dest_path.rstrip("/"), os.path.basename(src_path))

    if backup:
        backup_cmd = (
            f"sshpass -p '{pswd}' ssh -p {port} {_SSH_OPTS} {remote} "
            f"\"[ ! -f '{dest_file}' ] || mv '{dest_file}' '{dest_file}.bak.$(date +%Y%m%d%H%M%S)'\""
        )
        subprocess.run(backup_cmd, shell=True, capture_output=True, text=True, timeout=30)

    cmd = (
        f"sshpass -p '{pswd}' scp -P {port} {_SSH_OPTS} "
        f"'{src_path}' '{remote}:{dest_file}'"
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)

    if proc.returncode == 0:
        return True, f"文件已分发至: {dest_file}"
    else:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err or f"scp 返回码 {proc.returncode}"


def _get_host_ssh_map(host_ids):
    """获取每台主机 SSH 信息: {host_id: {ip, user, pswd, port, hostname}}"""
    from .models import Host
    ip_map = {}
    for ip in HostIP.objects.filter(host_id__in=host_ids).order_by("host_id", "id"):
        if ip.host_id not in ip_map:
            ip_map[ip.host_id] = ip.ip_address
    acct_map = {}
    for acct in HostAccount.objects.filter(host_id__in=host_ids, account_type="adm").order_by("host_id", "id"):
        if acct.host_id not in acct_map:
            acct_map[acct.host_id] = acct
    if len(acct_map) < len(host_ids):
        for acct in HostAccount.objects.filter(host_id__in=host_ids).order_by("host_id", "id"):
            if acct.host_id not in acct_map:
                acct_map[acct.host_id] = acct
    result = {}
    for host in Host.objects.filter(id__in=host_ids):
        ip_addr = ip_map.get(host.id)
        acct = acct_map.get(host.id)
        if not ip_addr:
            continue
        result[host.id] = {
            "hostname": host.hostname,
            "ip": ip_addr,
            "user": acct.account_name if acct else "root",
            "pswd": acct.account_pswd if acct else "",
            "port": host.ssh_port or 22,
        }
    return result


@shared_task(bind=True, max_retries=1)
def run_file_dist_task(self, task_id):
    """异步文件分发任务（sshpass + scp 直接传输，不依赖 ansible）"""
    try:
        task = FileDistTask.objects.select_related("source_host").get(id=task_id)
    except FileDistTask.DoesNotExist:
        return

    task.status = "running"
    task.save(update_fields=["status"])

    host_objs = list(FileDistTaskHost.objects.filter(task_id=task_id).select_related("host"))
    host_ids = [h.host_id for h in host_objs]

    now = timezone.now()
    FileDistTaskHost.objects.filter(task_id=task_id).update(status="running", started_at=now)

    try:
        # 确定源文件本地路径
        if task.source_type == "local":
            if not task.local_file:
                raise ValueError("本地文件来源缺少上传文件")
            src_path = task.local_file.path
        elif task.source_type == "remote":
            if not task.source_host or not task.source_path:
                raise ValueError("远程文件来源缺少源主机或源路径")
            src_ssh = _get_host_ssh_map([task.source_host_id]).get(task.source_host_id)
            if not src_ssh:
                raise ValueError("无法获取源主机 SSH 连接信息")
            # 从源主机拉取文件到临时目录
            tmp_dir = tempfile.mkdtemp(prefix="filedist_")
            tmp_file = os.path.join(tmp_dir, os.path.basename(task.source_path))
            ok, result = _scp_fetch(
                src_ssh["ip"], src_ssh["user"], src_ssh["pswd"], src_ssh["port"],
                task.source_path, tmp_file,
            )
            if not ok:
                raise ValueError(f"从源主机拉取文件失败: {result}")
            src_path = tmp_file
        else:
            raise ValueError(f"不支持的来源类型: {task.source_type}")

        ssh_map = _get_host_ssh_map(host_ids)

        success_count = 0
        fail_count = 0

        for h in host_objs:
            info = ssh_map.get(h.host_id)
            if not info:
                fail_count += 1
                FileDistTaskHost.objects.filter(task_id=task_id, host_id=h.host_id).update(
                    status="failed", output="主机缺少连接信息（无IP或账号）",
                    finished_at=timezone.now(),
                )
                continue

            is_ok, output = _scp_send(
                info["ip"], info["user"], info["pswd"], info["port"],
                src_path, task.dest_path, task.backup,
            )
            st = "success" if is_ok else "failed"
            FileDistTaskHost.objects.filter(task_id=task_id, host_id=h.host_id).update(
                status=st, output=output[:2000], finished_at=timezone.now(),
            )
            if is_ok:
                success_count += 1
            else:
                fail_count += 1

        task.success_count = success_count
        task.fail_count = fail_count
        task.status = "completed"
        task.save()

    except Exception as exc:
        task.status = "failed"
        task.save(update_fields=["status"])
        FileDistTaskHost.objects.filter(task_id=task_id, status="running").update(
            status="failed", output=str(exc)[:2000], finished_at=timezone.now(),
        )
