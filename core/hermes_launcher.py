"""Hermes 网关生命周期：探活 / 拉起 / API key 同步。

设计依据 docs/superpowers/specs/2026-08-15-agent-mode-design.md §4.2：
- GET /health（Bearer）探活，2s 超时
- 不通 → Popen("hermes -p <profile> gateway") 分离进程，日志落 data/hermes_gateway.log
- 轮询探活最多 30s；仍失败由调用方（backend_router）降级本地直连
- 桌宠退出不杀网关（常驻，同 GPT-SoVITS 惯例）
probe/popen 参数为依赖注入，供测试 mock。
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import httpx


def read_profile_api_key(profile: str = "kurisu") -> str | None:
    """从 ~/.hermes/profiles/<profile>/.env 读 API_SERVER_KEY。"""
    env_path = Path.home() / ".hermes" / "profiles" / profile / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("API_SERVER_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def probe_health(base_url: str, api_key: str = "", timeout: float = 2.0) -> bool:
    """GET /health，Bearer 认证（官方要求 key 必须，含 loopback 部署）。"""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{base_url.rstrip('/')}/health", headers=headers)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def ensure_gateway(
    *,
    base_url: str,
    api_key: str = "",
    profile: str = "kurisu",
    log_path: str | Path = "data/hermes_gateway.log",
    wait_timeout: float = 30.0,
    probe=None,
    popen=subprocess.Popen,
) -> bool:
    """探活 → 不通则拉起网关子进程 → 轮询探活。返回最终是否可用。"""
    probe = probe or probe_health
    if probe(base_url, api_key):
        return True
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with log_file.open("ab") as fh:
        popen(
            ["hermes", "-p", profile, "gateway"],
            stdout=fh, stderr=fh, creationflags=flags,
            stdin=subprocess.DEVNULL,
        )
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        time.sleep(1.0)
        if probe(base_url, api_key):
            return True
    return False
