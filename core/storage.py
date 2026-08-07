"""JSON 文件存储（替代浏览器 localStorage）。

存储位置：amadeus-py/data/（项目本地，避免 ~/.amadeus 权限问题）
- config.json         # 全局配置（API Key 等，后续步骤使用）
- saved_logins.json   # 记住账号密码

按角色隔离的会话/记忆数据将在后续步骤扩展。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# 默认存储到项目本地 amadeus-py/data/，避免用户目录权限问题
# 若环境变量 AMADEUS_DATA_DIR 被设置则优先使用
_APP_DIR_ENV = os.environ.get("AMADEUS_DATA_DIR")
if _APP_DIR_ENV:
    APP_DIR = Path(_APP_DIR_ENV)
else:
    # amadeus-py/core/storage.py → amadeus-py/data/
    APP_DIR = Path(__file__).resolve().parent.parent / "data"


def _ensure_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(filename: str, default: Any) -> Any:
    """读 JSON 文件，不存在或损坏时返回 default。"""
    path = APP_DIR / filename
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(filename: str, data: Any) -> None:
    _ensure_dir()
    path = APP_DIR / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# === 记住账号密码（对应原 amadeus_saved_logins） ===
def load_saved_logins() -> dict[str, dict[str, str]]:
    """返回 {"accounts": {account: password}, "lastAccount": account}。"""
    data = _load_json("saved_logins.json", {"accounts": {}, "lastAccount": ""})
    if not isinstance(data, dict):
        return {"accounts": {}, "lastAccount": ""}
    data.setdefault("accounts", {})
    data.setdefault("lastAccount", "")
    return data


def save_logins(accounts: dict[str, str], last_account: str) -> None:
    _save_json("saved_logins.json", {"accounts": accounts, "lastAccount": last_account})


# === 全局配置（API Key 等后续步骤使用） ===
def load_config() -> dict[str, Any]:
    return _load_json("config.json", {})


def save_config(config: dict[str, Any]) -> None:
    _save_json("config.json", config)
