import asyncio
import json
import logging
import threading

import paramiko
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


def _get_host_ssh_info(host_id):
    """获取主机 SSH 连接信息：第一个 IP + 优先 adm 账号，否则第一个账号"""
    from .models import Host, HostAccount, HostIP

    try:
        host = Host.objects.get(id=host_id)
    except Host.DoesNotExist:
        return None

    first_ip = HostIP.objects.filter(host_id=host_id).order_by("id").first()
    if not first_ip:
        return None

    account = (
        HostAccount.objects.filter(host_id=host_id, account_type="adm").order_by("id").first()
        or HostAccount.objects.filter(host_id=host_id).order_by("id").first()
    )

    return {
        "hostname": host.hostname,
        "display": str(host),
        "ip": first_ip.ip_address,
        "user": account.account_name if account else "root",
        "password": account.account_pswd if account else "",
        "port": host.ssh_port or 22,
    }


class SSHConsumer(AsyncWebsocketConsumer):
    """WebSocket Consumer：paramiko SSH 终端双向转发"""

    async def connect(self):
        from django.contrib.auth.models import AnonymousUser

        user = self.scope.get("user")
        if not user or isinstance(user, AnonymousUser):
            await self.close(code=4001)
            return

        host_id = self.scope["url_route"]["kwargs"]["host_id"]
        ssh_info = await database_sync_to_async(_get_host_ssh_info)(host_id)
        if not ssh_info:
            await self.close(code=4002)
            return

        await self.accept()

        # 捕获当前事件循环，供后台线程回写
        self._loop = asyncio.get_running_loop()
        self._running = False
        self._ssh = None
        self._chan = None
        self._read_task = None

        # 在线程池中建立 SSH 连接（paramiko 是阻塞的）
        try:
            self._ssh, self._chan = await asyncio.to_thread(
                self._connect_ssh, ssh_info,
            )
        except Exception as e:
            await self.send(text_data=json.dumps({
                "type": "error",
                "message": f"SSH 连接 {ssh_info['ip']}:{ssh_info['port']} 失败: {e}",
            }))
            await self.close()
            return

        await self.send(text_data=json.dumps({
            "type": "connected",
            "hostname": ssh_info["hostname"],
            "ip": ssh_info["ip"],
            "user": ssh_info["user"],
        }))

        # 后台线程持续读取 SSH 输出
        self._running = True
        self._read_task = asyncio.get_running_loop().run_in_executor(
            None, self._ssh_read_loop,
        )

    @staticmethod
    def _connect_ssh(info):
        """阻塞式 SSH 连接（在 to_thread 中执行）"""
        # 兼容老服务器：paramiko 5.0 完全移除了 ssh-rsa
        if "ssh-rsa" not in paramiko.Transport._preferred_keys:
            paramiko.Transport._preferred_keys += ("ssh-rsa",)
        if "ssh-rsa" not in paramiko.Transport._key_info:
            paramiko.Transport._key_info["ssh-rsa"] = paramiko.RSAKey
        if "ssh-rsa" not in paramiko.RSAKey.HASHES:
            from cryptography.hazmat.primitives import hashes
            paramiko.RSAKey.HASHES["ssh-rsa"] = hashes.SHA1
            paramiko.RSAKey.HASHES["ssh-rsa-cert-v01@openssh.com"] = hashes.SHA1

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=info["ip"],
            port=info["port"],
            username=info["user"],
            password=info["password"],
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
            disabled_algorithms={"pubkeys": []},
        )
        chan = client.invoke_shell(term="xterm-256color", width=120, height=40)
        chan.settimeout(0.5)
        return client, chan

    def _ssh_read_loop(self):
        """后台线程：阻塞读取 SSH → 调度 WebSocket 发送"""
        import socket

        while self._running and self._chan and not self._chan.closed:
            try:
                data = self._chan.recv(4096)
                if data:
                    asyncio.run_coroutine_threadsafe(
                        self.send(bytes_data=data), self._loop,
                    )
                else:
                    break  # EOF：channel 真正关闭
            except socket.timeout:
                continue  # 无数据可读，正常情况，继续等待
            except Exception:
                break

        # SSH 关闭，通知前端
        if self._running:
            asyncio.run_coroutine_threadsafe(
                self.send(text_data=json.dumps({"type": "disconnected"})),
                self._loop,
            )

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data:
            # 二进制数据直接转发到 SSH
            if self._chan and not self._chan.closed:
                try:
                    self._chan.send(bytes_data)
                except Exception:
                    pass
            return

        if not text_data:
            return

        # 尝试解析 JSON 控制消息
        try:
            msg = json.loads(text_data)
        except (json.JSONDecodeError, ValueError):
            # 不是 JSON → 终端输入 → 转发到 SSH
            if self._chan and not self._chan.closed:
                try:
                    self._chan.send(text_data)
                except Exception:
                    pass
            return

        # 是 JSON → 检查是否为控制消息
        if isinstance(msg, dict) and "type" in msg:
            if msg["type"] == "resize" and self._chan and not self._chan.closed:
                try:
                    self._chan.resize_pty(
                        width=msg.get("cols", 120),
                        height=msg.get("rows", 40),
                    )
                except Exception:
                    pass
            elif msg["type"] == "disconnect":
                await self.close()
        else:
            # JSON 值但不是 dict → 终端输入 → 转发到 SSH
            if self._chan and not self._chan.closed:
                try:
                    self._chan.send(text_data)
                except Exception:
                    pass

    async def disconnect(self, close_code):
        self._running = False
        if self._chan:
            try:
                self._chan.close()
            except Exception:
                pass
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
