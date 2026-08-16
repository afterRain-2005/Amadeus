"""解析 ~/.ssh/config，返回去重后的 Host 列表。

用于设置页面的 SSH Host 下拉选择。仅做读取，不修改用户 SSH 配置。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SSHHost:
    """单个 SSH Host 配置（去重后）。"""
    host: str          # Host 别名（如 "202.114.107.184"）
    hostname: str      # 实际连接地址
    user: str          # 登录用户
    port: int          # 端口

    def display(self) -> str:
        """下拉框显示文本：host (user@hostname:port)"""
        return f"{self.host} ({self.user}@{self.hostname}:{self.port})"


def _ssh_config_path() -> Path:
    """返回 ~/.ssh/config 路径（Windows 兼容 %USERPROFILE%）。"""
    home = Path(os.path.expanduser("~"))
    return home / ".ssh" / "config"


def parse_ssh_config(path: Path | None = None) -> list[SSHHost]:
    """解析 SSH config 文件，返回去重后的 Host 列表。

    解析逻辑（手写，不依赖 paramiko，避免引入新依赖）：
    - 遇到 `Host <name>` 开始新条目
    - 后续 `HostName`/`User`/`Port` 缩进字段填入当前条目
    - HostName 缺省时用 Host 名
    - User 缺省时用当前系统用户名
    - Port 缺省时用 22

    去重：同一 host 名重复出现时只保留第一条（用户的 config 中 115.156.97.117 出现 3 次）。
    """
    if path is None:
        path = _ssh_config_path()
    if not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    hosts: list[SSHHost] = []
    seen: set[str] = set()
    current: dict[str, str] | None = None

    default_user = os.environ.get("USERNAME") or os.environ.get("USER") or ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        key, value = parts[0], parts[1].strip()
        key_lower = key.lower()

        if key_lower == "host":
            if current is not None and current.get("host") not in seen:
                seen.add(current["host"])
                hosts.append(_build_host(current, default_user))
            current = {"host": value.split()[0]}
        elif current is not None:
            if key_lower == "hostname":
                current["hostname"] = value
            elif key_lower == "user":
                current["user"] = value
            elif key_lower == "port":
                current["port"] = value

    if current is not None and current.get("host") not in seen:
        seen.add(current["host"])
        hosts.append(_build_host(current, default_user))

    return hosts


def _build_host(d: dict[str, str], default_user: str) -> SSHHost:
    host = d.get("host", "")
    try:
        port = int(d.get("port", "22"))
    except ValueError:
        port = 22
    return SSHHost(
        host=host,
        hostname=d.get("hostname", host),
        user=d.get("user", default_user),
        port=port,
    )
