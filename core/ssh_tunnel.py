"""SSH 隧道管理器：把远程服务器的 9880 端口转发到本地。

工作原理：
    ssh -L <local_port>:localhost:<remote_port> <host> -N -f -o ConnectTimeout=5

用 -N 不执行远程命令，-f 后台运行。本地 KurisuTTS 连 127.0.0.1:9880 透明转发。

设计要点：
- 保存 Popen 句柄，退出时 terminate（lessons 2026-08-17 教训 1：句柄必须保存）
- test() 用 `ssh <host> echo ok` 探测连通性，与隧道分离
- is_alive() 用 poll() 检查进程是否还在
- 自动定位 ssh.exe 全路径：Windows 下 System32/OpenSSH 可能不在 PATH 里
  （桌宠进程启动时继承的 PATH 可能不含 OpenSSH 目录，即使系统已装）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from .ssh_config_parser import SSHHost


@dataclass
class TunnelStatus:
    """隧道状态探测结果。"""
    ok: bool
    message: str


def _find_ssh_executable() -> str:
    """定位 ssh 可执行文件全路径。

    优先级：
    1. shutil.which("ssh") —— PATH 里能找到就用（最快）
    2. Windows 常见路径：C:/Windows/System32/OpenSSH/ssh.exe
    3. 找不到返回 "ssh"，让 subprocess 报原始错误
    """
    found = shutil.which("ssh")
    if found:
        return found
    if sys.platform == "win32":
        candidates = [
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "OpenSSH", "ssh.exe"),
            r"C:\Windows\System32\OpenSSH\ssh.exe",
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
    return "ssh"


class SSHTunnel:
    """单条 SSH 隧道（本地端口 → 远程端口）。

    用法：
        tunnel = SSHTunnel(host_obj, local_port=9880, remote_port=9880)
        if tunnel.test().ok:
            tunnel.start()
            # ... KurisuTTS 连 127.0.0.1:9880 ...
            tunnel.stop()  # 退出时
    """

    def __init__(self, host: SSHHost, local_port: int = 9880, remote_port: int = 9880) -> None:
        self.host = host
        self.local_port = local_port
        self.remote_port = remote_port
        self._proc: subprocess.Popen | None = None

    def test(self, timeout: int = 5) -> TunnelStatus:
        """探测 SSH 连通性（不建隧道）。

        用 `ssh -o ConnectTimeout=<timeout> <host> echo ok`，根据输出判断。
        不用 BatchMode=yes：用户可能用密钥也可能用密码，BatchMode 会拒绝密码交互。
        """
        cmd = [
            _find_ssh_executable(),
            "-o", f"ConnectTimeout={timeout}",
            "-o", "StrictHostKeyChecking=accept-new",
            self.host.host,
            "echo", "__ok__",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 5,
                creationflags=_no_window_flag(),
            )
        except subprocess.TimeoutExpired:
            return TunnelStatus(False, f"SSH 连接超时（{timeout}s）")
        except FileNotFoundError:
            return TunnelStatus(False, "未找到 ssh 命令（请安装 OpenSSH 客户端）")
        except OSError as e:
            return TunnelStatus(False, f"SSH 启动失败：{e}")

        if result.returncode != 0:
            err = (result.stderr or "").strip().splitlines()
            hint = err[-1] if err else f"返回码 {result.returncode}"
            return TunnelStatus(False, f"SSH 失败：{hint}")

        if "__ok__" not in (result.stdout or ""):
            return TunnelStatus(False, "SSH 返回异常（未收到 ok 标记）")
        return TunnelStatus(True, f"连接成功（{self.host.user}@{self.host.hostname}:{self.host.port}）")

    def start(self) -> TunnelStatus:
        """建立 SSH 隧道（后台运行）。

        -N：不执行远程命令
        -f：后台运行（fork 后 ssh 进程脱离终端）
        -o ExitOnForwardFailure=yes：端口转发失败时 ssh 退出，避免假活
        """
        if self._proc is not None and self._proc.poll() is None:
            return TunnelStatus(True, "隧道已在运行")

        cmd = [
            _find_ssh_executable(),
            "-L", f"{self.local_port}:localhost:{self.remote_port}",
            "-N",
            "-f",
            "-o", "ConnectTimeout=5",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            self.host.host,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=_no_window_flag(),
            )
        except FileNotFoundError:
            return TunnelStatus(False, "未找到 ssh 命令")
        except OSError as e:
            return TunnelStatus(False, f"启动失败：{e}")

        # -f 模式下 ssh 会 fork 后退出，主进程很快返回。
        # 等待最多 8 秒让 fork 完成，然后用端口探测是否真的建立。
        time.sleep(2.0)
        if self._is_local_port_open():
            return TunnelStatus(True, f"隧道已建立（本地 {self.local_port} → {self.host.host}:{self.remote_port}）")
        # 端口未开，读 stderr 看原因
        err = ""
        if self._proc and self._proc.stderr:
            try:
                err = self._proc.stderr.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
        return TunnelStatus(False, f"隧道建立失败：{err or '端口未监听'}")

    def stop(self) -> None:
        """终止隧道进程。"""
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        except Exception:
            pass
        finally:
            self._proc = None

    def is_alive(self) -> bool:
        """隧道进程是否还活着。"""
        return self._proc is not None and self._proc.poll() is None

    def _is_local_port_open(self) -> bool:
        """探测本地 local_port 是否被 ssh 监听（隧道建立后会被监听）。"""
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            result = sock.connect_ex(("127.0.0.1", self.local_port))
            return result == 0
        except OSError:
            return False
        finally:
            sock.close()


def _no_window_flag() -> int:
    """Windows 下隐藏 ssh 控制台窗口。"""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0
