"""GPT-SoVITS 本地子进程 / SSH 隧道生命周期管理（从 desktop_pet.py 提出）。

模块级句柄 _gpt_sovits_proc/_ssh_tunnel 由本模块独占，
maybe_start_gpt_sovits（幂等拉起）/ stop_gpt_sovits（三段式清理）配对使用。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def _locate_gpt_sovits(root: Path) -> tuple[Path, Path] | None:
    """定位 GPT-SoVITS 的运行环境位置。

    作用：在可能的目录里寻找 gpt_sovits_venv（虚拟环境）和 GPT-SoVITS 源码目录。
          dev 模式：项目根目录下；
          frozen 模式：exe 同目录及其父目录（exe 常放 dist\，父级即项目根）。
    参数：
        root Path 项目根目录（dev 模式为项目根，frozen 模式为临时解压目录）
    返回值：tuple[Path, Path] | None —— (venv 的 python.exe 路径, GPT-SoVITS 目录)；
          找不到返回 None
    """
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else root
    for base in (root, root.parent, exe_dir, exe_dir.parent):
        venv_python = base / "gpt_sovits_venv" / "Scripts" / "python.exe"
        api_dir = base / "GPT-SoVITS"
        if venv_python.exists() and (api_dir / "api_v2.py").exists():
            return venv_python, api_dir
    return None


def maybe_start_gpt_sovits(spawn=subprocess.Popen) -> bool:
    """GPT-SoVITS API 不在线时后台拉起（幂等：在线则跳过）。

    作用：启动本地 GPT-SoVITS 语音合成服务。根据配置决定走
          local（本地子进程）/ ssh（SSH 隧道到远程 GPU）/ auto（自动）。
          拉起后模型加载需数十秒，由 SpeechPlayer 的 available TTL
          重查（60s）自愈衔接，不阻塞 UI。
    参数：
        spawn callable 启动子进程的函数（默认 subprocess.Popen，测试时可注入 mock）
    返回值：bool —— True=本次发起了启动；False=已在运行/启动失败/无需启动
              （阿里云 TTS 时无需启动本地服务，直接返回 False）
    """
    try:
        from config import TTS_PROVIDER_DEFAULT
        from core.storage import load_config
        provider = load_config().get("tts_provider", TTS_PROVIDER_DEFAULT)
        if provider == "aliyun":
            return False
    except Exception:
        pass

    try:
        from core.gpt_sovits_client import KurisuTTS
        if KurisuTTS().available:
            return False
    except Exception:
        pass

    # 读取 GPT-SoVITS 运行模式配置
    try:
        from config import GPT_SOVITS_DEFAULTS
        from core.storage import load_config
        cfg = {**GPT_SOVITS_DEFAULTS, **(load_config().get("gpt_sovits") or {})}
    except Exception:
        cfg = dict(GPT_SOVITS_DEFAULTS) if 'GPT_SOVITS_DEFAULTS' in dir() else {"mode": "local", "ssh_host": "", "local_port": 9880, "remote_port": 9880}

    mode = str(cfg.get("mode", "auto"))
    ssh_host_alias = str(cfg.get("ssh_host", ""))

    # auto 模式：配置了 ssh_host 时优先 SSH，失败回退本地
    if mode in ("ssh", "auto") and ssh_host_alias:
        if _start_ssh_tunnel(ssh_host_alias, cfg):
            return True
        if mode == "ssh":
            return False  # SSH 模式失败不回退本地
        # auto 模式继续尝试本地

    # 本地启动
    located = _locate_gpt_sovits(ROOT)
    if not located:
        return False
    venv_python, api_dir = located
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # stdout/stderr 必须重定向：CREATE_NO_WINDOW 且无重定向时 std 句柄为 NULL，
    # sys.stdout/stderr 为 None，GPT-SoVITS 加载途中会静默死亡（实测）。
    # 重定向到日志同时保留 GPU/模型加载诊断信息。
    log_file = None
    try:
        log_file = open(api_dir / "api_autostart.log", "w", encoding="utf-8", errors="replace")
        stdout = log_file
    except OSError:
        stdout = subprocess.DEVNULL
    try:
        global _gpt_sovits_proc
        _gpt_sovits_proc = spawn(
            [str(venv_python), "api_v2.py"],
            cwd=str(api_dir),
            stdout=stdout,
            stderr=subprocess.STDOUT,
            creationflags=creation,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        # 后台预热：拉起子进程后立即轮询 available，可达即发一次空合成
        # 让模型加载到 GPU。GPT-SoVITS 首次合成需加载模型（冷启动 +10-20s），
        # 预热让真实首句跳过冷启动，首句延迟从 ~14s 降到 ~4s。
        # 预热线程 daemon=True，不阻塞 UI，失败仅打日志。
        threading.Thread(target=_warmup_gpt_sovits, daemon=True).start()
        return True
    except OSError:
        if log_file is not None:
            log_file.close()
        return False


def _warmup_gpt_sovits(max_wait: float = 90.0) -> None:
    """后台预热 GPT-SoVITS 模型到 GPU。

    作用：GPT-SoVITS API 启动后首次合成会触发模型加载（torch 加载权重
          到 GPU 约 10-20s）。预热策略：轮询 KurisuTTS.available（每 0.5s，
          最多 90s），可达即发一次极短日语文本 "あ。" 合成请求，让模型
          提前加载到 GPU，把真实首句延迟从 ~14s 降到 ~4s。
          失败不抛异常：预热是优化项，不影响主流程。
    参数：
        max_wait float 最多等待秒数（默认 90.0）
    返回值：无（None）
    """
    try:
        from core.gpt_sovits_client import KurisuTTS
    except ImportError:
        return
    tts = KurisuTTS(timeout=15.0)
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        # 子进程已死 → API 永远不会可达，立即退出（避免无意义轮询 90s）
        proc = _gpt_sovits_proc
        if proc is not None and hasattr(proc, "poll") and proc.poll() is not None:
            print("[warmup] GPT-SoVITS subprocess died, give up")
            return
        try:
            if tts.available:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        print("[warmup] GPT-SoVITS not online within {max_wait:.0f}s, give up".format(max_wait=max_wait))
        return
    try:
        wav = tts.synthesize("あ。", text_lang="ja")
        size = len(wav) if wav else 0
        print("[warmup] success, wav bytes={}".format(size))
    except Exception as exc:
        print("[warmup] synthesize failed: {}".format(exc))


# 模块级句柄：本地子进程 + SSH 隧道，退出时清理（lessons 2026-08-17 教训 1）
_gpt_sovits_proc = None
_ssh_tunnel = None


def _start_ssh_tunnel(host_alias: str, cfg: dict) -> bool:
    """根据 host 别名建 SSH 隧道。

    作用：从 ~/.ssh/config 解析 Host 信息，用 SSHTunnel 建隧道
          （把远程服务器的 9880 端口映射到本地），这样本地 TTS 客户端
          就能通过隧道访问远程 GPU 服务器上的 GPT-SoVITS。
          隧道句柄保存到模块级 _ssh_tunnel，供 stop_gpt_sovits 清理。
    参数：
        host_alias str  SSH 配置里的 Host 别名（如 "my-gpu-server"）
        cfg        dict GPT-SoVITS 配置（含 local_port/remote_port）
    返回值：bool —— True=隧道建立成功；False=解析失败/无此 Host/启动失败
    """
    global _ssh_tunnel
    try:
        from core.ssh_config_parser import parse_ssh_config
        from core.ssh_tunnel import SSHTunnel
    except ImportError:
        return False

    hosts = parse_ssh_config()
    host_obj = next((h for h in hosts if h.host == host_alias), None)
    if host_obj is None:
        return False

    try:
        local_port = int(cfg.get("local_port", 9880))
        remote_port = int(cfg.get("remote_port", 9880))
    except (TypeError, ValueError):
        local_port, remote_port = 9880, 9880

    tunnel = SSHTunnel(host_obj, local_port=local_port, remote_port=remote_port)
    status = tunnel.start()
    if status.ok:
        _ssh_tunnel = tunnel
        return True
    return False


def stop_gpt_sovits(timeout: float = 5.0) -> None:
    """退出时清理：终止本地子进程 + 停止 SSH 隧道。

    作用：桌宠退出时调用，避免留下占显存/端口的孤儿进程。
          采用三段式终止：terminate（温柔请求）→ wait（等它自己退出）→
          kill（等不及就强杀）。
    参数：
        timeout float 等待子进程退出的秒数（默认 5.0）
    返回值：无（None）
    """
    global _gpt_sovits_proc, _ssh_tunnel
    # 清理本地子进程
    proc = _gpt_sovits_proc
    if proc is not None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
        finally:
            _gpt_sovits_proc = None
    # 清理 SSH 隧道
    tunnel = _ssh_tunnel
    if tunnel is not None:
        try:
            tunnel.stop()
        except Exception:
            pass
        finally:
            _ssh_tunnel = None
