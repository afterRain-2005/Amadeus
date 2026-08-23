"""Pure-Python Live2D desktop pet with a native transparent overlay."""
from __future__ import annotations

import base64
import html
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import multiprocessing as mp
from multiprocessing.connection import Connection
from pathlib import Path
import socket
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid


ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
# 本地依赖目录：用 pip install --target 装的 ddgs/trafilatura 等（避开 anaconda site-packages 沙箱限制）。
# 冻结模式下依赖已打包进 exe，不需要 .libs。
if not getattr(sys, 'frozen', False):
    _libs = ROOT / ".libs"
    if _libs.is_dir():
        sys.path.insert(0, str(_libs))
# 通信走 mp.Pipe(duplex=True) 双向管道（frame 下行 / command 上行），
# 不再用 data/pet_command.json 文件轮询。
from core.ipc_command import apply_command_js
from core.single_instance import acquire_single_instance
from core.storage import APP_DIR as _APP_DIR
READY_FILE = _APP_DIR / "desktop_pet.ready"


def _write_runtime_log(filename: str, content: str) -> Path | None:
    """Write a UTF-8 diagnostic log beside runtime data."""
    try:
        log_dir = _APP_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / filename
        path.write_text(content, encoding="utf-8")
        return path
    except OSError:
        return None


# ============================================================
# 类：QuietHandler
# 作用：HTTP 服务处理器——给 renderer 进程提供静态文件（Live2D 页面）。
#       继承 SimpleHTTPRequestHandler，只覆盖了"不打印访问日志"。
# ============================================================
class QuietHandler(SimpleHTTPRequestHandler):
    # ============================================================
    # 函数：__init__()
    # 作用：构造时指定静态文件根目录为项目根（ROOT）
    # 参数：*args/**kwargs 透传给父类
    # 返回值：无（None）
    # ============================================================
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # ============================================================
    # 函数：log_message()
    # 作用：覆盖父类的日志打印为"什么都不做"，避免请求日志刷屏
    # 参数：format/*args 透传（本来要打印的日志内容，这里直接丢弃）
    # 返回值：无（None）
    # ============================================================
    def log_message(self, format, *args):
        pass


# ============================================================
# 函数：free_port()
# 作用：获取一个系统空闲的端口号（绑定 127.0.0.1:0 让系统随机分配，
#       拿到端口号后立即关闭释放）。用于 renderer 进程的本地 HTTP 服务。
# 参数：无
# 返回值：int —— 空闲端口号
# ============================================================
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


# ============================================================
# 函数：renderer_process()
# 作用：★renderer 子进程的入口（由桌面宠进程 mp.Process 拉起）。
#       在这个独立进程里：启动本地 HTTP 服务器（提供 Live2D 页面静态文件），
#       用 pywebview 创建透明网页窗口加载 Live2D 模型。
#       主循环（stream_frames）：
#         1. 接收 overlay 主进程发来的命令（如"说话/表情"）→ 用 JS 驱动 Live2D
#         2. 截取网页 canvas 画面 → 通过 mp.Pipe 发回主进程显示
#         3. 每秒 15 帧
#       ★注意：import webview 必须放在函数内部（不能放文件顶部）——
#       主进程若提前加载 Qt，子进程的 QtWebEngine 渲染必崩（lessons 8-15 事故）。
# 参数：
#   connection Connection mp.Pipe 的一端（子进程端），用来和主进程收发数据
# 返回值：无（None，函数结束后子进程退出）
# ============================================================
def renderer_process(connection: Connection) -> None:
    try:
        import webview

        port = free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = (
            f"http://127.0.0.1:{port}/live2d/phone_live2d_page.html"
            f"?model=/resources/live2d/kurisu/amadeusV1.model3.json"
        )

        # ============================================================
        # 类：RendererJsApi
        # 作用：pywebview 向 JS 暴露的桥接 API（必须在 create_window 前构造）。
        #       JS 端 Home 键点击 → window.pywebview.api.home_click()
        #       → 通过管道通知主进程 _minimize_to_tray()。
        # ============================================================
        class RendererJsApi:
            def home_click(self) -> None:
                try:
                    connection.send(("home_click", True))
                except (BrokenPipeError, OSError):
                    pass

            def hide_window(self) -> None:
                try:
                    connection.send(("hide_window", True))
                except (BrokenPipeError, OSError):
                    pass

            def close(self) -> None:
                try:
                    connection.send(("close", True))
                except (BrokenPipeError, OSError):
                    pass

        window = webview.create_window(
            "Amadeus Renderer", url=url, width=340, height=720,
            x=-10000, y=-10000, frameless=True, shadow=False,
            js_api=RendererJsApi(),
        )

        def loaded() -> None:
            # ============================================================
            # 函数：stream_frames()
            # 作用：★renderer 主循环（独立线程，daemon=True）。
            #       1. 等模型就绪
            #       2. 循环：接收命令→驱动动作；监听 Home 键；截取"#app 整页"
            #         （手机壳+屏幕+辉光+角色一体）回传主进程 paintEvent 直接显示
            #       每秒 15 帧
            # ============================================================
            def stream_frames() -> None:
                try:
                    # JS 端：给页面原有的单/双击分流提供 pywebview 回调。
                    # 不额外绑定 click，避免双击时第一下 click 立即最小化。
                    home_js = (
                        "(function(){"
                        "  window.__amadeusHomeClick = function() {"
                        "    if (window.pywebview && window.pywebview.api && window.pywebview.api.home_click) {"
                        "      try { window.pywebview.api.home_click(); } catch(e){}"
                        "    }"
                        "  };"
                        "  const menu = document.getElementById('pet-menu');"
                        "  if (menu) menu.addEventListener('click', () => {"
                        "    if (window.pywebview && window.pywebview.api && window.pywebview.api.close) {"
                        "      try { window.pywebview.api.close(); } catch(e){}"
                        "    }"
                        "  });"
                        "})();"
                    )
                    try:
                        window.evaluate_js(home_js)
                    except Exception:
                        pass

                    for _ in range(30):
                        time.sleep(0.25)
                        if window.evaluate_js("document.title") == "KURISU_READY":
                            break
                    else:
                        connection.send(("error", "Live2D model did not become ready"))
                        return

                    # 调 JS 端的 __amadeusComposite()：手机壳 + Live2D 一体合成
                    # （返回 PNG dataURL；304×690；手机 UI 完全在 Web 端用 Canvas 2D 绘制）
                    script = (
                        "(function(){"
                        "  return typeof window.__amadeusComposite === 'function'"
                        "    ? window.__amadeusComposite()"
                        "    : (window.__amadeus && window.__amadeus.app"
                        "       ? window.__amadeus.app.renderer.extract.canvas("
                        "         window.__amadeus.app.stage).toDataURL('image/png')"
                        "       : null);"
                        "})()"
                    )
                    while True:
                        # 接收 overlay→renderer 命令（非阻塞，drain 队列）
                        while connection.poll():
                            try:
                                kind, payload = connection.recv()
                            except (EOFError, OSError):
                                break
                            if kind == "command":
                                js = apply_command_js(payload)
                                if js:
                                    window.evaluate_js(js)
                        data_url = window.evaluate_js(script)
                        if not data_url:
                            time.sleep(1 / 15)
                            continue
                        frame = base64.b64decode(data_url.split(",", 1)[1])
                        connection.send(("frame", frame))
                        time.sleep(1 / 15)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                except Exception:
                    path = _write_runtime_log("renderer-crash.log", traceback.format_exc())
                    try:
                        connection.send(("error", f"Renderer crashed. Log: {path}"))
                    except (BrokenPipeError, OSError):
                        pass
                finally:
                    try:
                        window.destroy()
                    except Exception:
                        pass

            threading.Thread(target=stream_frames, daemon=True).start()

        window.events.loaded += loaded
        try:
            webview.start(gui="edgechromium", debug=False)
        finally:
            server.shutdown()
    except Exception:
        path = _write_runtime_log("renderer-crash.log", traceback.format_exc())
        try:
            connection.send(("error", f"Renderer failed to start. Log: {path}"))
        except (BrokenPipeError, OSError):
            pass


# ============================================================
# 函数：_sync_bubble_accessories()
# 作用：让气泡顶部的名牌（header）和底部的注脚（footer）跟随气泡的
#       几何位置自动对齐。
#       初始化早期 header/footer 可能还没创建（在 reply_bubble 之后才建），
#       所以先用 None 判断做保护，避免 AttributeError 崩溃。
# 参数：
#   header  QWidget|None 顶部名牌控件
#   footer  QWidget|None 底部注脚控件
#   x       int 气泡横坐标
#   y       int 气泡纵坐标（Dock 栏上方）
#   w       int 气泡宽度
#   h       int 气泡高度
# 返回值：无（None）
# ============================================================
def _sync_bubble_accessories(header, footer, corners, status_line, x, y, w, h) -> None:
    """气泡头部名牌/底部注脚/四角括号/状态行跟随气泡几何（fauux 稿⑤⑩④）。

    初始化早期（bubble_header/footer 创建于 reply_bubble 之后）可能先行调用
    _set_bubble_text，此时配件尚未创建，保护式访问避免 AttributeError
    （历史会话存在时 exe/start.bat 启动即静默崩溃）。
    v4：名牌/注脚改为一体标签（贴合气泡上下缘，不再裸叠立绘），
        corners 为 4 个 ⌈⌉⌊⌋ 角括号 QLabel，status_line 为状态行。
    """
    if header is not None:
        hw = min(w, 150)
        header.setGeometry(x + (w - hw) // 2, y - 2, hw, 16)
    if footer is not None:
        fw = min(w, 200)
        footer.setGeometry(x + (w - fw) // 2, y + h - 16, fw, 16)
    if corners is not None:
        c = 12
        corners[0].setGeometry(x - 5, y - 2, c, c)
        corners[1].setGeometry(x + w - 7, y - 2, c, c)
        corners[2].setGeometry(x - 5, y + h - 7, c, c)
        corners[3].setGeometry(x + w - 7, y + h - 7, c, c)
    if status_line is not None:
        status_line.setGeometry(x + 8, y + h + 6, w - 16, 14)


# ============================================================
# 函数：_wrap_bubble_html()
# 作用：把气泡正文包装为富文本 HTML：HTML 转义 + 换行转 <br> +
#       1.5 行距 + 左对齐（QLabel 富文本才能表达 line-height，
#       QSS 不支持 QLabel 行距）。纯函数，便于单元测试。
# 参数：
#   text str 气泡纯文本
# 返回值：str —— 富文本 HTML
# ============================================================
def _wrap_bubble_html(text: str) -> str:
    """气泡正文富文本包装（v4：1.5 行距 + 左对齐）。"""
    safe = html.escape(text).replace("\n", "<br>")
    return (
        "<html><body style='margin:0;line-height:150%;text-align:left;"
        "color:#c1b492;font-size:14px'>" + safe + "</body></html>"
    )


# ============================================================
# 函数：_bubble_size_hint()
# 作用：用 QTextDocument 估算气泡富文本尺寸（QLabel 无法用
#       QFontMetrics 测富文本行距）。纯函数，便于单元测试。
# 参数：
#   html   str     富文本 HTML（_wrap_bubble_html 输出）
#   font   QFont   气泡字体（QTextDocument 默认字体）
#   max_w  int     最大宽度
# 返回值：tuple[int, int] —— (宽, 高)，含 padding 余量
# ============================================================
def _bubble_size_hint(html_text: str, font, max_w: int) -> tuple[int, int]:
    """QTextDocument 估算富文本气泡尺寸（v4）。"""
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setDefaultFont(font)
    doc.setHtml(html_text)
    doc.setTextWidth(max_w - 36)
    return int(doc.idealWidth()) + 36, int(doc.size().height()) + 24


# ============================================================
# 函数：_streamed_display_text()
# 作用：从流式累积文本中提取"当前可上屏"的气泡文字（流式气泡体感提速）。
#       规则：去掉开头 [emotion:xxx] 标签（含只到达一半的残缺标签），
#       只取第一个 === 分隔符之前的内容（残缺的 == 也按分隔符处理），
#       保证显示文本随 delta 到达单调增长，情绪标签/日语段不闪现在气泡里。
#       ★纯函数（不碰 UI、无副作用），方便单元测试。
# 参数：
#   streamed str 流式累积的全部文本
# 返回值：str —— 当前应显示的文本（空串表示尚无可显示内容）
# ============================================================
def _streamed_display_text(streamed: str) -> str:
    """流式阶段的气泡显示文本（纯函数，便于单元测试）。"""
    import re

    text = streamed.lstrip()
    # 情绪标签可能只到达一半（如 "[emotion:smi"），一并容忍去掉
    text = re.sub(r"^\[emotion:[^\]]*\]?", "", text)
    # 孤立的 "[" 片段（可能是 [emotion: 的第一片）不上屏，等标签闭合或正文到达
    if text.startswith("[") and "]" not in text:
        return ""
    # 中文在 === 之前；分隔符本身也可能只到达一半（"=="），按 2 个以上 = 切
    text = re.split(r"={2,}", text, maxsplit=1)[0]
    return text.strip()


# ============================================================
# 函数：_merge_bubble_segments()
# 作用：把分句列表按"<6 字短句并入前句"规则合并成气泡分段。
#       ★纯函数。_show_layered_bubbles 与流式增量分句共用，
#       保证流式期增量分段与最终分段同构（前缀稳定，只增不改前段）。
# 参数：
#   parts list[str] 已按句末标点切好的句子列表
# 返回值：list[str] —— 合并后的分段
# ============================================================
def _merge_bubble_segments(parts: list[str]) -> list[str]:
    """短句（<6 字）并入前一段，避免气泡一次只蹦两三个字。"""
    merged: list[str] = []
    for seg in parts:
        if merged and len(merged[-1]) < 6:
            merged[-1] += seg
        else:
            merged.append(seg)
    return merged


# ============================================================
# 函数：_final_bubble_segments()
# 作用：完整文本 → 最终气泡分段（_show_layered_bubbles 的分段算法抽出）。
#       ★纯函数。
# 参数：
#   text str 完整中文回复
# 返回值：list[str]
# ============================================================
def _final_bubble_segments(text: str) -> list[str]:
    """完整回复的分段（纯函数，便于单元测试）。"""
    import re

    parts = [p.strip() for p in re.split(r'(?<=[。！？!?\n])\s*', text.strip())]
    return _merge_bubble_segments([p for p in parts if p])


# ============================================================
# 函数：_split_stream_segments()
# 作用：流式中文 → (已完成的分段, 未完结尾巴)。流式增量分句核心：
#       每到句末标点即成段，用户无需等整条回复结束（更无需等 TTS 播完）
#       就能单击推进；尾巴是最后一个尚未到句末标点的残句，继续打字机。
#       完成段是最终分段的前缀（追加新句只增长末段/追加新段）。
#       ★纯函数（不碰 UI、无副作用），方便单元测试。
# 参数：
#   display str 流式当前可显示中文（_streamed_display_text 的输出）
# 返回值：(list[str], str) —— (已完成分段, 未完结尾巴)
# ============================================================
def _split_stream_segments(display: str) -> "tuple[list[str], str]":
    """流式文本 → (已完成分段, 未完结尾巴)（纯函数，便于单元测试）。"""
    import re

    text = display.strip()
    if not text:
        return [], ""
    parts = [p.strip() for p in re.split(r'(?<=[。！？!?\n])\s*', text)]
    parts = [p for p in parts if p]
    if not parts:
        return [], ""
    if re.search(r'[。！？!?\n]$', text):
        return _merge_bubble_segments(parts), ""
    return _merge_bubble_segments(parts[:-1]), parts[-1]


# ============================================================
# 函数：_decide_delta_action()
# 作用：AI 回复以流式增量（逐段）到达时，做纯逻辑决策：
#       把新内容累积进 streamed_reply，并决定显示"思考点"动画还是
#       流式更新气泡文字。★纯函数（不碰 UI、无副作用），方便单元测试。
#       决策规则：气泡模式下一旦有可显示内容（_streamed_display_text 非空）
#       即流式上屏并停止思考点动画——首字即见，不等整条回复结束；
#       尚无可显示内容（如只到达 [emotion: 前缀）时继续思考点动画；
#       终端模式激活时不显示思考点也不更新气泡（终端流式回显已替代）。
# 参数：
#   streamed_reply    str  已累积的回复内容
#   text              str  本次新到达的增量文本
#   suppress_thinking bool 是否抑制思考点动画（终端模式为 True）
# 返回值：tuple[str, bool, bool] ——
#   (新的累积内容, 是否显示思考点动画, 是否更新气泡文字)
# ============================================================
def _decide_delta_action(
    streamed_reply: str, text: str, suppress_thinking: bool
) -> tuple[str, bool, bool]:
    """流式增量到达时的纯决策逻辑。

    返回 (new_streamed, should_show_thinking, should_set_bubble_text)：
    - 始终把 text 累积到 streamed_reply；
    - 终端模式（suppress_thinking=True）：终端流式回显已替代气泡，两者都不更新；
    - 气泡模式：_streamed_display_text 非空 → 流式更新气泡文字、停止思考点；
      仍为空（如只有 [emotion: 前缀到达）→ 继续思考点动画。
    """
    new_streamed = streamed_reply + text
    if suppress_thinking:
        return new_streamed, False, False
    if _streamed_display_text(new_streamed):
        return new_streamed, False, True
    return new_streamed, True, False


# ============================================================
# 函数：_decide_send_instant_action()
# 作用：用户按下发送瞬间的"即时反应"决策——返回一个动作字典，
#       让 UI 显示呼吸动画 + thinking 情绪（"让我想想…"由动画取代）。
# 参数：无
# 返回值：dict —— {"show_thinking_dots": bool, "emotion": str}
# ============================================================
def _decide_send_instant_action() -> dict:
    """_send 发送瞬间的即时反应决策：呼吸动画 + thinking emotion。
    返回 {show_thinking_dots, emotion}。不再用静态'让我想想…'。"""
    return {"show_thinking_dots": True, "emotion": "thinking"}


# ============================================================
# 函数：_decide_call_toggle_action()
# 作用：Dock 电话按钮点击时的决策：非通话态→进入通话；通话态→挂断。
#       纯函数便于测试（同 _decide_delta_action 模式）。
# 参数：
#   in_call bool 当前是否正在通话中
# 返回值：dict —— {"enter_call": bool, "hangup": bool}
# ============================================================
def _decide_call_toggle_action(in_call: bool) -> dict:
    """Dock 电话按钮点击决策：非通话态→进入通话，通话态→挂断。

    返回 {enter_call, hangup}。纯函数便于测试（参考 _decide_delta_action 模式）。
    """
    if in_call:
        return {"enter_call": False, "hangup": True}
    return {"enter_call": True, "hangup": False}


# ============================================================
# 函数：_dither_texture_url()
# 作用：返回 fauux 抖动纹理图片的绝对路径（正斜杠格式，
#       因为 QSS 的 url() 引用要求正斜杠，Windows 反斜杠会出错）。
# 参数：无
# 返回值：str —— 纹理图片的绝对路径
# ============================================================
def _dither_texture_url() -> str:
    """fauux 抖动纹理的绝对路径（正斜杠，供 QSS url() 引用）。"""
    return str(ROOT / "resources" / "textures" / "dither_rose.png").replace("\\", "/")


# ============================================================
# 函数：_ensure_dither_texture()
# 作用：首次运行时用 Qt 画图 API 生成 16×16 抖动纹理图片
#       （已存在则跳过）。生成失败时静默忽略（界面退化为纯色）。
# 参数：无
# 返回值：无（None）
# ============================================================
def _ensure_dither_texture() -> None:
    """首次运行时生成 16×16 抖动纹理（已存在则跳过）。失败退化为纯色。"""
    target = ROOT / "resources" / "textures" / "dither_rose.png"
    if target.exists():
        return
    try:
        from PySide6.QtGui import QColor, QImage, QPainter
        target.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(16, 16, QImage.Format_ARGB32)
        img.fill(QColor("#171114"))
        p = QPainter(img)
        try:
            rose = QColor(210, 115, 138, 56)
            cream = QColor(193, 180, 146, 26)
            dark = QColor(0, 0, 0, 128)
            for y in range(0, 16, 4):
                for x in range(0, 16, 4):
                    p.setPen(rose);  p.drawPoint(x, y)
                    p.setPen(cream); p.drawPoint(x + 2, y + 2)
                    p.setPen(dark);  p.drawPoint(x + 1, y + 3)
                p.setPen(QColor(0, 0, 0, 40))
                p.drawLine(0, y + 3, 15, y + 3)
        finally:
            p.end()
        img.save(str(target), "PNG")
    except OSError:
        pass


# ==== CRT 终端 HTML 构建（fauux 令牌：rose #d2738a / cream #c1b492 / b=rose）====

_TERMINAL_ROSE = "#d2738a"
_TERMINAL_CREAM = "#c1b492"
_TERMINAL_DIM = "#8a7f63"
_TERMINAL_PROMPT = "guest@wired:~$"


# ============================================================
# 函数：_render_markdown()
# 作用：把 LLM 回复的 markdown 文本渲染成 HTML（支持代码块/表格等），
#       用于终端显示。markdown 库未安装时退化为纯文本（HTML 转义）。
#       单段落时去掉 <p> 包装，让内容与 "kurisu>" 前缀保持同行。
# 参数：
#   text str markdown 源文本
# 返回值：str —— 渲染后的 HTML
# ============================================================
def _render_markdown(text: str) -> str:
    """把 LLM 回复渲染成 HTML（markdown 支持）。markdown 库缺失时退化为纯文本。"""
    safe_text = html.escape(text)
    try:
        import markdown
        rendered = markdown.markdown(
            safe_text,
            extensions=["fenced_code", "tables", "nl2br"],
            output_format="html5",
        )
    except ImportError:
        return safe_text.replace("\n", "<br>")
    # 单段落时去掉 <p> 包装，保持与 kurisu> 前缀同行
    if rendered.startswith("<p>") and rendered.endswith("</p>") and rendered.count("<p>") == 1:
        rendered = rendered[3:-4]
    return rendered


# ============================================================
# 函数：_build_terminal_line_html()
# 作用：把终端的一行日志渲染成 HTML。按 kind 分类显示：
#       cmd=用户命令 / out=AI 输出（支持 markdown）/ err=错误（玫瑰色!）/
#       tool=工具调用（⟳ 图标）/ diff=文件差异 / 其他=普通行。
# 参数：
#   kind  str   行类型（cmd/out/err/sys/tool/diff）
#   text  str   行内容
#   extra dict|None 附加信息（diff 类型时含 path/old/new）
# 返回值：str —— 单行终端 HTML
# ============================================================
def _build_terminal_line_html(kind: str, text: str, extra: dict | None = None) -> str:
    """单行终端 HTML。kind: cmd/out/err/sys/tool=工具调用/diff=差异。"""
    safe = html.escape(text).replace("\n", "<br>")
    if kind == "cmd":
        return (
            f"<div style='margin:2px 0'><span style='color:{_TERMINAL_ROSE}'>"
            f"{_TERMINAL_PROMPT}</span> <span style='color:{_TERMINAL_CREAM}'>{safe}</span></div>"
        )
    if kind == "out":
        rendered = _render_markdown(text)
        # 块级 markdown（代码块/列表/标题/多段落）用 div 包裹，前缀独立一行；否则同行
        if any(tag in rendered for tag in ("<pre", "<ul", "<ol", "<h1", "<h2", "<h3", "<h4", "<h5", "<h6", "<blockquote", "<table", "<p>")):
            return (
                f"<div style='margin:2px 0'><span style='color:{_TERMINAL_ROSE}'>kurisu&gt;</span></div>"
                f"<div style='color:{_TERMINAL_CREAM}'>{rendered}</div>"
            )
        return (
            f"<div style='margin:2px 0'><span style='color:{_TERMINAL_ROSE}'>kurisu&gt;</span> "
            f"<span style='color:{_TERMINAL_CREAM}'>{rendered}</span></div>"
        )
    if kind == "err":
        return f"<div style='margin:2px 0;color:{_TERMINAL_ROSE}'>! {safe}</div>"
    if kind == "tool":
        return (
            f"<div style='margin:2px 0'><span style='color:{_TERMINAL_ROSE}'>⟳</span> "
            f"<span style='color:{_TERMINAL_DIM}'>{safe}</span></div>"
        )
    if kind == "result":
        return (
            f"<pre style='margin:3px 0 5px 16px;color:{_TERMINAL_CREAM};"
            "white-space:pre-wrap;font-family:Consolas,Microsoft YaHei'>"
            f"{html.escape(text)}</pre>"
        )
    if kind == "diff":
        extra = extra or {}
        return _render_diff_html(str(extra.get("path", "")), extra.get("old"), extra.get("new"))
    return f"<div style='margin:2px 0;color:{_TERMINAL_DIM}'>{safe}</div>"


# ============================================================
# 函数：_render_diff_html()
# 作用：把 str_replace_editor 工具的 old/new 文本渲染成 CRT 风格的行内
#       diff（+ 新增=米黄 / - 删除=玫瑰 / 空格=上下文=暗色），
#       difflib.ndiff 计算逐行差异。
# 参数：
#   path str       文件名（显示在 diff 头部）
#   old  str|None  修改前的文件内容（None=新建文件）
#   new  str|None  修改后的文件内容
# 返回值：str —— diff 的 HTML 片段
# ============================================================
def _render_diff_html(path: str, old: str | None, new: str | None) -> str:
    """把 str_replace_editor 的 old/new 渲染成 CRT 风格行内 diff（+ 新增 / - 删除 / 空格 上下文）。"""
    import difflib
    header = f"<div style='color:{_TERMINAL_DIM}'>diff — {html.escape(path) if path else '(untitled)'}</div>"
    if old is None:
        body = "".join(
            f"<div style='color:{_TERMINAL_CREAM}'>+ {html.escape(line)}</div>"
            for line in (new or "").splitlines()
        )
        if not body:
            body = f"<div style='color:{_TERMINAL_DIM}'>(empty file)</div>"
        return (
            f"<div style='margin:4px 0;border-left:2px solid {_TERMINAL_ROSE};padding-left:8px'>"
            f"{header}{body}</div>"
        )
    rows = []
    for line in difflib.ndiff((old or "").splitlines(), (new or "").splitlines()):
        tag = line[:2]
        content = line[2:]
        if tag == "- ":
            rows.append(f"<div style='color:{_TERMINAL_ROSE}'>- {html.escape(content)}</div>")
        elif tag == "+ ":
            rows.append(f"<div style='color:{_TERMINAL_CREAM}'>+ {html.escape(content)}</div>")
        elif tag == "? ":
            continue
        else:
            rows.append(f"<div style='color:{_TERMINAL_DIM}'>  {html.escape(content)}</div>")
    return (
        f"<div style='margin:4px 0;border-left:2px solid {_TERMINAL_ROSE};padding-left:8px'>"
        f"{header}{''.join(rows)}</div>"
    )


# ============================================================
# 函数：_editor_diff_extra()
# 作用：从 str_replace_editor 工具的 arguments 里提取用于显示 diff 的
#       path/old/new 三元组。按 command 类型处理：
#       create=新建文件（old 为 None）；str_replace=替换（old/new 都有）。
# 参数：
#   args dict 工具的 arguments（含 command/path/old_str/new_str/file_text 等）
# 返回值：dict —— {"path": str, "old": str|None, "new": str|None}
# ============================================================
def _editor_diff_extra(args: dict) -> dict:
    """从 str_replace_editor 的 arguments 提取 diff 的 path/old/new。"""
    command = str(args.get("command", ""))
    path = str(args.get("path", ""))
    if command == "create":
        return {"path": path, "old": None, "new": args.get("file_text", "")}
    if command == "str_replace":
        return {"path": path, "old": args.get("old_str"), "new": args.get("new_str")}
    if command == "insert":
        line = args.get("insert_line")
        label = f"{path}:{line}" if line is not None else path
        return {"path": label, "old": "", "new": args.get("new_str", "")}
    return {"path": path, "old": None, "new": None}


# ============================================================
# 函数：_tool_args_summary()
# 作用：把工具调用的 arguments 压缩成一行简短摘要（最多取前 3 个参数、
#       每个值超过 40 字符截断加 …），用于终端工具行显示。
# 参数：
#   args dict 工具参数
# 返回值：str —— 摘要文本（空参数时返回空字符串）
# ============================================================
def _tool_args_summary(args: dict) -> str:
    if not args:
        return ""
    parts = []
    for key, value in list(args.items())[:3]:
        s = str(value)
        if len(s) > 40:
            s = s[:40] + "…"
        parts.append(f"{key}={s}")
    return " ".join(parts)


def _terminal_token_start(text: str) -> int:
    """返回终端输入中最后一个未闭合参数的起始位置。"""
    token_start = 0
    quote = ""
    for index, character in enumerate(text):
        if quote:
            if character == quote:
                quote = ""
        elif character in ("'", '"'):
            quote = character
        elif character.isspace():
            token_start = index + 1
    return token_start


def _complete_terminal_input(
    text: str,
    history: list[str],
    cwd: str | os.PathLike[str] | None = None,
) -> str | None:
    """按整行历史优先、当前参数文件路径次之补全终端输入。"""
    if not text:
        return None
    history_matches = [item for item in reversed(history) if item.startswith(text) and item != text]
    if history_matches:
        common = os.path.commonprefix(history_matches)
        if len(common) > len(text):
            return common

    token_start = _terminal_token_start(text)
    token = text[token_start:]
    if not token:
        return None
    quote = token[0] if token[0] in ("'", '"') else ""
    raw_token = token[1:] if quote else token
    expanded = os.path.expanduser(raw_token)
    base = Path(cwd) if cwd is not None else Path.cwd()
    candidate_path = Path(expanded)
    search_path = candidate_path if candidate_path.is_absolute() else base / candidate_path

    import glob
    matches = glob.glob(str(search_path) + "*")
    if not matches:
        return None
    displays: list[str] = []
    for match in sorted(matches, key=str.casefold):
        matched_path = Path(match)
        if candidate_path.is_absolute():
            display = str(matched_path)
        else:
            display = os.path.relpath(matched_path, base)
        if matched_path.is_dir():
            display += os.sep
        displays.append(quote + display)
    completed_token = os.path.commonprefix(displays)
    if len(completed_token) <= len(token):
        return None
    return text[:token_start] + completed_token


# ============================================================
# 函数：_line_cache_key()
# 作用：计算终端行的缓存键。含 extra(dict) 的行用 id() 作为键的一部分
#       （dict 不可哈希，且避免重复序列化的开销）。
# 参数：
#   item tuple —— 终端行 (kind, text) 或 (kind, text, extra)
# 返回值：tuple —— 缓存键
# ============================================================
def _line_cache_key(item) -> tuple:
    """终端行的缓存键。extra(dict) 用 id() 避免不可哈希与重复序列化开销。"""
    if len(item) == 3:
        return (item[0], item[1], id(item[2]))
    return (item[0], item[1])


# ============================================================
# 函数：_build_terminal_html()
# 作用：把终端的所有行（lines）拼成完整 HTML（含标题栏"amadeus shell"
#       和分隔线），供 QTextBrowser 显示（自动滚动到底部）。
# 参数：
#   lines list —— 终端行列表，每项为 (kind, text) 或 (kind, text, extra)
# 返回值：str —— 完整终端 HTML
# ============================================================
def _build_terminal_html(lines: list) -> str:
    """终端完整 HTML（QTextBrowser 用，自动滚底）。"""
    parts = [
        f"<div style='color:{_TERMINAL_DIM};font-size:9px'>║▒░ amadeus shell — wired session</div>",
        f"<div style='border-top:1px solid {_TERMINAL_ROSE};margin:2px 0 6px 0'></div>",
    ]
    for item in lines:
        if len(item) == 3:
            kind, text, extra = item
            parts.append(_build_terminal_line_html(kind, text, extra))
        else:
            kind, text = item
            parts.append(_build_terminal_line_html(kind, text))
    return "".join(parts)


# ============================================================
# 函数：run_overlay()
# 作用：★桌宠 UI 主进程的完整实现（本文件最大的函数，~2000 行）。
#       在这里创建 Qt 应用程序和所有 UI 组件：
#       - PetWindow（主窗口：Live2D 覆盖层 + 气泡/Dock/终端/设置）
#       - 各种内部类：AgentTask（AI 请求后台任务）、DockButton、Toolbar、
#         ReplyBubble、TerminalView 等
#       流程：创建 QApplication → 构造 PetWindow → 进入 Qt 事件循环
#       （app.exec()），直到退出返回退出码。
#       ★PySide6 相关导入全部放在本函数内部（延迟导入）——因为
#       desktop_pet.py 主进程顶层绝对不能 import PySide6，否则 renderer
#       子进程的 QtWebEngine 渲染会崩（lessons 8-15 重大事故）。
# 参数：
#   connection Connection mp.Pipe 的一端（主进程端），与 renderer 子进程通信
#   renderer   mp.Process renderer 子进程对象（退出时需 terminate）
# 返回值：int —— Qt 事件循环退出码（0=正常）
# ============================================================
def run_overlay(connection: Connection, renderer: mp.Process) -> int:
    from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRect, QRectF, QRunnable, Qt, QSize, QThreadPool, QTimer, Signal
    from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QIcon, QImage, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPixmap, QRadialGradient
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import (
                QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
                QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
            )

    from config import PHONE_DEFAULTS, get_character_by_id, get_random_greeting
    from core.agent_client import _load_soul_md
    from core.emotion_parser import parse_reply
    from core.ipc_command import serialize_command
    from core.session_manager import active_session, add_message, create_session, load_state, save_state
    from core.skills import SkillManager, build_skill_prompt
    from core.terminal_commands import TerminalCommandContext, registry as terminal_command_registry
    from core.storage import load_config
    from core.terminal_state import load_terminal_state, save_terminal_state
    from core.tts_client import SpeechPlayer
    from ui.settings_dialog import SettingsDialog
    from ui.widgets.crt_overlay import CrtOverlay
    from ui.widgets.crt_title_bar import CrtTitleBar

    character = get_character_by_id("kurisu")

    # ============================================================
    # 函数：send_command()
    # 作用：overlay 主进程 → renderer 子进程发送命令（如 emotion 表情、
    #       speaking 口型），通过 duplex 管道发送序列化后的命令。
    #       管道断裂（renderer 已退出）时静默忽略，不崩溃。
    # 参数：
    #   **payload dict —— 命令参数（如 {"type": "emotion", "value": "blush"}）
    # 返回值：无（None）
    # ============================================================
    def send_command(**payload) -> None:
        """overlay→renderer 发送命令（emotion/speaking），走 duplex 管道。"""
        try:
            connection.send(serialize_command(**payload))
        except (BrokenPipeError, OSError):
            pass

    # ============================================================
    # 类：AgentSignals（QObject）
    # 作用：后台 AI 任务（AgentTask）与主线程之间的信号桥。
    #       Qt 信号可以在线程之间安全传递：AI 任务在后台线程发出信号，
    #       主线程通过 connect 接收并更新 UI。
    #       信号列表：status=状态文本 / delta=流式增量 / finished=完整回复 /
    #       failed=失败信息 / tool_event=工具调用事件 / confirmation=工具审批请求
    # ============================================================
    class AgentSignals(QObject):
        status = Signal(str)
        delta = Signal(str)
        finished = Signal(str)
        failed = Signal(str)
        cancelled = Signal(str)
        tool_event = Signal(object)
        confirmation = Signal(object)

    # ============================================================
    # 类：AgentTask（QRunnable）
    # 作用：★后台 AI 请求任务。把"调用 AI + 工具执行"放到线程池里跑，
    #       避免阻塞 Qt 主线程（否则界面会卡死）。
    #       流程：run() 里调 route_and_send（路由+发消息），结果通过
    #       signals 发回主线程（流式增量发 delta，完整结果发 finished）。
    # ============================================================
    class AgentTask(QRunnable):
        # ============================================================
        # 函数：__init__()
        # 作用：初始化任务，保存对话历史和记忆
        # 参数：
        #   history  list[dict] 对话历史（最后 14 条）
        #   memories list|None  长期记忆
        # 返回值：无（None）
        # ============================================================
        def __init__(
            self,
            history,
            memories=None,
            route_override: str | None = None,
            response_max_tokens: int | None = 700,
            inject_system_prompt: str | None = None,
            terminal_cwd: str | None = None,
            terminal_session_id: str | None = None,
        ) -> None:
            super().__init__()
            self.history = history
            self.memories = memories or []
            self.route_override = route_override
            self.response_max_tokens = response_max_tokens
            self.inject_system_prompt = inject_system_prompt
            self.terminal_cwd = terminal_cwd
            self.terminal_session_id = terminal_session_id
            self.cancel_event = threading.Event()
            self.signals = AgentSignals()

        def cancel(self) -> None:
            self.cancel_event.set()

        # ============================================================
        # 函数：run()
        # 作用：★任务的线程入口（QThreadPool 调用）。
        #       读取 SOUL.md（或人设回退），调用 route_and_send 发消息，
        #       期间所有状态通过 self.signals 信号发出。
        #       成功→finished(reply)；任何异常→failed(错误信息)。
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def run(self) -> None:
            config = dict(load_config())
            if self.route_override:
                router = dict(config.get("agent_router") or {})
                if self.route_override == "local":
                    router["mode"] = "chat"
                    router["auto_route"] = False
                elif self.route_override == "terminal_auto":
                    router["mode"] = "chat"
                    router["auto_route"] = True
                    router["auto_targets"] = list(router.get("auto_targets") or ["local", "harness"])
                else:
                    router["mode"] = self.route_override
                    router["auto_route"] = False
                config["agent_router"] = router
            if self.terminal_cwd:
                harness_cfg = dict(config.get("harness") or {})
                harness_cfg["cwd"] = self.terminal_cwd
                config["harness"] = harness_cfg
            # 读取 SOUL.md（若存在），否则回退到 config.py 中的 KURISU_PERSONALITY
            soul_md = _load_soul_md("kurisu") or character.personality
            # 聊天屏幕感知：开启时附加当前屏幕一句话描述（缓存内复用，失败静默）
            try:
                from core.screen_context import build_screen_prompt
                screen_prompt = build_screen_prompt(config)
                if screen_prompt:
                    self.inject_system_prompt = (
                        f"{self.inject_system_prompt}\n\n{screen_prompt}"
                        if self.inject_system_prompt else screen_prompt
                    )
            except Exception:
                pass
            try:
                from core.backend_router import route_and_send
                reply, _backend = route_and_send(
                    config=config,
                    input_text=self.history[-1]["content"],
                    soul_md=soul_md,
                    conversation_history=self.history[:-1],
                    memories=self.memories,
                    on_status=self.signals.status.emit,
                    on_delta=self.signals.delta.emit,
                    on_tool_event=self.signals.tool_event.emit,
                    on_approval=self._handle_approval,
                    inject_system_prompt=self.inject_system_prompt,
                    response_max_tokens=self.response_max_tokens,
                    cancel_event=self.cancel_event,
                    harness_session_id=self.terminal_session_id,
                )
                if self.cancel_event.is_set():
                    self.signals.cancelled.emit(reply)
                else:
                    self.signals.finished.emit(reply)
            except Exception as exc:
                if self.cancel_event.is_set():
                    self.signals.cancelled.emit("")
                else:
                    self.signals.failed.emit(str(exc))

        # ============================================================
        # 函数：_handle_approval()
        # 作用：★工具审批（危险操作需要用户确认）。
        #       AI 想执行高权限工具时，发送 confirmation 信号到主线程，
        #       主线程弹出确认框；本函数用 Event 阻塞等待用户选择
        #       （once/session/always/deny），然后返回选择结果。
        # 参数：
        #   payload dict 工具调用详情（command/description 等）
        # 返回值：str —— 用户选择："once" | "session" | "always" | "deny"
        # ============================================================
        def _handle_approval(self, payload: dict) -> str:
            import threading
            request = {"payload": payload, "event": threading.Event(), "choice": "deny"}
            self.signals.confirmation.emit(request)
            while not request["event"].wait(0.1):
                if self.cancel_event.is_set():
                    return "deny"
            return request["choice"]

    # ============================================================
    # 类：DockButton（QPushButton）
    # 作用：Dock 单个按钮：SVG 图标 + hover 放大动画 + 按压回弹。
    #       fauux 刻蚀按钮物理感（hover 放大、按下缩小）。
    # ============================================================
    class DockButton(QPushButton):
        """Dock 单个按钮：SVG 图标 + hover 放大。"""
        BASE_SIZE = 40
        HOVER_SIZE = 48
        NEAR_SIZE = 44

        # ============================================================
        # 函数：__init__()
        # 作用：初始化按钮：设置大小/提示/鼠标指针，加载 SVG 图标
        #       （QSvgRenderer 渲染），创建 hover 放大和按下回弹动画
        # 参数：
        #   icon_name str   图标文件名（resources/icons/{name}.svg）
        #   tooltip   str   鼠标悬停提示文字
        #   is_danger bool  是否危险按钮（退出），视觉弱化
        #   parent    QWidget|None 父控件
        # 返回值：无（None）
        # ============================================================
        def __init__(self, icon_name: str, tooltip: str, is_danger: bool = False, parent=None):
            super().__init__(parent)
            self._icon_name = icon_name
            self._is_danger = is_danger
            self._pinned = False
            self.setFixedSize(self.BASE_SIZE, self.BASE_SIZE)
            self.setToolTip(tooltip)
            self.setCursor(Qt.PointingHandCursor)
            self._renderer = QSvgRenderer(QByteArray(
                (ROOT / "resources" / "icons" / f"{icon_name}.svg").read_bytes()
            ))
            self._scale = 1.0
            self._hover_anim = QPropertyAnimation(self, b"scale", self)
            self._hover_anim.setDuration(200)
            self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
            # 按压反馈：快速下压 + 释放回弹（fauux 刻蚀按钮物理感）
            self._press_anim = QPropertyAnimation(self, b"scale", self)
            self._press_anim.setDuration(110)
            self._press_anim.setEasingCurve(QEasingCurve.OutQuad)

        # ============================================================
        # 函数：mousePressEvent()
        # 作用：按下时播放"快速下压"动画（缩小到 88%，制造按压反馈）
        # 参数：
        #   event QMouseEvent 鼠标事件
        # 返回值：无（None）
        # ============================================================
        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.LeftButton:
                self._hover_anim.stop()
                self._press_anim.stop()
                self._press_anim.setStartValue(self._scale)
                self._press_anim.setEndValue(max(self._scale * 0.88, 0.68))
                self._press_anim.start()
            super().mousePressEvent(event)

        # ============================================================
        # 函数：mouseReleaseEvent()
        # 作用：松开时回弹：仍在按钮上则放大到 hover 尺寸，否则恢复 1.0
        # 参数：
        #   event QMouseEvent 鼠标事件
        # 返回值：无（None）
        # ============================================================
        def mouseReleaseEvent(self, event) -> None:
            super().mouseReleaseEvent(event)
            self._press_anim.stop()
            target = DockButton.HOVER_SIZE / DockButton.BASE_SIZE if self.underMouse() else 1.0
            self.set_target_scale(target)

        # ============================================================
        # 函数：get_scale() / set_scale()
        # 作用：缩放比例属性（property "scale" 供 QPropertyAnimation 动画用）。
        #       set_scale 会按比例改变按钮的固定尺寸并重绘。
        # 参数（set_scale）：
        #   value float 目标缩放比例（1.0=原始大小）
        # 返回值：get 返回 float 当前缩放比例；set 无返回
        # ============================================================
        def get_scale(self) -> float:
            return self._scale

        def set_scale(self, value: float) -> None:
            self._scale = value
            size = int(self.BASE_SIZE * value)
            self.setFixedSize(size, size)
            self.update()

        scale = property(get_scale, set_scale)

        # ============================================================
        # 函数：set_target_scale()
        # 作用：启动 hover 缩放动画（从当前缩放平滑过渡到目标缩放）
        # 参数：
        #   scale float 目标缩放比例
        # 返回值：无（None）
        # ============================================================
        def set_target_scale(self, scale: float) -> None:
            self._hover_anim.stop()
            self._hover_anim.setStartValue(self._scale)
            self._hover_anim.setEndValue(scale)
            self._hover_anim.start()

        # ============================================================
        # 函数：set_pinned()
        # 作用：设置固定态（v4）：固定时玫瑰常亮填充 + 实线边框，
        #       让用户能感知当前是否已固定（原 _toggle_pin 无视觉反馈）
        # 参数：
        #   pinned bool 是否固定
        # 返回值：无（None）
        # ============================================================
        def set_pinned(self, pinned: bool) -> None:
            self._pinned = pinned
            self.update()

        # ============================================================
        # 函数：paintEvent()
        # 作用：自定义绘制按钮：按状态（按下/hover/固定/危险/普通）画不同
        #       玫瑰色背景边框，再渲染 SVG 图标（按下时下沉 1px）
        # 参数：
        #   event QPaintEvent 绘制事件
        # 返回值：无（None）
        # ============================================================
        def paintEvent(self, event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            # 状态视觉（fauux：玫瑰 #d2738a，直角；按下=填充+粗框，hover=微亮）
            pressed = self.isDown()
            hovered = self.underMouse()
            if pressed:
                bg = QColor(210, 115, 138, 46)
                border = QColor(210, 115, 138, 255)
            elif hovered:
                bg = QColor(210, 115, 138, 10)
                border = QColor(210, 115, 138, 200)
            elif self._pinned:
                bg = QColor(210, 115, 138, 60)
                border = QColor(210, 115, 138, 255)
            elif self._is_danger:
                bg = QColor(0, 0, 0, 0)
                border = QColor(210, 115, 138, 110)
            else:
                bg = QColor(0, 0, 0, 0)
                border = QColor(210, 115, 138, 100)
            painter.setBrush(bg)
            if pressed:
                from PySide6.QtGui import QPen
                painter.setPen(QPen(border, 2))
            else:
                painter.setPen(border)
            painter.drawRect(self.rect())
            # SVG 图标（文件已带颜色，直接渲染；按下时下沉 1px 制造按压感）
            pad = 5 if pressed else 4
            self._renderer.render(
                painter,
                QRectF(pad, pad + (1 if pressed else 0),
                       self.width() - pad * 2, self.height() - pad * 2),
            )

    # ============================================================
    # 类：StatusBar（QWidget）
    # 作用：手机屏幕顶部状态栏——时间 + 网络信号（终端风，Qt 实现）。
    #       替代原 HTML 状态栏（已从 phone_live2d_page.html 移除），
    #       与 Dock 一样由 Qt 侧渲染，不经 web 截图。
    # ============================================================
    class StatusBar(QWidget):
        """顶部状态栏：kurisu> 时间（光标闪烁）· 在线灯 频道 信号。"""
        _MONO = "Consolas, 'Courier New', 'Microsoft YaHei', monospace"

        # ============================================================
        # 函数：__init__()
        # 作用：初始化透明背景状态栏与水平布局，创建文本控件
        # 参数：
        #   parent QWidget|None 父控件
        # 返回值：无（None）
        # ============================================================
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 3, 12, 0)
            layout.setSpacing(7)
            layout.setAlignment(Qt.AlignVCenter)
            self.setLayout(layout)

            # 左侧：kurisu> 提示符（粉）+ 时间（弱化）+ 闪烁光标（粉）
            self._prompt = QLabel("kurisu&gt;")
            self._prompt.setStyleSheet(f"color:#d2738a;font:9px {self._MONO};letter-spacing:1px;")
            self._clock = QLabel("--:--")
            self._clock.setStyleSheet(f"color:#8a7f63;font:9px {self._MONO};letter-spacing:1px;")
            self._cursor = QLabel("\u2588")
            self._cursor.setStyleSheet(f"color:#d2738a;font:9px {self._MONO};")

            # 右侧：在线灯（绿）+ 频道名（弱化）+ 信号（粉三角）
            self._dot = QLabel("\u25CF")
            self._dot.setStyleSheet("color:#34c759;font:10px Consolas,monospace;")
            self._ch = QLabel("wire ch 1")
            self._ch.setStyleSheet(f"color:#8a7f63;font:9px {self._MONO};")
            self._sig = QLabel("\u25B2\u25B2\u25B2")
            self._sig.setStyleSheet("color:#d2738a;font:9px Consolas,monospace;letter-spacing:-2px;")

            layout.addWidget(self._prompt)
            layout.addWidget(self._clock)
            layout.addWidget(self._cursor)
            layout.addStretch(1)
            layout.addWidget(self._dot)
            layout.addWidget(self._ch)
            layout.addWidget(self._sig)

            # 光标闪烁（0.5s 周期）
            self._blink = QTimer(self)
            self._blink.timeout.connect(self._toggle_cursor)
            self._blink.start(500)

            # 时钟刷新（30s 一次，同原 HTML 频率）
            self._tick = QTimer(self)
            self._tick.timeout.connect(self._update_time)
            self._tick.start(30000)
            self._update_time()

        # ============================================================
        # 函数：_update_time()
        # 作用：刷新当前时间到时钟标签（HH:MM）
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _update_time(self) -> None:
            self._clock.setText(time.strftime("%H:%M"))

        # ============================================================
        # 函数：_toggle_cursor()
        # 作用：交替显示/隐藏闪烁光标块，模拟终端光标
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _toggle_cursor(self) -> None:
            self._cursor.setVisible(not self._cursor.isVisible())

    # ============================================================
    # 类：DockBar（QWidget）
    # 作用：底部悬浮 Dock 工具栏：6 个按钮（对话/电话/固定/设置/终端/退出）
    #       + hover 时邻近按钮依次放大的联动效果。
    # ============================================================
    class DockBar(QWidget):
        """底部悬浮 Dock 工具栏：5 按钮 + hover 邻近放大。"""
        # ============================================================
        # 函数：__init__()
        # 作用：初始化透明背景工具栏和水平布局，创建全部按钮
        # 参数：
        #   parent QWidget|None 父控件
        # 返回值：无（None）
        # ============================================================
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self._buttons: list = []
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(8)
            layout.setAlignment(Qt.AlignCenter)
            self.setLayout(layout)
            self._build_buttons()

        # ============================================================
        # 函数：_build_buttons()
        # 作用：按规格表创建 6 个 DockButton（图标名/提示/是否危险），
        #       装入列表并加入布局，每个按钮安装事件过滤器（用于
        #       hover 联动放大）
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _build_buttons(self) -> None:
            specs = [
                ("chat", "对话", False),
                ("phone", "电话", False),
                ("pin", "固定", False),
                ("settings", "设置", False),
                ("terminal", "终端", False),
                ("close", "退出", True),
            ]
            for icon_name, tooltip, is_danger in specs:
                btn = DockButton(icon_name, tooltip, is_danger, self)
                btn.installEventFilter(self)
                self._buttons.append(btn)
                self.layout().addWidget(btn)

        # ============================================================
        # 函数：eventFilter()
        # 作用：监听按钮的鼠标 Enter/Leave 事件，触发邻近放大动画
        # 参数：
        #   obj   QObject 事件来源对象
        #   event QEvent   事件
        # 返回值：bool —— True=事件已处理；False=继续传给父类
        # ============================================================
        def eventFilter(self, obj, event) -> bool:
            if obj in self._buttons:
                idx = self._buttons.index(obj)
                if event.type() == event.Type.Enter:
                    self._apply_hover_scale(idx)
                elif event.type() == event.Type.Leave:
                    self._apply_leave_scale()
            return super().eventFilter(obj, event)

        # ============================================================
        # 函数：_apply_hover_scale()
        # 作用：hover 联动：被 hover 的按钮放大到 HOVER_SIZE，
        #       相邻按钮放大到 NEAR_SIZE，其余恢复 1.0
        # 参数：
        #   hover_idx int 被 hover 按钮的索引
        # 返回值：无（None）
        # ============================================================
        def _apply_hover_scale(self, hover_idx: int) -> None:
            for i, btn in enumerate(self._buttons):
                dist = abs(i - hover_idx)
                if dist == 0:
                    btn.set_target_scale(DockButton.HOVER_SIZE / DockButton.BASE_SIZE)
                elif dist == 1:
                    btn.set_target_scale(DockButton.NEAR_SIZE / DockButton.BASE_SIZE)
                else:
                    btn.set_target_scale(1.0)

        # ============================================================
        # 函数：_apply_leave_scale()
        # 作用：鼠标离开 Dock 时所有按钮恢复原始大小
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _apply_leave_scale(self) -> None:
            for btn in self._buttons:
                btn.set_target_scale(1.0)

        # ============================================================
        # 函数：button()
        # 作用：按提示文字查找按钮（如 button("对话")）
        # 参数：
        #   name str 按钮 tooltip（提示文字）
        # 返回值：DockButton —— 匹配的按钮；找不到抛 KeyError
        # ============================================================
        def button(self, name: str):
            for btn in self._buttons:
                if btn.toolTip() == name:
                    return btn
            raise KeyError(name)

    # ============================================================
    # 类：GlitchLabel（QWidget）
    # 作用：全角标题 + CRT glitch 双色分裂动画（参考 fauux .glitch）。
    #       主文字 cream，两层偏移副本（rose 浅 / dim 暗）只在偶发 glitch
    #       帧的随机水平条带内显示，制造 RGB 分裂撕裂感；多数时间保持稳定。
    # ============================================================
    class GlitchLabel(QWidget):
        """全角标题 + CRT glitch 双色分裂动画（参考 fauux .glitch）。

        主文字 cream，两层偏移副本（rose 浅 / dim 暗）只在偶发 glitch 帧的
        随机水平条带内显示，制造 RGB 分裂撕裂感；多数时间保持稳定不跳动。
        配色保持 rose/cream 系，色调不变。
        """

        # ============================================================
        # 函数：__init__()
        # 作用：初始化标签文字，启动 70ms 定时器驱动 glitch 动画帧
        # 参数：
        #   text   str          显示的标题文字
        #   parent QWidget|None 父控件
        # 返回值：无（None）
        # ============================================================
        def __init__(self, text: str, parent=None):
            super().__init__(parent)
            self._text = text
            self._seed = 0
            self._glitch_frames = 0
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            self._timer = QTimer(self)
            self._timer.setInterval(70)
            self._timer.timeout.connect(self._advance)
            self._timer.start()

        # ============================================================
        # 函数：_advance()
        # 作用：动画推进：更新随机种子，约 3% 概率进入 glitch 状态
        #       （持续 2~4 帧约 140~280ms），其余时间保持稳定，然后重绘
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _advance(self) -> None:
            self._seed = (self._seed + 1) % 10000
            import random
            rnd = random.Random(self._seed)
            if self._glitch_frames > 0:
                self._glitch_frames -= 1
            elif rnd.random() < 0.03:
                # 偶发撕裂：持续 2~4 帧（约 140~280ms），其余时间保持稳定
                self._glitch_frames = rnd.randint(2, 4)
            self.update()

        # ============================================================
        # 函数：_font()
        # 作用：构建标题字体（Consolas 15px 粗体 + 112% 字距）
        # 参数：无
        # 返回值：QFont —— 标题字体
        # ============================================================
        def _font(self) -> QFont:
            font = QFont("Times New Roman")
            font.setPixelSize(13)
            font.setBold(True)
            return font

        # ============================================================
        # 函数：sizeHint()
        # 作用：根据文字宽度估算控件的建议尺寸
        # 参数：无
        # 返回值：QSize —— 建议尺寸（文字宽 + 16，文字高 + 10）
        # ============================================================
        def sizeHint(self):
            fm = QFontMetrics(self._font())
            return QSize(fm.horizontalAdvance(self._text) + 16, fm.height() + 10)

        # ============================================================
        # 函数：paintEvent()
        # 作用：绘制标题：先画主文字（cream），glitch 状态时在随机
        #       水平条带内画 rose/dim 两层偏移副本（模拟 RGB 撕裂）
        # 参数：
        #   event QPaintEvent 绘制事件
        # 返回值：无（None）
        # ============================================================
        def paintEvent(self, event) -> None:
            import random
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setFont(self._font())
                rect = self.rect()
                # 主文字 cream
                painter.setPen(QColor(193, 180, 146))
                painter.drawText(rect, Qt.AlignCenter, self._text)

                if self._glitch_frames <= 0:
                    return
                rnd = random.Random(self._seed)
                # 随机 1~3 个水平条带，仅在条带内显示偏移副本（模拟 clip-path 撕裂）
                for _ in range(rnd.randint(1, 3)):
                    y = rnd.randrange(max(1, self.height()))
                    h = rnd.randint(2, 7)
                    painter.save()
                    painter.setClipRect(0, y, self.width(), h)
                    painter.setPen(QColor(210, 115, 138))
                    painter.drawText(rect.translated(-2, -1), Qt.AlignCenter, self._text)
                    painter.restore()
                    painter.save()
                    painter.setClipRect(0, y, self.width(), h)
                    painter.setPen(QColor(138, 127, 99))
                    painter.drawText(rect.translated(2, 1), Qt.AlignCenter, self._text)
                    painter.restore()
            finally:
                painter.end()

    # ============================================================
    # 类：AgentTerminal（QDialog）
    # 作用：★独立 CRT 命令行 agent 窗口（fauux 风格，类 Codex CLI）。
    #       终端日志区 + 命令行输入框 + blink 光标 + CRT 特效
    #       （扫描线/暗角/噪点/闪烁）+ 输入历史/
    #       Tab 补全/Ctrl+C 中断。与 SettingsDialog 同级的独立窗口。
    # ============================================================
    class AgentTerminal(QDialog):
        """独立 CRT 命令行 agent 窗口（fauux 风格，类 Codex CLI）。

        与 SettingsDialog 同级的独立窗口。设计令牌取自
        fauux.neocities.org/stylesheet.css 实测：rose #d2738a 强调 /
        cream #c1b492 正文 / Times 衬线 / ⌈⌉ 角括号 / ║▒░ 分隔符 /
        blink 光标 / wiredB text-shadow rose 1px 4px 5px 辉光。
        """
        submitted = Signal(str)
        interrupt_requested = Signal()

        # ============================================================
        # 函数：__init__()
        # 作用：★构建整个终端窗口：标题栏（glitch + 辉光 + 关闭钮）、
        #       日志区（QTextBrowser）、输入行（提示符+输入框+blink光标）、
        #       各种定时器（blink/渲染节流/CRT闪烁/噪点）。
        #       关键细节：关闭按钮必须关掉 autoDefault（否则回车误触发
        #       关闭）；渲染用 33ms 节流合并 setHtml 防卡死。
        # 参数：
        #   parent QWidget|None 父控件
        # 返回值：无（None）
        # ============================================================
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Amadeus Terminal")
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
            self.setObjectName("agentTerminal")
            self.setStyleSheet(
                "QDialog#agentTerminal{background-color:#171114;"
                "color:#c1b492;"
                "background-image:url(" + _dither_texture_url() + ");"
                "border:1px solid #d2738a;}"
            )
            self.setMinimumSize(560, 390)
            self.resize(720, 520)
            self._lines: list = []
            self._line_cache: dict = {}  # 行级 HTML 缓存，避免每 delta 全量 markdown 重渲染
            self._rendered_count: int = 0  # 已渲染进 QTextBrowser 的行数（增量刷新基准）
            self._needs_rebuild: bool = True  # 行列表被整体更换（任务切换）时强制全量重建
            self._line_starts: list[int] = []  # 每个逻辑行在 QTextDocument 中的起始位置
            self._dirty_from: int | None = None
            self._history: list[str] = []
            self._history_index: int = -1
            self._pending_approval: dict | None = None
            layout = QVBoxLayout(self)
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(10)

            self.title_bar = CrtTitleBar(
                "⌈ Ａｍａｄｅｕｓ Ｔｅｒｍｉｎａｌ ⌋",
                "wire ESTABLISHED · ch 1",
                self,
                self.close,
            )
            self.title = self.title_bar.title_label
            self.close_btn = self.title_bar.close_button
            layout.addWidget(self.title_bar)

            # 日志区（主体）
            self.log = QTextBrowser(self)
            self.log.setStyleSheet(
                "QTextBrowser{background-color:transparent;"
                f"color:{_TERMINAL_CREAM};border:1px solid {_TERMINAL_ROSE};"
                "border-radius:0px;padding:12px;font:14px 'Consolas','Microsoft YaHei'}"
                f"QTextBrowser{{selection-background-color:{_TERMINAL_ROSE};selection-color:#171114}}"
                f"QScrollBar:vertical{{background:rgba(210,115,138,0.15);width:6px;margin:4px}}"
                f"QScrollBar::handle:vertical{{background:{_TERMINAL_ROSE};border-radius:0px;min-height:30px}}"
                "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
                "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}"
            )
            self.log.setOpenExternalLinks(False)
            # 超链接：默认不自动导航，Ctrl+点击用系统浏览器打开
            self.log.setOpenLinks(False)
            self.log.anchorClicked.connect(self._open_external_link)
            layout.addWidget(self.log, 1)

            # 审批条：危险工具请求直接在终端内确认，不打断 CRT 工作流
            self.approval_panel = QWidget(self)
            self.approval_panel.setStyleSheet(
                "QWidget{background:rgba(23,17,20,220);border:1px solid #d2738a;}"
                "QLabel{color:#c1b492;border:0;background:transparent;}"
                "QPushButton{color:#d2738a;background:#171114;border:1px solid #d2738a;"
                "padding:4px 8px;font:12px 'Consolas','Microsoft YaHei';}"
                "QPushButton:hover{color:#171114;background:#d2738a;}"
            )
            approval_layout = QVBoxLayout(self.approval_panel)
            approval_layout.setContentsMargins(8, 6, 8, 6)
            approval_layout.setSpacing(6)
            self.approval_label = QLabel(self.approval_panel)
            self.approval_label.setWordWrap(True)
            approval_layout.addWidget(self.approval_label)
            approval_buttons = QHBoxLayout()
            for label, choice in (
                ("仅本次", "once"),
                ("本次会话", "session"),
                ("始终允许", "always"),
                ("拒绝", "deny"),
            ):
                button = QPushButton(label, self.approval_panel)
                button.setAutoDefault(False)
                button.setDefault(False)
                button.clicked.connect(lambda _checked=False, value=choice: self._resolve_approval(value))
                approval_buttons.addWidget(button)
            approval_layout.addLayout(approval_buttons)
            self.approval_panel.hide()
            layout.addWidget(self.approval_panel)

            # 输入行：rose 提示符 + 输入框 + 闪烁块光标（下边框式）
            input_row = QHBoxLayout()
            input_row.setSpacing(6)
            prompt = QLabel(_TERMINAL_PROMPT, self)
            prompt.setStyleSheet(
                f"color:{_TERMINAL_ROSE};background:transparent;font:13px 'Consolas','Microsoft YaHei'"
            )
            self.input = QLineEdit(self)
            self.input.setStyleSheet(
                "QLineEdit{background:transparent;color:#c1b492;border:0;border-bottom:1px solid #d2738a;"
                "padding:4px 2px;font:13px 'Consolas','Microsoft YaHei'}"
                "QLineEdit::placeholder{color:#8a7f63}"
            )
            self.input.setPlaceholderText("say something to kurisu…")
            self.input.returnPressed.connect(self._submit)
            self.input.installEventFilter(self)
            # / 命令下拉补全面板：输入 / 时弹出，上下键选中，回车/Tab 填入命令
            self._slash_commands: list[tuple[str, str]] = terminal_command_registry.slash_completions()
            self._slash_panel = QListWidget(self)
            self._slash_panel.setStyleSheet(
                "QListWidget{background:#171114;color:#c1b492;border:1px solid #d2738a;"
                "font:12px 'Consolas','Microsoft YaHei';outline:0;}"
                "QListWidget::item{padding:5px 8px;}"
                "QListWidget::item:selected{background:#d2738a;color:#171114;}"
            )
            self._slash_panel.setFocusPolicy(Qt.NoFocus)
            self._slash_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._slash_panel.hide()
            self._slash_panel.itemClicked.connect(self._slash_item_clicked)
            self.input.textChanged.connect(self._refresh_slash_panel)
            self.cursor = QLabel("█", self)
            self.cursor.setStyleSheet(
                f"color:{_TERMINAL_CREAM};background:transparent;font:13px 'Consolas','Microsoft YaHei'"
            )
            input_row.addWidget(prompt)
            input_row.addWidget(self.input, 1)
            input_row.addWidget(self.cursor)
            layout.addLayout(input_row)

            # blink 光标：530ms 切换可见性（近似 CSS blink 50%→黑=隐没于黑底）
            self._blink_timer = QTimer(self)
            self._blink_timer.setInterval(530)
            self._blink_timer.timeout.connect(lambda: self.cursor.setVisible(not self.cursor.isVisible()))
            self._blink_timer.start()

            # 渲染节流：流式 delta 高频到达时合并 setHtml，避免主线程被全量重渲染卡死
            self._render_timer = QTimer(self)
            self._render_timer.setSingleShot(True)
            self._render_timer.setInterval(33)
            self._render_timer.timeout.connect(self._flush_render)

            # CRT 屏幕闪烁：整个窗口 opacity 轻微波动（参考 fauux 的 CRT 刷新感）
            self._flicker_effect = QGraphicsOpacityEffect(self)
            self._flicker_effect.setOpacity(1.0)
            self.setGraphicsEffect(self._flicker_effect)
            self._flicker_timer = QTimer(self)
            self._flicker_timer.setInterval(70)
            self._flicker_timer.timeout.connect(self._flicker)
            self._flicker_timer.start()

            # CRT 背景特效：扫描线 + 暗角 + 静电噪点画在背景层，
            # 文字控件（log / 输入框）绘制其上，保持清晰不被扫描线/噪点覆盖
            self._noise_seed = 0
            self._noise_timer = QTimer(self)
            self._noise_timer.setInterval(80)
            self._noise_timer.timeout.connect(self._advance_noise)
            self._noise_timer.start()

            # 背景 logo 水印：加载后暗化，作为 CRT 背景的底层衬底
            # 背景图片（terminal_back.png）+ logo 水印：图片铺满终端，logo 叠放其上
            self._bg = self._load_background()
            self._bg_scaled = None  # 已按当前窗口尺寸缩放好的背景缓存
            self._bg_size = (0, 0)
            self._logo = self._load_logo()

        # ============================================================
        # 函数：resizeEvent()
        # 作用：终端尺寸变化时通知主窗口重新定位
        # 参数：
        #   event QResizeEvent 尺寸变化事件
        # 返回值：无（None）
        # ============================================================
        def resizeEvent(self, event) -> None:
            super().resizeEvent(event)
            # 终端固定在主窗口左侧：尺寸变化时通知主窗口重新定位
            p = self.parentWidget()
            if p is not None and hasattr(p, "_position_terminal"):
                p._position_terminal()

        # ============================================================
        # 函数：_flicker()
        # 作用：CRT 屏幕闪烁：窗口整体 opacity 在 0.98~1.0 之间随机微调，
        #       模拟老式 CRT 刷新感（70ms 定时器驱动）
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _flicker(self) -> None:
            import random
            # 0.98~1.0 之间微调 opacity，模拟老式 CRT 屏幕的轻微闪烁
            self._flicker_effect.setOpacity(0.98 + 0.02 * random.random())

        # ============================================================
        # 函数：_advance_noise()
        # 作用：静电噪点动画推进：更新随机种子并重绘（80ms 定时器驱动）
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _advance_noise(self) -> None:
            self._noise_seed = (self._noise_seed + 1) % 10000
            self.update()

        # ============================================================
        # 函数：paintEvent()
        # 作用：绘制终端背景（背景图片 → logo 水印 → 暗角 → 扫描线 → 噪点）
        # 参数：
        #   event QPaintEvent 绘制事件
        # 返回值：无（None）
        # ============================================================
        def paintEvent(self, event) -> None:
            super().paintEvent(event)
            painter = QPainter(self)
            try:
                self._paint_background(painter)
                self._paint_logo(painter)
                self._paint_vignette(painter)
                self._paint_scanlines(painter)
                self._paint_noise(painter)
            finally:
                painter.end()

        # ============================================================
        # 函数：_load_background()
        # 作用：加载终端背景图片 resources/terminal_back.png（原图，
        #       不处理），作为终端背景底层。文件缺失/加载失败返回 None
        # 参数：无
        # 返回值：QPixmap | None —— 背景图片
        # ============================================================
        def _load_background(self):
            """加载终端背景图片 resources/terminal_back.png（失败返回 None）。"""
            path = ROOT / "resources" / "terminal_back.png"
            if not path.exists():
                return None
            pix = QPixmap(str(path))
            return None if pix.isNull() else pix

        # ============================================================
        # 函数：_paint_background()
        # 作用：把背景图片按 cover 模式铺满整个终端窗口（保持比例，
        #       裁掉多余部分），并按窗口尺寸缓存缩放结果避免重复缩放。
        #       绘制后叠加暗化蒙版（半透明黑），保证终端文字可读
        # 参数：
        #   painter QPainter 绘制器
        # 返回值：无（None）
        # ============================================================
        def _paint_background(self, painter: QPainter) -> None:
            """把 terminal_back.png 按 cover 模式铺满终端背景，再叠加暗化蒙版。"""
            if self._bg is None:
                return
            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            if self._bg_scaled is None or self._bg_size != (w, h):
                self._bg_scaled = self._bg.scaled(
                    w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                )
                self._bg_size = (w, h)
            x = (w - self._bg_scaled.width()) // 2
            y = (h - self._bg_scaled.height()) // 2
            painter.drawPixmap(x, y, self._bg_scaled)
            # 暗化蒙版：半透明黑整幅压暗，保证终端文字可读
            painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        # ============================================================
        # 函数：_load_logo()
        # 作用：加载 amadeus 品牌 logo（amadeus-logo-TM.png）并暗化
        #       （透明度降至 0.55，保留 alpha），作为终端背景水印叠放
        #       在背景图片之上。文件缺失/加载失败返回 None
        # 参数：无
        # 返回值：QPixmap | None —— 暗化后的 logo
        # ============================================================
        def _load_logo(self):
            """加载 amadeus logo 并暗化（0.55 透明度），作为终端背景水印。"""
            path = ROOT / "amadeus-logo-TM.png"
            if not path.exists():
                return None
            src = QPixmap(str(path))
            if src.isNull():
                return None
            # 暗化：在透明底上以 0.55 透明度重绘，得到低对比水印（保留 alpha）
            dark = QPixmap(src.size())
            dark.fill(Qt.GlobalColor.transparent)
            p = QPainter(dark)
            try:
                p.setOpacity(0.55)
                p.drawPixmap(0, 0, src)
            finally:
                p.end()
            return dark

        # ============================================================
        # 函数：_paint_logo()
        # 作用：把 logo 原图绘制为终端底部背景中央的水印（输入行上方），
        #       叠放在背景图片之上
        # 参数：
        #   painter QPainter 绘制器
        # 返回值：无（None）
        # ============================================================
        def _paint_logo(self, painter: QPainter) -> None:
            """把 logo 原图绘制为终端底部背景中央的水印（输入行之上）。"""
            if self._logo is None:
                return
            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            pw = int(w * 0.42)
            ph = int(self._logo.height() * pw / self._logo.width())
            if ph > int(h * 0.30):
                ph = int(h * 0.30)
                pw = int(self._logo.width() * ph / self._logo.height())
            scaled = self._logo.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (w - scaled.width()) // 2
            # 底部背景中央：垂直中线位于窗口 84% 高度处（输入行上方留白）
            y = int(h * 0.84) - scaled.height() // 2
            painter.save()
            painter.setOpacity(0.8)
            painter.drawPixmap(x, y, scaled)
            painter.restore()

        def _paint_vignette(self, painter: QPainter) -> None:
            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            gradient = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
            gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
            gradient.setColorAt(0.55, QColor(0, 0, 0, 0))
            gradient.setColorAt(1.0, QColor(0, 0, 0, 175))
            painter.fillRect(self.rect(), gradient)

        def _paint_scanlines(self, painter: QPainter) -> None:
            painter.setPen(QColor(0, 0, 0, 70))
            for y in range(0, self.height(), 3):
                painter.drawLine(0, y, self.width(), y)

        def _paint_noise(self, painter: QPainter) -> None:
            import random
            rnd = random.Random(self._noise_seed)
            painter.setPen(QColor(193, 180, 146, 45))
            count = max(0, self.width() * self.height() // 100)
            for _ in range(count):
                painter.drawPoint(rnd.randrange(self.width()), rnd.randrange(self.height()))

        # ============================================================
        # 函数：_paint_vignette()
        # 作用：绘制暗角（radial 渐变，边缘渐黑 175 透明度），
        #       模拟 CRT 屏幕四角变暗的效果
        # 参数：
        #   painter QPainter 绘制器
        # 返回值：无（None）
        # ============================================================
        def _paint_vignette(self, painter: QPainter) -> None:
            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            gradient = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
            gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
            gradient.setColorAt(0.55, QColor(0, 0, 0, 0))
            gradient.setColorAt(1.0, QColor(0, 0, 0, 175))
            painter.fillRect(self.rect(), gradient)

        # ============================================================
        # 函数：_paint_scanlines()
        # 作用：绘制水平扫描线（每 3px 一条半透明黑线，CRT 特征效果）
        # 参数：
        #   painter QPainter 绘制器
        # 返回值：无（None）
        # ============================================================
        def _paint_scanlines(self, painter: QPainter) -> None:
            painter.setPen(QColor(0, 0, 0, 70))
            for y in range(0, self.height(), 3):
                painter.drawLine(0, y, self.width(), y)

        # ============================================================
        # 函数：_paint_noise()
        # 作用：绘制静电噪点（随机分布的半透明点，数量约为面积/100）
        # 参数：
        #   painter QPainter 绘制器
        # 返回值：无（None）
        # ============================================================
        def _paint_noise(self, painter: QPainter) -> None:
            import random
            rnd = random.Random(self._noise_seed)
            painter.setPen(QColor(193, 180, 146, 45))
            count = max(0, self.width() * self.height() // 100)
            for _ in range(count):
                painter.drawPoint(rnd.randrange(self.width()), rnd.randrange(self.height()))

        # ============================================================
        # 函数：keyPressEvent()
        # 作用：终端键盘快捷键：Ctrl+Plus/Minus=字体缩放、Ctrl+C=中断任务
        # 参数：
        #   event QKeyEvent 键盘事件
        # 返回值：无（None）
        # ============================================================
        def keyPressEvent(self, event) -> None:
            if event.modifiers() & Qt.ControlModifier:
                if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
                    self.log.zoomIn(1)
                    event.accept()
                    return
                if event.key() == Qt.Key_Minus:
                    self.log.zoomOut(1)
                    event.accept()
                    return
                if event.key() == Qt.Key_C:
                    self.interrupt_requested.emit()
                    event.accept()
                    return
            super().keyPressEvent(event)

        # ============================================================
        # 函数：eventFilter()
        # 作用：监听输入框键盘事件：Ctrl+C=中断、Up/Down=历史、Tab=补全
        # 参数：
        #   obj   QObject 事件来源对象
        #   event QEvent   事件
        # 返回值：bool —— True=事件已处理；False=继续传给父类
        # ============================================================
        def eventFilter(self, obj, event) -> bool:
            if obj is self.input and event.type() == QEvent.KeyPress:
                key = event.key()
                if event.modifiers() & Qt.ControlModifier and key == Qt.Key_C:
                    self.interrupt_requested.emit()
                    return True
                # / 命令面板可见时，方向键/Tab/回车优先导航面板，而不是翻历史/提交
                if self._slash_panel.isVisible():
                    if key == Qt.Key_Escape:
                        self._hide_slash_panel()
                        return True
                    if key == Qt.Key_Up:
                        self._slash_move(-1)
                        return True
                    if key == Qt.Key_Down:
                        self._slash_move(1)
                        return True
                    if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
                        self._slash_accept()
                        return True
                if key == Qt.Key_Up:
                    self._history_prev()
                    return True
                if key == Qt.Key_Down:
                    self._history_next()
                    return True
                if key == Qt.Key_Tab:
                    self._tab_complete()
                    return True
            return super().eventFilter(obj, event)

        # ============================================================
        # 函数：_history_prev()
        # 作用：上一条输入历史（Up 键）——倒序翻历史记录
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _history_prev(self) -> None:
            if not self._history:
                return
            if self._history_index < 0:
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            self.input.setText(self._history[self._history_index])
            self.input.setCursorPosition(len(self.input.text()))

        # ============================================================
        # 函数：_history_next()
        # 作用：下一条输入历史（Down 键）——正序翻历史，到底后清空
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _history_next(self) -> None:
            if self._history_index < 0:
                return
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
                self.input.setText(self._history[self._history_index])
            else:
                self._history_index = -1
                self.input.setText("")
            self.input.setCursorPosition(len(self.input.text()))

        # ============================================================
        # 函数：_tab_complete()
        # 作用：Tab 补全：先匹配历史命令，否则文件路径补全
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _tab_complete(self) -> None:
            parent = self.parentWidget()
            cwd = getattr(parent, "_terminal_cwd", None)
            completed = _complete_terminal_input(self.input.text(), self._history, cwd)
            if completed is None:
                return
            self.input.setText(completed)
            self.input.setCursorPosition(len(completed))

        # ============================================================
        # 函数：_refresh_slash_panel()
        # 作用：输入框文本变化时刷新 / 命令下拉面板：仅当输入以 / 开头
        #       且尚未输入参数（无空格）时，按已输入字符过滤命令并显示；
        #       否则隐藏面板。
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _refresh_slash_panel(self) -> None:
            text = self.input.text()
            if not text.startswith("/") or " " in text:
                self._hide_slash_panel()
                return
            token = text[1:].lower()
            matches = [
                (name, desc)
                for name, desc in self._slash_commands
                if name.lower().startswith(token)
            ]
            if not matches:
                self._hide_slash_panel()
                return
            self._slash_panel.clear()
            for name, desc in matches:
                item = QListWidgetItem(f"/{name}  —  {desc}")
                item.setData(Qt.UserRole, name)
                self._slash_panel.addItem(item)
            self._slash_panel.setCurrentRow(0)
            self._position_slash_panel()
            self._slash_panel.show()
            self._slash_panel.raise_()

        # ============================================================
        # 函数：_position_slash_panel()
        # 作用：把 / 命令面板定位到输入框正上方，宽度与输入框一致
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _position_slash_panel(self) -> None:
            top_left = self.input.mapTo(self, QPoint(0, 0))
            panel_w = self.input.width()
            # 每项约 26px，最多 8 项；至少给 1 项高度
            item_h = 26
            panel_h = min(self._slash_panel.count(), 8) * item_h + 4
            self._slash_panel.setGeometry(
                top_left.x(), top_left.y() - panel_h, panel_w, panel_h
            )

        def _hide_slash_panel(self) -> None:
            self._slash_panel.hide()

        # ============================================================
        # 函数：_slash_move()
        # 作用：面板可见时上下移动选中项（循环）
        # 参数：
        #   delta int +1 下移 / -1 上移
        # 返回值：无（None）
        # ============================================================
        def _slash_move(self, delta: int) -> None:
            count = self._slash_panel.count()
            if count == 0:
                return
            self._slash_panel.setCurrentRow((self._slash_panel.currentRow() + delta) % count)

        # ============================================================
        # 函数：_slash_accept()
        # 作用：确认当前选中命令：把 /命令名 填回输入框并聚焦光标到末尾
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _slash_accept(self) -> None:
            item = self._slash_panel.currentItem()
            if item is not None:
                name = item.data(Qt.UserRole)
                self.input.setText("/" + name + " ")
            self._hide_slash_panel()
            self.input.setFocus()
            self.input.setCursorPosition(len(self.input.text()))

        def _slash_item_clicked(self, item) -> None:
            """鼠标点击面板项：填入命令并隐藏面板。"""
            name = item.data(Qt.UserRole)
            self.input.setText("/" + name + " ")
            self._hide_slash_panel()
            self.input.setFocus()
            self.input.setCursorPosition(len(self.input.text()))

        # ============================================================
        # 函数：_interrupt()
        # 作用：Ctrl+C 中断当前正在运行的 harness 回合（仅 harness 模式生效）
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        # ============================================================
        # 函数：_submit()
        # 作用：提交输入：存入历史（最多 200 条）→ 发出 submitted 信号 →
        #       清空输入框。主窗口接收信号后发送给 AI。
        # 参数：无
        # 返回值：无（None）
        # ============================================================
        def _submit(self) -> None:
            text = self.input.text().strip()
            if text:
                if not self._history or self._history[-1] != text:
                    self._history.append(text)
                    if len(self._history) > 200:
                        self._history.pop(0)
                self._history_index = -1
                self.submitted.emit(text)
                self.input.clear()

        def request_approval(self, request: dict) -> None:
            """在终端内显示一个待处理的工具审批请求。"""
            if self._pending_approval is not None:
                self._resolve_approval("deny")
            payload = request.get("payload") or {}
            command = str(payload.get("command", "") or "tool")
            description = str(payload.get("description", "") or command)
            self._pending_approval = request
            self.approval_label.setText(f"⚠ {command}\n{description}\n允许执行吗？")
            self.approval_panel.show()

        def _resolve_approval(self, choice: str) -> None:
            request = self._pending_approval
            if request is None:
                return
            self._pending_approval = None
            self.approval_panel.hide()
            request["choice"] = choice
            request["event"].set()

        def closeEvent(self, event) -> None:
            self._resolve_approval("deny")
            super().closeEvent(event)

        # ============================================================
        # 函数：render_lines()
        # 作用：标记待渲染的终端行并启动 33ms 节流定时器（实际刷新在
        #       _flush_render 合并执行）。full=True=整体更换（全量重建），
        #       默认增量追加
        # 参数：
        #   lines list  终端行列表，每项 (kind, text) 或 (kind, text, extra)
        #   full  bool  是否强制全量重建（默认 False）
        # 返回值：无（None）
        # ============================================================
        def render_lines(
            self,
            lines: list,
            full: bool = False,
            dirty_from: int | None = None,
        ) -> None:
            """标记待渲染并启动节流定时器；实际刷新在 _flush_render 中合并执行。

            full=True 表示行列表被整体更换（新任务/重开终端），需全量重建；
            默认增量：只追加新行并替换流式末行。
            """
            self._lines = lines
            if full:
                self._needs_rebuild = True
            if dirty_from is not None:
                dirty_from = max(0, dirty_from)
                self._dirty_from = (
                    dirty_from
                    if self._dirty_from is None
                    else min(self._dirty_from, dirty_from)
                )
            if not self._render_timer.isActive():
                self._render_timer.start()

        def _flush_render(self) -> None:
            """从首个脏逻辑行重绘后缀，保留前缀且不丢多块 HTML。"""
            lines = self._lines
            if self._needs_rebuild or len(lines) < self._rendered_count:
                self._rebuild_all(lines)
                return
            if not lines:
                self._dirty_from = None
                return
            if self._rendered_count == 0 or not self._line_starts:
                self._rebuild_all(lines)
                return
            if self._dirty_from is not None:
                start_index = min(self._dirty_from, self._rendered_count - 1)
            elif len(lines) > self._rendered_count:
                start_index = self._rendered_count - 1
            else:
                start_index = len(lines) - 1
            self._replace_suffix(start_index)
            self._rendered_count = len(lines)
            self._dirty_from = None
            QTimer.singleShot(0, self._scroll_to_bottom)

        def _rebuild_all(self, lines: list) -> None:
            """全量重建并记录每个逻辑行的文档位置。"""
            from PySide6.QtGui import QTextCursor

            self._needs_rebuild = False
            self._rendered_count = len(lines)
            self._dirty_from = None
            self._line_starts = []
            header = "".join([
                f"<div style='color:{_TERMINAL_DIM};font-size:9px'>║▒░ amadeus shell — wired session</div>",
                f"<div style='border-top:1px solid {_TERMINAL_ROSE};margin:2px 0 6px 0'></div>",
            ])
            self.log.clear()
            cursor = QTextCursor(self.log.document())
            cursor.insertHtml(header)
            for index in range(len(lines)):
                if cursor.position() > 0:
                    cursor.insertBlock()
                self._line_starts.append(cursor.position())
                cursor.insertHtml(self._line_html(index))
            self.log.setTextCursor(cursor)
            QTimer.singleShot(0, self._scroll_to_bottom)

        def _replace_suffix(self, start_index: int) -> None:
            """删除 start_index 起的文档后缀，再按当前逻辑行重插。"""
            from PySide6.QtGui import QTextCursor

            if start_index >= len(self._line_starts):
                self._rebuild_all(self._lines)
                return
            cursor = QTextCursor(self.log.document())
            cursor.setPosition(self._line_starts[start_index])
            cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            del self._line_starts[start_index:]
            for offset, index in enumerate(range(start_index, len(self._lines))):
                if offset > 0 and cursor.position() > 0:
                    cursor.insertBlock()
                self._line_starts.append(cursor.position())
                cursor.insertHtml(self._line_html(index))
            self.log.setTextCursor(cursor)

        def _line_html(self, index: int) -> str:
            """构建第 index 行 HTML：末行不缓存（流式中间态），历史行按 key 缓存。"""
            item = self._lines[index]
            if len(item) == 3:
                kind, text, extra = item
            else:
                kind, text, extra = item[0], item[1], None
            if index == len(self._lines) - 1:
                return _build_terminal_line_html(kind, text, extra)
            key = _line_cache_key(item)
            line_html = self._line_cache.get(key)
            if line_html is None:
                line_html = _build_terminal_line_html(kind, text, extra)
                self._line_cache[key] = line_html
            return line_html

        def _scroll_to_bottom(self) -> None:
            sb = self.log.verticalScrollBar()
            sb.setValue(sb.maximum())

        def _open_external_link(self, url) -> None:
            """Ctrl+点击超链接：用系统默认浏览器打开（普通点击不响应）。"""
            if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
                return
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(url)

        def set_busy(self, busy: bool) -> None:
            # 保持输入框可接收 Ctrl+C；禁用控件会让焦点事件无法到达中断处理。
            self.input.setReadOnly(busy)

    class PetWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._frame = QImage()
            self._first_frame_received = False
            self._drag_offset: QPoint | None = None
            self._busy = False
            self._streamed_reply = ""
            self._hotkey_down = False
            self._csa_down = False
            self.terminal = None  # AgentTerminal 独立窗口，懒创建
            self._term_lines: list = []
            self._terminal_reply_index: int | None = None
            self._terminal_route_mode = "auto"
            self._terminal_cwd = ROOT
            self._active_agent_task = None
            terminal_state = load_terminal_state()
            self._terminal_session_id = terminal_state["session_id"]
            persisted_cwd = terminal_state.get("cwd")
            if persisted_cwd:
                candidate_cwd = Path(persisted_cwd).expanduser()
                if candidate_cwd.is_dir():
                    self._terminal_cwd = candidate_cwd.resolve()
            if terminal_state.get("route") in {"auto", "local", "harness"}:
                self._terminal_route_mode = terminal_state["route"]
            self._skill_manager = SkillManager()
            self._active_skills: dict[str, object] = {}
            self._zoom = 0.9
            self._pinned = False
            self._bubble_segments: list[str] = []
            self._bubble_index = 0
            # 流式期间用户是否已单击进入逐句阅读（进入后流式文字不再覆盖当前句）；
            # _stream_live = 已追平所有完成句（此后残句尾巴继续打字机直播）
            self._stream_reading = False
            self._stream_live = False
            self._bubble_timer: QTimer | None = None
            self._snap_anim: QPropertyAnimation | None = None
            self._user_pos: QPoint | None = None
            self._was_desktop = False
            self._inactivity_timer = QTimer(self)
            self._inactivity_timer.setSingleShot(True)
            self._inactivity_timer.timeout.connect(self._hide_idle_bubble)
            self._state = load_state(character.id, get_random_greeting(character.id))
            self.speech = SpeechPlayer(self)
            self.speech.speaking_changed.connect(lambda value: send_command(speaking=value))
            # 音量强度 → Live2D 口型开合（mouth_intensity 在播放线程发射，queued 回主线程）
            self.speech.mouth_intensity.connect(lambda value: send_command(mouth=value))
            # 语音服务离线：气泡序列末尾追加提示（信号在 TTS 工作线程发射，
            # queued connection 回到主线程，追加列表安全）
            self.speech.tts_offline.connect(self._notify_tts_offline)
            self.setWindowTitle("牧濑红莉栖 [PY]")
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
            # 尺寸对齐 phone_live2d_page.html 的合成画面：304×690
            # （手机本体 280×560 —— 严格 2:1 比例）
            self.setFixedSize(304, 690)

            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 20, screen.bottom() - self.height() - 60)

            self.reply_bubble = QLabel(self)
            # 气泡：手机屏幕内、Dock 栏上方（几何由 _set_bubble_text 统一计算）
            self.reply_bubble.setGeometry(28, 448, 248, 96)
            # v4：正文左对齐（富文本 line-height 1.5 由 _wrap_bubble_html 提供）
            self.reply_bubble.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.reply_bubble.setWordWrap(True)
            self.reply_bubble.setStyleSheet(
                "QLabel{background-color:#171114;background-image:url(" + _dither_texture_url() + ");"
                "color:#c1b492;"
                "border-left:1px solid #d2738a;border-right:1px solid #d2738a;"
                "border-top:1.5px solid #d2738a;border-bottom:1.5px solid #d2738a;"
                "border-radius:0px;"
                "padding:10px 16px;font:14px 'Consolas','Microsoft YaHei';"
                "font-weight:400}"
            )
            _init_msgs = active_session(self._state)["messages"]
            if _init_msgs:
                self._set_bubble_text(self._latest_line(_init_msgs[-1]["content"]))
            self.reply_bubble.hide()
            self._bubble_crt = CrtOverlay(self.reply_bubble, scanlines=True, vignette=False, noise=False)
            self._bubble_crt.raise_()

            # fauux 稿⑤⑩④：气泡头部名牌 + 底部状态注脚（随气泡 Show/Hide 联动）
            # v4：名牌/注脚改为一体标签（bg + 粗左边条，贴合气泡上下缘）
            self.bubble_header = QLabel("K U R I S U", self)
            self.bubble_header.setStyleSheet(
                "color:#d2738a;background-color:#171114;"
                "border:1px solid #d2738a;border-left:6px solid #d2738a;border-radius:0px;"
                "font-family:'Times New Roman','Times',serif;font-size:11px;font-weight:bold;"
            )
            self.bubble_header.setAlignment(Qt.AlignHCenter)
            self.bubble_header.hide()
            self.bubble_footer = QLabel("wire ESTABLISHED · Δ 0.41s · ch 1", self)
            self.bubble_footer.setStyleSheet(
                "color:#8a7f63;background-color:#171114;"
                "border:1px solid #8a7f63;border-left:6px solid #8a7f63;border-radius:0px;"
                "font-family:'Consolas','Microsoft YaHei';font-size:9px;"
            )
            self.bubble_footer.setAlignment(Qt.AlignHCenter)
            self.bubble_footer.hide()
            # v4：气泡四角括号（fauux ⌈⌉⌊⌋，随气泡显示/隐藏联动）
            self.bubble_corners = []
            for _ch in ("⌈", "⌉", "⌊", "⌋"):
                lbl = QLabel(_ch, self)
                lbl.setStyleSheet(
                    "color:#d2738a;background:transparent;"
                    "font-family:'Times New Roman','Times',serif;font-size:15px;font-weight:bold;"
                )
                lbl.hide()
                self.bubble_corners.append(lbl)
            # v4：状态行（工具进度与台词分离，dim 小字）
            self.status_line = QLabel("", self)
            self.status_line.setStyleSheet(
                "color:#8a7f63;background:transparent;"
                "font-family:'Consolas','Microsoft YaHei';font-size:9px;"
            )
            self.status_line.setAlignment(Qt.AlignHCenter)
            self.status_line.hide()
            self.reply_bubble.installEventFilter(self)

            # 输入面板（默认隐藏，点击💬展开；屏幕内底部槽位）
            panel_w = 248
            panel_x = 20 + (264 - panel_w) // 2   # 20 = 屏幕左，264 = 屏幕宽
            panel_y = 118 + 496 - 64 - 2            # 屏幕底 - 64 Dock预留 - 2margin
            self.input_panel = QWidget(self)
            self.input_panel.setGeometry(panel_x, panel_y, panel_w, 52)
            self.input_panel.setStyleSheet(
                "background-color:#171114;background-image:url(" + _dither_texture_url() + ");"
                "border-left:1px solid #d2738a;border-right:1px solid #d2738a;"
                "border-top:1.5px solid #d2738a;border-bottom:1.5px solid #d2738a;"
                "border-radius:0px"
            )
            input_layout = QHBoxLayout(self.input_panel)
            input_layout.setContentsMargins(14, 6, 6, 6)
            input_layout.setSpacing(4)
            self.input = QLineEdit()
            self.input.setPlaceholderText("和红莉栖对话…")
            self.input.setStyleSheet(
                "QLineEdit{background:transparent;color:#c1b492;border:0;padding:8px 10px;"
                "font-size:14px;font-family:'Consolas','Microsoft YaHei'}"
                "QLineEdit::placeholder{color:#8a7f63}"
            )
            self.input.returnPressed.connect(self._send)
            collapse_button = QPushButton("←")
            collapse_button.setToolTip("收起聊天")
            collapse_button.clicked.connect(self._toggle_input_panel)
            collapse_button.setFixedSize(36, 36)
            collapse_button.setStyleSheet(
                "QPushButton{background:transparent;color:#8a7f63;border:1px solid #8a7f63;border-radius:0px;"
                "font-size:16px}"
                "QPushButton:hover{background:#d2738a;color:#171114}"
            )
            input_layout.addWidget(collapse_button)
            input_layout.addWidget(self.input, 1)
            send_button = QPushButton("↑")
            send_button.setToolTip("发送")
            send_button.clicked.connect(self._send)
            send_button.setFixedSize(36, 36)
            send_button.setStyleSheet(
                "QPushButton{background:transparent;color:#c1b492;border:1px solid #c1b492;border-radius:0px;"
                "font-size:16px;font-weight:bold}"
                "QPushButton:hover{background:#d2738a;color:#171114} QPushButton:disabled{color:#8a7f63}"
            )
            input_layout.addWidget(send_button)
            self.send_button = send_button
            self.input_panel.hide()
            self._panel_crt = CrtOverlay(self.input_panel, scanlines=True, vignette=False, noise=False)
            self._panel_crt.raise_()

            # Dock 底部悬浮工具栏
            self.dock_bar = DockBar(self)
            self.dock_bar.button("对话").clicked.connect(self._toggle_input_panel)
            self.dock_bar.button("固定").clicked.connect(self._toggle_pin)
            self.dock_bar.button("设置").clicked.connect(lambda: SettingsDialog(self).exec())
            self.dock_bar.button("终端").clicked.connect(self._toggle_terminal)
            self.dock_bar.button("退出").clicked.connect(QApplication.quit)
            self.dock_bar.button("电话").clicked.connect(self._toggle_call)
            self.dock_bar.show()

            # 手机屏幕顶部状态栏（时间 + 信号，Qt 实现）
            self.status_bar = StatusBar(self)
            self.status_bar.show()
            self.status_bar.raise_()

            # 通话态视图（默认隐藏，覆盖屏幕区域）
            from ui.widgets.call_view import CallView
            self._in_call = False
            self.call_view = CallView(self)
            self.call_view.setGeometry(20, 118, 264, 496)
            self.call_view.hide()
            self.call_controller = None  # 通话时创建，避免闲置时持有 sounddevice stream

            # Dock 与输入框互斥切换的 opacity effect
            self._dock_opacity = QGraphicsOpacityEffect(self.dock_bar)
            self.dock_bar.setGraphicsEffect(self._dock_opacity)
            self._dock_opacity.setOpacity(1.0)

            self._input_opacity = QGraphicsOpacityEffect(self.input_panel)
            self.input_panel.setGraphicsEffect(self._input_opacity)
            self._input_opacity.setOpacity(0.0)
            # 收起动画挂起的 hide 定时器（防止淡出中途再次展开后被误隐藏）
            self._pending_panel_hide: QTimer | None = None

            self._relayout()

            self.tray = QSystemTrayIcon(self)
            # v4：tray 图标用项目 logo 缩略；缺失时回落红色方块（避免空图标）
            icon_pixmap = QPixmap(str(ROOT / "amadeus-logo-TM.png"))
            if icon_pixmap.isNull():
                icon_pixmap = QPixmap(24, 24)
                icon_pixmap.fill(QColor(210, 115, 138))
            else:
                icon_pixmap = icon_pixmap.scaled(
                    24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            self.tray.setIcon(QIcon(icon_pixmap))
            tray_menu = QMenu()
            restore_action = tray_menu.addAction("显示红莉栖")
            focus_action = tray_menu.addAction("聚焦输入")
            quit_action = tray_menu.addAction("退出")
            restore_action.triggered.connect(self._restore_from_tray)
            focus_action.triggered.connect(self._focus_input)
            quit_action.triggered.connect(QApplication.quit)
            self.tray.setContextMenu(tray_menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()

            # 最小化时的悬浮恢复按钮（独立窗口）
            self._restore_win = QWidget()
            self._restore_win.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self._restore_win.setAttribute(Qt.WA_TranslucentBackground, True)
            self._restore_win.setFixedSize(48, 48)
            screen = QApplication.primaryScreen().availableGeometry()
            self._restore_win.move(screen.right() - 58, screen.bottom() - 120)
            btn = QPushButton(self._restore_win)
            btn.setGeometry(0, 0, 48, 48)
            btn.setToolTip("点击打开红莉栖")
            btn.setCursor(Qt.PointingHandCursor)
            # 加载 amadeus logo 作为按钮图标（44×44，居中留 2px 边距）
            _logo_path = ROOT / "amadeus-logo-TM.png"
            if _logo_path.exists():
                _logo_pix = QPixmap(str(_logo_path)).scaled(
                    44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                btn.setIcon(QIcon(_logo_pix))
                btn.setIconSize(_logo_pix.size())
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:none;padding:0}"
                "QPushButton:hover{background:rgba(210,115,138,0.3);border-radius:6px}"
                "QPushButton:pressed{background:rgba(210,115,138,0.5);border-radius:6px}"
            )
            btn.clicked.connect(self._restore_from_tray)
            self._restore_win.hide()

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.read_frames)
            self.timer.start(16)

            self.hotkey_timer = QTimer(self)
            self.hotkey_timer.timeout.connect(self._poll_global_hotkey)
            self.hotkey_timer.start(80)

            # 全局鼠标跟踪：每 50ms 采样鼠标位置，发送到 renderer 驱动 Live2D 视线+身体
            self._pointer_timer = QTimer(self)
            self._pointer_timer.timeout.connect(self._track_pointer)
            self._pointer_timer.start(50)

            self._home_click_timer = QTimer(self)
            self._home_click_timer.setSingleShot(True)
            self._home_click_timer.setInterval(260)
            self._home_click_timer.timeout.connect(self._handle_phone_home_click)

            from PySide6.QtGui import QKeySequence, QShortcut
            # Ctrl+Space 已由 _poll_global_hotkey 的 win32api 全局轮询处理，
            # 此处不再注册 QShortcut，避免重复触发 _focus_input。
            QShortcut(QKeySequence("Escape"), self).activated.connect(
                self._hide_input_or_noop
            )

        def _focus_input(self) -> None:
            self._restore_from_tray()
            if not self.input_panel.isVisible():
                self._toggle_input_panel()
            self.input.setFocus()

        def _toggle_input_panel(self) -> None:
            """切换输入面板：Dock 淡出 + 输入框淡入，或反向。"""
            # 任意方向切换先取消挂起的 hide，避免淡出动画中途再次展开后被强制隐藏
            if self._pending_panel_hide is not None:
                self._pending_panel_hide.stop()
                self._pending_panel_hide = None
            if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                # 收起 input，恢复 dock 可点击
                self._cross_fade(self._input_opacity, self._dock_opacity)
                self._pending_panel_hide = QTimer.singleShot(200, self.input_panel.hide)
                self.dock_bar.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            else:
                # 展开 input，dock 透明时不拦截鼠标（避免误点 dock 按钮）
                self.input_panel.show()
                self.input.setFocus()
                self._cross_fade(self._dock_opacity, self._input_opacity)
                self.dock_bar.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        def _cross_fade(self, fade_out_effect, fade_in_effect) -> None:
            """200ms opacity 交叉淡入淡出。"""
            anim_out = QPropertyAnimation(fade_out_effect, b"opacity", self)
            anim_out.setDuration(200)
            anim_out.setStartValue(fade_out_effect.opacity())
            anim_out.setEndValue(0.0)
            anim_out.setEasingCurve(QEasingCurve.OutCubic)
            anim_out.start()
            self._fade_out_anim = anim_out

            anim_in = QPropertyAnimation(fade_in_effect, b"opacity", self)
            anim_in.setDuration(200)
            anim_in.setStartValue(fade_in_effect.opacity())
            anim_in.setEndValue(1.0)
            anim_in.setEasingCurve(QEasingCurve.OutCubic)
            anim_in.start()
            self._fade_in_anim = anim_in

        def _toggle_pin(self) -> None:
            """固定/解锁位置。固定后禁用拖拽和自动贴合。v4：DockButton 固定态视觉反馈。"""
            self._pinned = not self._pinned
            self.dock_bar.button("固定").set_pinned(self._pinned)

        def _set_bubble_text(self, text: str) -> None:
            """设置回复气泡文字并自动缩放大小（支持长文本内部滚动）。

            v4：正文走富文本（_wrap_bubble_html：1.5 行距 + 左对齐），
            尺寸用 QTextDocument 估算（QLabel 无法用 QFontMetrics 测行距）。
            名牌/注脚/四角括号/状态行随气泡几何同步。
            """
            bubble_html = _wrap_bubble_html(text)
            self.reply_bubble.setText(bubble_html)
            # 手机屏幕区：x=20 w=264；Dock 顶 y = 118+496-64 = 550
            screen_x, screen_w = 20, 264
            dock_top = 118 + 496 - 64
            w, h = _bubble_size_hint(bubble_html, self.reply_bubble.font(), screen_w - 16)
            w = min(max(w, 80), screen_w - 16)
            h = min(max(h, 36), 100)
            x = screen_x + (screen_w - w) // 2
            y = dock_top - h - 6
            self.reply_bubble.setGeometry(x, y, w, h)
            # 头部名牌/底部注脚/四角括号/状态行跟随气泡几何（fauux 稿⑤⑩④）。
            # 初始化早期调用本方法时配件尚未创建，传 None 由 _sync_bubble_accessories
            # 跳过同步；构建完成后正常跟随。
            _sync_bubble_accessories(
                getattr(self, 'bubble_header', None),
                getattr(self, 'bubble_footer', None),
                getattr(self, 'bubble_corners', None),
                getattr(self, 'status_line', None),
                x, y, w, h,
            )

        def _show_thinking_dots(self) -> None:
            """delta 期间显示等待叙事（fauux 启动序列行 + 多语言短语轮换），气泡呼吸。"""
            # v4：状态行在思考期间隐藏（工具进度由状态行表达，台词区回到 thinking）
            if hasattr(self, 'status_line'):
                self.status_line.hide()
            self._set_bubble_text("> linking fork.db\n… ok")
            # 底部注脚切为相位行（④ ║▒░♫ 与等待叙事同源）
            if getattr(self, 'bubble_footer', None):
                self.bubble_footer.setText("║▒░ ♫ ░▒║ · synchronizing")
            if not hasattr(self, '_bubble_opacity'):
                self._bubble_opacity = QGraphicsOpacityEffect(self.reply_bubble)
                self.reply_bubble.setGraphicsEffect(self._bubble_opacity)
            if not hasattr(self, '_thinking_anim'):
                self._thinking_anim = QPropertyAnimation(self._bubble_opacity, b"opacity", self)
                self._thinking_anim.setDuration(1000)
                self._thinking_anim.setStartValue(0.4)
                self._thinking_anim.setKeyValueAt(0.5, 1.0)
                self._thinking_anim.setEndValue(0.4)
                self._thinking_anim.setLoopCount(-1)
            if self._thinking_anim.state() != QPropertyAnimation.Running:
                self._thinking_anim.start()
            # 多语言短语轮换（fauux [make me sad] 交互）：1.2s 一轮
            if not hasattr(self, '_think_phrase_timer'):
                self._think_phrase_timer = QTimer(self)
                self._think_phrase_timer.setInterval(1200)
                self._think_phrase_timer.timeout.connect(self._cycle_think_phrase)
            self._think_phrase_timer.start()

        def _cycle_think_phrase(self) -> None:
            """轮换 thinking 气泡第二行的状态短语。"""
            phrases = ("[synchronizing mind]", "[思维同步中]", "[心を同期中]", "[make me sad]")
            self._think_phrase_index = getattr(self, '_think_phrase_index', 0)
            self._think_phrase_index = (self._think_phrase_index + 1) % len(phrases)
            if not self.reply_bubble.isVisible():
                return
            self.reply_bubble.setText(_wrap_bubble_html(
                f"> linking fork.db\n… ok　{phrases[self._think_phrase_index]}"
            ))

        def _send_emotion(self, emotion: str) -> None:
            """经 duplex 管道发送 emotion 到 renderer（即时，不等 LLM）。"""
            send_command(emotion=emotion)

        # ===== 通话态切换 =====
        def _toggle_call(self) -> None:
            """Dock 电话按钮：非通话态→进入通话，通话态→挂断。"""
            decision = _decide_call_toggle_action(self._in_call)
            if decision["enter_call"]:
                self._enter_call()
            elif decision["hangup"]:
                self._hangup_call()

        def _enter_call(self) -> None:
            """进入通话态：隐藏平时组件，显示 CallView，启动管线。"""
            config = {**PHONE_DEFAULTS, **load_config()}
            if not all(config.get(key) for key in ("endpoint", "api_key", "model")):
                SettingsDialog(self).exec()
                return
            self._in_call = True
            # 通话独占音频：立即停掉桌面 TTS（避免两个 SpeechPlayer 抢输出流，
            # 以及桌面播报声被通话 VAD 误拾成用户说话）
            try:
                self.speech.stop()
            except Exception:
                pass
            self.reply_bubble.hide()
            self.input_panel.hide()
            self.dock_bar.hide()
            self.call_view.show()
            self.call_view.raise_()
            self.call_view.setFocus()  # 确保 CallView 能接收键盘事件（Escape 退出）
            # 断开旧 controller 信号（避免重复连接）
            if self.call_controller is not None:
                try:
                    self.call_controller.phase_changed.disconnect()
                    self.call_controller.subtitle.disconnect()
                    self.call_controller.elapsed.disconnect()
                    self.call_controller.waveform.disconnect()
                    self.call_controller.you_said.disconnect()
                    self.call_controller.error.disconnect()
                    self.call_controller.muted_changed.disconnect()
                    self.call_controller.screen_share_changed.disconnect()
                    self.call_controller.mouth_intensity.disconnect()
                except Exception:
                    pass
            # 重新创建 controller 以用最新 config
            from core.voice_call import VoiceCallController
            self.call_controller = VoiceCallController(config, character, self)
            self.call_controller.phase_changed.connect(self._on_call_phase_changed)
            self.call_controller.subtitle.connect(self.call_view.set_subtitle)
            self.call_controller.elapsed.connect(self.call_view.set_elapsed)
            self.call_controller.waveform.connect(self.call_view.set_waveform)
            self.call_controller.you_said.connect(self._on_call_you_said)
            self.call_controller.error.connect(self._on_call_error)
            self.call_controller.muted_changed.connect(self.call_view.set_muted)
            self.call_controller.screen_share_changed.connect(self.call_view.set_screen_share)
            self.call_controller.mouth_intensity.connect(lambda value: send_command(mouth=value))
            # 断开 CallView 旧信号再重连（防止重复连接导致 hangup 被多次调用）
            try:
                self.call_view.mute_clicked.disconnect()
                self.call_view.hangup_clicked.disconnect()
                self.call_view.screen_clicked.disconnect()
            except Exception:
                pass
            self.call_view.mute_clicked.connect(self.call_controller.toggle_mute)
            self.call_view.hangup_clicked.connect(self._hangup_call)
            self.call_view.screen_clicked.connect(self.call_controller.toggle_screen_share)
            self.call_view.set_muted(self.call_controller.is_muted)
            self.call_view.set_screen_share(self.call_controller.screen_share_on)
            self.call_controller.start()

        def _hangup_call(self) -> None:
            """挂断：停管线，恢复平时态。即使 controller 为 None 也能恢复 UI。"""
            if not self._in_call:
                return
            self._in_call = False
            if self.call_controller is not None:
                try:
                    self.call_controller.hangup()
                except Exception:
                    pass
                try:
                    self.call_controller.phase_changed.disconnect()
                    self.call_controller.subtitle.disconnect()
                    self.call_controller.elapsed.disconnect()
                    self.call_controller.waveform.disconnect()
                    self.call_controller.you_said.disconnect()
                    self.call_controller.error.disconnect()
                    self.call_controller.muted_changed.disconnect()
                    self.call_controller.screen_share_changed.disconnect()
                    self.call_controller.mouth_intensity.disconnect()
                except Exception:
                    pass
                self.call_controller = None
            self.call_view.hide()
            self.dock_bar.show()
            self.reply_bubble.show()
            # 恢复对话气泡
            try:
                msgs = active_session(self._state)["messages"]
                if msgs:
                    self._set_bubble_text(self._latest_line(msgs[-1]["content"]))
            except Exception:
                pass

        def _on_call_phase_changed(self, phase: str) -> None:
            self.call_view.set_phase(phase)
            # Live2D 表情随状态切换
            emotion_map = {
                "listening": "neutral",
                "processing": "thinking",
                "speaking": "smile",
                "ended": "neutral",
            }
            emotion = emotion_map.get(phase)
            if emotion:
                self._send_emotion(emotion)
            # speaking 态驱动 Live2D 口型
            if phase == "speaking":
                send_command(speaking=True)
            elif phase in ("listening", "ended"):
                send_command(speaking=False)

        def _on_call_you_said(self, text: str) -> None:
            """通话中识别到的用户话语：常驻显示在 CallView 小字标签。"""
            try:
                self.call_view.set_you_said(text)
            except Exception:
                pass

        def _on_call_error(self, text: str) -> None:
            self.call_view.set_subtitle(f"⚠ {text}")

        def _stop_thinking_anim(self) -> None:
            """停止思考呼吸动画与短语轮换，并恢复气泡不透明度（finished/failed 共用）。"""
            if hasattr(self, '_think_phrase_timer') and self._think_phrase_timer.isActive():
                self._think_phrase_timer.stop()
            if hasattr(self, 'bubble_footer'):
                self.bubble_footer.setText("wire ESTABLISHED · Δ 0.41s · ch 1")
            if hasattr(self, '_thinking_anim') and self._thinking_anim.state() == QPropertyAnimation.Running:
                self._thinking_anim.stop()
                if hasattr(self, '_bubble_opacity'):
                    self._bubble_opacity.setOpacity(1.0)

        def _show_layered_bubbles(self, text: str) -> None:
            """将回复分层后分多个气泡前后展示，每段用 opacity 动画淡入。"""
            self._cancel_bubbles()
            # 停止思考呼吸动画，恢复 opacity
            self._stop_thinking_anim()
            self._thinking_dots_shown = False
            # v4：正式回复到达，状态行隐藏（台词与状态分离）
            if hasattr(self, 'status_line'):
                self.status_line.hide()
            merged = _final_bubble_segments(text)
            if not merged:
                return
            self._bubble_segments = merged
            self._bubble_index = 0
            self.reply_bubble.show()
            self._show_next_bubble()

        def _show_next_bubble(self) -> None:
            """显示下一个气泡分段，用 opacity 动画淡入。"""
            if self._bubble_index >= len(self._bubble_segments):
                self._schedule_bubble_hide()
                return
            text = self._bubble_segments[self._bubble_index]
            self._set_bubble_text(text)
            # opacity 淡入动画
            if not hasattr(self, '_bubble_opacity'):
                self._bubble_opacity = QGraphicsOpacityEffect(self.reply_bubble)
                self.reply_bubble.setGraphicsEffect(self._bubble_opacity)
            self._bubble_opacity.setOpacity(0.0)
            fade_in = QPropertyAnimation(self._bubble_opacity, b"opacity", self)
            fade_in.setDuration(180)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.OutCubic)
            fade_in.start()
            self._fade_anim = fade_in  # 保持引用防 GC
            self.reply_bubble.show()
            self._bubble_index += 1
            # 全部段展示完后，9 秒无操作再隐藏气泡
            if self._bubble_index >= len(self._bubble_segments):
                self._schedule_bubble_hide()

        def _schedule_bubble_hide(self) -> None:
            """9 秒无操作隐藏气泡。用可取消的 QTimer 实例：
            QTimer.singleShot 静态版无法引用取消，流式期间用户可能仍在
            逐句阅读，新分段到达时旧定时器必须能被 _cancel_bubbles 停掉。"""
            if self._bubble_timer is not None:
                self._bubble_timer.stop()
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._hide_idle_bubble)
            timer.start(9000)
            self._bubble_timer = timer

        def _cancel_bubbles(self) -> None:
            """取消正在进行的气泡序列。"""
            if self._bubble_timer is not None:
                self._bubble_timer.stop()
                self._bubble_timer = None
            self._bubble_segments = []
            self._bubble_index = 0

        def _notify_tts_offline(self) -> None:
            """语音服务离线提示：在当前气泡序列末尾追加一条，不打断展示。"""
            notice = "（语音服务离线）"
            segments = getattr(self, "_bubble_segments", [])
            if notice in segments:
                return
            self._bubble_segments = segments + [notice]
            # 原序列已展示完毕但气泡仍可见时，继续展示追加段（否则提示永不出现）
            if self._bubble_index >= len(self._bubble_segments) - 1 and self.reply_bubble.isVisible():
                self._bubble_index = len(self._bubble_segments) - 1
                self._show_next_bubble()

        def _animate_to(self, target: QPoint) -> None:
            """平滑滑动到目标位置（300ms OutCubic 缓动）。"""
            if self._snap_anim is not None:
                self._snap_anim.stop()
            self._snap_anim = QPropertyAnimation(self, b"pos")
            self._snap_anim.setDuration(300)
            self._snap_anim.setStartValue(self.pos())
            self._snap_anim.setEndValue(target)
            self._snap_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._snap_anim.start()

        def _restore_from_tray(self) -> None:
            self._restore_win.hide()
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()

        def _minimize_to_tray(self) -> None:
            self.hide()
            self._restore_win.show()

        def _handle_phone_home_click(self) -> None:
            self._minimize_to_tray()

        @staticmethod
        def _phone_menu_rect() -> QRect:
            return QRect(10, 10, 30, 30)

        @staticmethod
        def _phone_home_rect() -> QRect:
            return QRect(134, 621, 36, 36)

        def _tray_activated(self, reason) -> None:
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
                self._restore_from_tray()

        @staticmethod
        def _latest_line(text: str) -> str:
            """提取气泡显示文本：完整 chinese 部分（多段 === 时合并所有中文段）。

            修复：原版只取最后 3 行 + 105 字截断，长回复被截断。
            现改为返回完整 chinese（去掉 [emotion:] 标签，保留所有中文段，多段用空行分隔）。
            气泡 _set_bubble_text 高度自适应 + 内部滚动支持长文本。
            """
            chinese = parse_reply(text).chinese
            return chinese if chinese.strip() else "…"

        def _terminal_session_lines(self) -> list:
            """当前会话消息 → 终端行（cmd=用户 / out=kurisu 中文）。"""
            lines = []
            for message in active_session(self._state)["messages"]:
                if message["role"] == "assistant":
                    chinese = parse_reply(message["content"]).chinese
                    if chinese.strip():
                        lines.append(("out", chinese))
                else:
                    lines.append(("cmd", message["content"]))
            return lines

        def _position_terminal(self) -> None:
            """终端固定在主窗口左侧，随主窗口移动，不能主动拖动。"""
            if self.terminal is None or not self.terminal.isVisible():
                return
            gap = -24  # 负值：终端右移约一个 dock 图标宽度（32px），贴近角色
            x = self.x() - self.terminal.width() - gap
            y = self.y() + (self.height() - self.terminal.height()) // 2
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(x, screen.left())
            y = max(y, screen.top())
            self.terminal.move(x, y)

        def moveEvent(self, event) -> None:
            super().moveEvent(event)
            self._position_terminal()

        def _terminal_active(self) -> bool:
            return self.terminal is not None and self.terminal.isVisible()

        def _toggle_terminal(self) -> None:
            """Dock 终端按钮：打开/关闭独立 CRT 终端窗口。"""
            if self._terminal_active():
                self.terminal.close()
                return
            if self.terminal is None:
                self.terminal = AgentTerminal(self)
                self.terminal.submitted.connect(self._terminal_send)
                self.terminal.interrupt_requested.connect(self._interrupt_agent)
                self.terminal._history = list(load_terminal_state().get("history") or [])
            # 每次打开都从当前会话重建终端行，避免只加载一次导致
            # 之后主页面新增的聊天记录不显示在终端（历史抽屉是每次重读的）。
            self._term_lines = self._terminal_session_lines()
            self._terminal_reply_index = None
            self.terminal.render_lines(self._term_lines, full=True)
            self.terminal.show()
            self._position_terminal()
            self.terminal.raise_()
            self.terminal.activateWindow()
            self.terminal.input.setFocus()

        def _terminal_command_context(self) -> TerminalCommandContext:
            def list_skills() -> list[tuple[str, str, str]]:
                return [
                    (info.name, info.description, info.source)
                    for info in self._skill_manager.discover().values()
                ]

            def enable_skill(name: str) -> tuple[bool, str]:
                try:
                    skill = self._skill_manager.load(name)
                except KeyError:
                    return False, f"skill not found: {name}"
                except (OSError, UnicodeError, ValueError) as exc:
                    return False, f"skill load failed: {exc}"
                self._active_skills[skill.info.name] = skill
                return True, f"skill enabled: {skill.info.name}"

            def clear_skills() -> None:
                self._active_skills.clear()

            def new_session() -> None:
                create_session(self._state, get_random_greeting(character.id))
                save_state(character.id, self._state)
                self._terminal_session_id = f"amadeus-terminal-{uuid.uuid4().hex}"
                self._term_lines = self._terminal_session_lines()
                self._terminal_reply_index = None

            history = self.terminal._history if self.terminal is not None else []
            return TerminalCommandContext(
                route_mode=self._terminal_route_mode,
                cwd=self._terminal_cwd,
                history=history,
                active_skills=list(self._active_skills.keys()),
                list_skills=list_skills,
                enable_skill=enable_skill,
                clear_skills=clear_skills,
                new_session=new_session,
            )

        def _handle_terminal_command(self, text: str) -> str | None:
            result = terminal_command_registry.dispatch(text, self._terminal_command_context())
            if not result.handled:
                return text
            if result.route_mode is not None:
                self._terminal_route_mode = result.route_mode
            if result.cwd is not None:
                self._terminal_cwd = result.cwd
            save_terminal_state(
                history=self.terminal._history if self.terminal is not None else [],
                route=self._terminal_route_mode,
                cwd=self._terminal_cwd,
                session_id=self._terminal_session_id,
            )
            if result.clear:
                self._term_lines = []
                self._terminal_reply_index = None
            self._term_lines.extend(result.lines)
            if self.terminal is not None:
                self.terminal.render_lines(self._term_lines, full=result.clear)
            return result.forward_text

        def _terminal_backend_ready(self) -> bool:
            """Check only the credentials needed by the selected terminal route."""
            config = load_config()
            local_ready = all(config.get(key) for key in ("endpoint", "api_key", "model"))
            if self._terminal_route_mode == "local":
                return local_ready
            harness = dict((config.get("agent_router") or {}).get("harness") or {})
            harness.update(config.get("harness") or {})
            harness_ready = bool(
                (harness.get("api_key") or config.get("api_key"))
                and (harness.get("model") or config.get("model") or "deepseek-v4-flash")
            )
            return local_ready or harness_ready

        def _interrupt_agent(self) -> None:
            task = self._active_agent_task
            if task is None or not self._busy:
                if self.terminal is not None:
                    self.terminal._lines.append(("sys", "^C — 当前没有正在运行的任务"))
                    self.terminal.render_lines(self.terminal._lines)
                return
            task.cancel()
            try:
                from core.harness_bridge import cancel_active_run
                cancel_active_run()
            except Exception:
                pass
            if self.terminal is not None:
                self.terminal._lines.append(("sys", "^C — 已请求中断当前任务"))
                self.terminal.render_lines(self.terminal._lines)

        def _terminal_route_override(self) -> str:
            if self._terminal_route_mode == "local":
                return "local"
            if self._terminal_route_mode == "harness":
                return "harness"
            return "terminal_auto"

        def _terminal_skill_prompt(self) -> str:
            return build_skill_prompt(list(self._active_skills.values()))

        def _terminal_send(self, text: str) -> None:
            """终端提交：复用气泡发送管线（不显示气泡，输出回显到终端）。"""
            if self._busy:
                return
            forwarded = self._handle_terminal_command(text)
            if forwarded is None:
                return
            text = forwarded
            save_terminal_state(
                history=self.terminal._history if self.terminal is not None else [],
                route=self._terminal_route_mode,
                cwd=self._terminal_cwd,
                session_id=self._terminal_session_id,
            )
            if not self._terminal_backend_ready():
                SettingsDialog(self).exec()
                return
            self._term_lines.append(("cmd", text))
            self._term_lines.append(("out", "▌"))
            self._terminal_reply_index = len(self._term_lines) - 1
            self.terminal.render_lines(self._term_lines)
            self._send_text(
                text,
                show_bubble=False,
                route_override=self._terminal_route_override(),
                response_max_tokens=None,
                inject_system_prompt=self._terminal_skill_prompt(),
                terminal_cwd=str(self._terminal_cwd),
                terminal_session_id=self._terminal_session_id,
            )

        def _hide_input_or_noop(self) -> None:
            """Escape 键：收起输入面板（若已展开），Dock 淡入恢复。"""
            if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                self._toggle_input_panel()

        def _poll_global_hotkey(self) -> None:
            try:
                import win32api
            except ImportError:
                return  # 非 Windows 平台，跳过全局热键
            # Ctrl+Space → 聚焦输入
            cs_pressed = bool(win32api.GetAsyncKeyState(0x11) & 0x8000) and bool(win32api.GetAsyncKeyState(0x20) & 0x8000)
            if cs_pressed and not self._hotkey_down:
                self._focus_input()
            self._hotkey_down = cs_pressed
            # Ctrl+Alt+S → 切换显示/隐藏
            cas_pressed = (
                bool(win32api.GetAsyncKeyState(0x11) & 0x8000)
                and bool(win32api.GetAsyncKeyState(0x12) & 0x8000)
                and bool(win32api.GetAsyncKeyState(0x53) & 0x8000)
            )
            if cas_pressed and not self._csa_down:
                self._toggle_visibility()
            self._csa_down = cas_pressed

        def _toggle_visibility(self) -> None:
            """Ctrl+Alt+S 切换桌宠显示/隐藏。"""
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()
                self.activateWindow()

        def _track_pointer(self) -> None:
            """全局鼠标跟踪：采样光标位置，归一化后发送到 renderer 驱动 Live2D 视线+身体。

            归一化逻辑：以窗口中心为原点，水平 ±250px / 垂直 ±200px 映射到 [-1, 1]，
            clamp 后发送。鼠标在窗口左侧 → pointerX = -1（角色看左），右侧 → +1。
            光标静止时跳过发送（管道去重）：renderer 收不到 pointer 更新即视为
            无鼠标活动，作为闲置微动作计时器的复位信号（live2d_page.html setPointer）。
            """
            try:
                cursor = QCursor.pos()
                center = self.geometry().center()
                dx = (cursor.x() - center.x()) / 250.0
                dy = (cursor.y() - center.y()) / 200.0
                px = max(-1.0, min(1.0, dx))
                py = max(-1.0, min(1.0, dy))
                last = getattr(self, "_last_pointer", None)
                if last is not None and abs(px - last[0]) < 1e-3 and abs(py - last[1]) < 1e-3:
                    return
                self._last_pointer = (px, py)
                send_command(pointer=(px, py))
            except Exception:
                pass

        def _send(self) -> None:
            text = self.input.text().strip()
            if not text or self._busy:
                return
            self._send_text(text)

        def _send_text(
            self,
            text: str,
            show_bubble: bool = True,
            route_override: str | None = None,
            response_max_tokens: int | None = 700,
            inject_system_prompt: str | None = None,
            terminal_cwd: str | None = None,
        ) -> None:
            """发送核心：气泡与终端共用（终端模式不显示气泡，回显走终端）。"""
            config = load_config()
            if route_override == "harness" or route_override == "terminal_auto":
                config_ready = self._terminal_backend_ready()
            else:
                config_ready = all(config.get(key) for key in ("endpoint", "api_key", "model"))
            if not config_ready:
                SettingsDialog(self).exec()
                return
            self.input.clear()
            self._cancel_bubbles()
            if show_bubble:
                self.reply_bubble.show()
            session = active_session(self._state)
            add_message(session, "user", text)
            instant = _decide_send_instant_action()
            if show_bubble:
                self._show_thinking_dots()
            self._send_emotion(instant["emotion"])
            self._busy = True
            if self.terminal is not None:
                self.terminal.set_busy(True)
            self._streamed_reply = ""
            # 流式 TTS 状态：_stream_sep_count 记录已遇到的 === 数（奇数=日语段，偶数=中文段）
            # _stream_tts_started 标记是否已启动 speak_streaming_start（避免重复启动）
            # 修复 LLM 输出多段 === 交替（中文===日语===中文===日语）时把中文段也送 TTS 的 bug
            self._stream_sep_count = 0
            self._stream_tts_started = False
            # 流式气泡上屏节流状态（60ms 一帧）；_stream_reading 标记用户是否
            # 已在流式期间单击进入逐句阅读（进入后流式文字不再覆盖当前句），
            # _stream_live 标记是否已点完所有完成句（追平后尾巴继续打字机）
            self._stream_reading = False
            self._stream_live = False
            self._stream_paint_ts = 0.0
            self._stream_paint_text = ""
            self.send_button.setDisabled(True)
            history = [{"role": message["role"], "content": message["content"]} for message in session["messages"][-14:]]
            if route_override is None:
                route_override = "local"
            if response_max_tokens == 700:
                router_cfg = config.get("agent_router") or {}
                try:
                    response_max_tokens = int(router_cfg.get("chat_max_tokens", 700))
                except (TypeError, ValueError):
                    response_max_tokens = 700
            task = AgentTask(
                history,
                session.get("memories", []),
                route_override=route_override,
                response_max_tokens=response_max_tokens,
                inject_system_prompt=inject_system_prompt,
                terminal_cwd=terminal_cwd,
            )
            self._active_agent_task = task
            task.signals.status.connect(self._show_status)
            task.signals.delta.connect(self._agent_delta)
            task.signals.finished.connect(self._agent_finished)
            task.signals.cancelled.connect(self._agent_cancelled)
            task.signals.failed.connect(self._agent_failed)
            task.signals.tool_event.connect(self._agent_tool_event)
            task.signals.confirmation.connect(self._confirm_operation)
            QThreadPool.globalInstance().start(task)

        def _agent_cancelled(self, partial_reply: str) -> None:
            self._busy = False
            self._streamed_reply = ""
            self._active_agent_task = None
            self._stop_thinking_anim()
            if self.terminal is not None:
                self.terminal.set_busy(False)
                if (
                    self._terminal_reply_index is not None
                    and self._terminal_reply_index < len(self._term_lines)
                ):
                    self._term_lines[self._terminal_reply_index] = ("sys", "^C — 任务已中断")
                    self.terminal.render_lines(self._term_lines, dirty_from=self._terminal_reply_index)
            self._terminal_reply_index = None
            self.send_button.setDisabled(False)

        def _confirm_operation(self, request: dict) -> None:
            if self._terminal_active():
                self._term_lines.append(("sys", "等待工具审批…"))
                self.terminal.render_lines(self._term_lines)
                self.terminal.request_approval(request)
                return
            payload = request["payload"]
            command = payload.get("command", "")
            description = payload.get("description", "")
            detail_text = description or command
            if not detail_text:
                detail_text = __import__("json").dumps(payload, ensure_ascii=False, indent=2)
            details = html.escape(detail_text)
            msg = QMessageBox(self)
            msg.setWindowTitle("红莉栖请求操作电脑")
            msg.setIcon(QMessageBox.Question)
            msg.setText(f"她要执行：\n{details}\n\n允许吗？")
            btn_once = msg.addButton("仅本次", QMessageBox.AcceptRole)
            btn_session = msg.addButton("本次会话", QMessageBox.AcceptRole)
            btn_always = msg.addButton("始终允许", QMessageBox.AcceptRole)
            btn_deny = msg.addButton("拒绝", QMessageBox.RejectRole)
            msg.setDefaultButton(btn_deny)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == btn_once:
                request["choice"] = "once"
            elif clicked == btn_session:
                request["choice"] = "session"
            elif clicked == btn_always:
                request["choice"] = "always"
            else:
                request["choice"] = "deny"
            request["event"].set()

        def _agent_tool_event(self, event: dict) -> None:
            if not self._terminal_active():
                return
            kind = event.get("kind")
            name = event.get("name", "tool")
            if kind == "tool_call":
                args = event.get("arguments") or {}
                if name == "str_replace_editor":
                    command = str(args.get("command", ""))
                    self._term_lines.append(("tool", f"{name} {command} {args.get('path', '')}".rstrip()))
                elif name in ("bash", "pwsh", "run_bash", "run_command"):
                    cmd = args.get("command") or args.get("cmd") or ""
                    self._term_lines.append(("tool", f"{name} $ {cmd}" if cmd else name))
                else:
                    self._term_lines.append(("tool", f"{name} {_tool_args_summary(args)}".rstrip()))
            elif kind == "tool_result":
                content = str(event.get("content", "") or "")
                is_error = bool(event.get("isError", False))
                args = event.get("arguments") or {}
                command = str(args.get("command", ""))
                if is_error:
                    self._term_lines.append(("err", content or f"{name} 执行失败"))
                elif name == "str_replace_editor" and command in ("create", "str_replace", "insert"):
                    self._term_lines.append(("diff", "", _editor_diff_extra(args)))
                elif content:
                    self._term_lines.append(("result", content))
                else:
                    self._term_lines.append(("tool", f"✓ {name}"))
            self.terminal.render_lines(self._term_lines)

        def _show_status(self, text: str) -> None:
            # v4：状态与台词分离 —— 工具进度走独立 dim 状态行，不覆盖台词气泡
            if not self._terminal_active():
                if hasattr(self, 'status_line'):
                    self.status_line.setText("▸ " + text)
                    self.status_line.show()

        def _agent_delta(self, text: str) -> None:
            new_streamed, should_show_thinking, should_set_bubble_text = _decide_delta_action(
                self._streamed_reply, text, self._terminal_active()
            )
            self._streamed_reply = new_streamed
            if (
                self._terminal_active()
                and self._terminal_reply_index is not None
                and self._terminal_reply_index < len(self._term_lines)
            ):
                # 终端模式：流式中文实时回显到最后一行 out
                chinese = parse_reply(self._streamed_reply).chinese
                self._term_lines[self._terminal_reply_index] = ("out", chinese if chinese.strip() else "▌")
                self.terminal.render_lines(self._term_lines, dirty_from=self._terminal_reply_index)
            if should_set_bubble_text:
                # 流式气泡：首字即上屏，不等 finished（本地直连低延迟体感优化）。
                # 停掉思考呼吸动画与 1.2s 短语轮换，防止轮换把流式文字覆盖掉。
                self._stop_thinking_anim()
                display = _streamed_display_text(self._streamed_reply)
                # 增量分句：句末标点一到位即成为可单击的分段，不必等整条回复
                # 结束（TTS 逐句播放时第一段声音未完也能推进阅读）
                self._bubble_segments, tail = _split_stream_segments(display)
                now = time.monotonic()
                if not getattr(self, "_stream_reading", False):
                    # 未进入逐句阅读：整段打字机预览（原有行为）
                    paint_text = display
                elif getattr(self, "_stream_live", False) or self._bubble_index >= len(self._bubble_segments):
                    # 已追平全部完成句：只打字机当前残句（新句完成后仍保持直播）
                    paint_text = tail
                    self._stream_live = True
                else:
                    # 用户停在已完成的句子上：保持不动，等下一次单击推进
                    paint_text = None
                # 本地模型 delta 可达数百次/秒，节流到 ~16fps 防止布局风暴；
                # 尾帧由 _agent_finished 的完整文本兜底，丢帧无碍
                if paint_text and paint_text != getattr(self, "_stream_paint_text", None) and (
                    now - getattr(self, "_stream_paint_ts", 0.0) >= 0.06
                ):
                    self._stream_paint_ts = now
                    self._stream_paint_text = paint_text
                    self._set_bubble_text(paint_text + "▌")
            if should_show_thinking:
                self._show_thinking_dots()
            # 流式 TTS：纯 === 切段 + 日语字符过滤（修复多段 === + emotion 错位 bug）
            # LLM 实际输出格式（dist/data/sessions.json 实测）：
            #   中文1
            #   ===
            #   日语1 \n\n [emotion:neutral]中文2     ← [emotion] 在 === 后，与日语1 同段
            #   ===
            #   日语2 \n\n [emotion:neutral]中文3
            #   ===
            #   日语3 \n\n 中文4（无 emotion）
            #   ===
            #   日语4
            # 用 === 切所有段，对每段用 has_japanese() 判断：
            #   - 含假名（平假名/片假名）→ 日语段，提取假名部分送 TTS
            #   - 纯汉字/无假名 → 中文段，跳过
            # 数学本质：日语必有假名（U+3040-309F 平假名 + U+30A0-30FF 片假名），
            # CJK 汉字中日韩共用手写汉字（U+4E00-9FFF）无法区分，但日语段必含假名。
            # 形象理解：每段用 === 切开后扫假名，有假名的是日语段送 TTS。
            # 通话中桌面 TTS 静默：声音统一归通话管线（ctrl._tts），避免两个
            # SpeechPlayer 同时开输出流互抢 + 声音回流被通话 VAD 误拾
            if getattr(self, "_in_call", False):
                return
            config = load_config()
            if not config.get("tts_enabled", True):
                return
            if not text:
                return
            # 把 delta 按 === 切分：parts[0] 是当前段尾部，parts[1:] 是新切段开头
            parts = text.split("===")
            if len(parts) == 1:
                # 无新 ===：当前段增量追加（按假名判断是否送 TTS）
                self._append_tts_segment_by_japanese(parts[0])
            else:
                # 有新 ===：先处理当前段尾部，再逐个切段
                self._append_tts_segment_by_japanese(parts[0])
                for i in range(1, len(parts)):
                    seg = parts[i].lstrip("=\r\n")
                    self._append_tts_segment_by_japanese(seg)

        def _append_tts_segment_by_japanese(self, text: str) -> None:
            """按假名判断是否追加 TTS：含假名的是日语段，提取假名段送 TTS。

            LLM 把日语 + 中文混在同一段（如「日语1 \\n\\n [emotion:neutral]中文2」），
            用策略：先按 [emotion:xxx] 标签切分（去掉标签），再按空白行切段，
            对每段用 has_japanese() 判断含假名则送 TTS，跳过纯中文段。
            """
            if not text:
                return
            import re
            # 去掉 [emotion:xxx] 标签
            cleaned = re.sub(r"\[emotion:[^\]]+\]", "", text)
            # 按空白行（\\n\\n 或 \\n）切段
            chunks = re.split(r"\n\s*\n", cleaned)
            for chunk in chunks:
                segment = chunk.strip()
                if not segment:
                    continue
                # 必须含至少 1 个假名才算日语段
                if not re.search(r"[\u3040-\u309F\u30A0-\u30FF]", segment):
                    continue
                # 首次进入日语段时启动流式 TTS 会话
                if not self._stream_tts_started:
                    config = load_config()
                    self.speech.set_rate([-2, 0, 2][config.get("tts_rate", 1)])
                    self.speech.speak_streaming_start(text_lang="ja")
                    self._stream_tts_started = True
                self.speech.speak_streaming_append(segment)

        def _agent_finished(self, reply: str) -> None:
            session = active_session(self._state)
            add_message(session, "assistant", reply)
            parsed = parse_reply(reply)
            self._busy = False
            self._active_agent_task = None
            self._streamed_reply = ""
            self.send_button.setDisabled(False)
            if self.terminal is not None:
                self.terminal.set_busy(False)
            if (
                self._terminal_active()
                and self._terminal_reply_index is not None
                and self._terminal_reply_index < len(self._term_lines)
            ):
                self._term_lines[self._terminal_reply_index] = ("out", parsed.chinese)
                self.terminal.render_lines(self._term_lines, dirty_from=self._terminal_reply_index)
            self._terminal_reply_index = None
            # 响应提速：TTS 收尾 / 本地 Ollama 表情分类（最长阻塞 3s）/ 会话写盘
            # 全部后移，不再拖慢首屏文字
            if not self._terminal_active():
                # 分层气泡需要完整中文做分段展示，_latest_line 的 105 字截断会丢段
                if getattr(self, "_stream_reading", False) and self._bubble_index > 0:
                    # 流式期间用户已在逐句阅读：同步最终分段但保留当前进度，
                    # 不回卷第一句重播（回卷会让已读过的句子再看一遍）
                    self._bubble_segments = _final_bubble_segments(parsed.chinese)
                    if getattr(self, "_stream_live", False) or self._bubble_index >= len(self._bubble_segments):
                        # 已追平：补显最后一段（流式尾巴此刻刚好完结成句）
                        self._show_next_bubble()
                    # 否则停在当前句，单击继续推进
                else:
                    # 流式期间未点击阅读：整条回复分层展示，从第一句开始
                    self._show_layered_bubbles(parsed.chinese)
                self._stream_reading = False
                self._stream_live = False
            print(f"[PET-DBG] finished reply_len={len(reply)} jp_len={len(parsed.japanese)} tts_started={self._stream_tts_started} sep_count={self._stream_sep_count}")
            print(f"[PET-DBG] finished reply={reply[:200]!r}")
            print(f"[PET-DBG] finished parsed.jp={parsed.japanese[:200]!r}")
            # 流式 TTS：会话结束，刷新剩余缓冲
            # （流式合成在 _agent_delta 中已开始，这里只刷新剩余文本）
            # 通话中桌面 TTS 静默：正在播的停掉、兜底合成跳过（声音归通话管线）
            config = load_config()
            in_call = getattr(self, "_in_call", False)
            if in_call:
                self._stream_tts_started = False
                self.speech.stop()
            elif config.get("tts_enabled", True) and self._stream_tts_started:
                self.speech.speak_streaming_end()
            elif config.get("tts_enabled", True) and parsed.japanese and not self._stream_tts_started:
                # 兜底：如果流式未启动（如无 === 分隔符），整段合成
                print(f"[PET-DBG] 兜底整段合成 jp={parsed.japanese[:60]!r}")
                self.speech.set_rate([-2, 0, 2][config.get("tts_rate", 1)])
                self.speech.speak_with_options(
                    parsed.japanese,
                    text_lang="ja",
                    allow_fallback=False,
                )
            self._stream_sep_count = 0
            self._stream_tts_started = False
            # 表情/动作综合判定：本地 Ollama 小模型分类（emotion+motion），
            # 失败回退规则解析；任何异常回退 parse_reply 的 emotion（表现层不影响主流程）。
            # 放在气泡显示之后执行——分类请求最长阻塞 3s，不应拖慢文字首屏
            try:
                from core.companion.expression import decide_expression
                _cfg = load_config()
                _expr = decide_expression(
                    reply, ollama=(_cfg.get("agent_router") or {}).get("ollama")
                )
                emotion, motion = _expr.emotion, _expr.motion
            except Exception:
                emotion, motion = parsed.emotion, "neutral"
            send_command(emotion=emotion, motion=motion)
            # 会话持久化后移（写盘不阻塞首屏）
            save_state(character.id, self._state)

        def _hide_idle_bubble(self) -> None:
            if not self._busy:
                self.reply_bubble.hide()

        def _agent_failed(self, error: str) -> None:
            self._busy = False
            self._active_agent_task = None
            self._streamed_reply = ""
            self._stream_reading = False
            self._stream_live = False
            # 停止思考呼吸动画，避免失败后气泡永远显示 "● ● ●" 呼吸
            self._stop_thinking_anim()
            if self.terminal is not None:
                self.terminal.set_busy(False)
            if (
                self._terminal_active()
                and self._terminal_reply_index is not None
                and self._terminal_reply_index < len(self._term_lines)
            ):
                self._term_lines[self._terminal_reply_index] = ("err", f"任务失败：{error}")
                self.terminal.render_lines(self._term_lines, dirty_from=self._terminal_reply_index)
            else:
                if hasattr(self, 'status_line'):
                    self.status_line.hide()
                self.reply_bubble.show()
                self._set_bubble_text(self._latest_line(f"任务失败：{error}"))
                # 失败提示也会闲置后隐藏，避免永久悬挂
                self._schedule_bubble_hide()
            self._terminal_reply_index = None
            self.send_button.setDisabled(False)
            send_command(emotion="angry")

        def read_frames(self) -> None:
            latest = None
            while connection.poll():
                kind, payload = connection.recv()
                if kind == "frame":
                    latest = payload
                elif kind == "error":
                    print(f"[Amadeus] {payload}", file=sys.stderr)
                    _write_runtime_log("renderer-error.log", str(payload))
                    if hasattr(self, 'status_line'):
                        self.status_line.hide()
                    self.reply_bubble.show()
                    self._set_bubble_text(
                        "Live2D 渲染启动失败。\n"
                        "请安装 Microsoft Edge WebView2 Runtime，"
                        "或把 data/logs/renderer-crash.log 发给开发者。"
                    )
                elif kind == "home_click":
                    # JS 端 Home 键点击 → 最小化到托盘（保留托盘）
                    self._minimize_to_tray()
                elif kind == "hide_window":
                    # JS 端 Home 键双击 → 隐藏整个窗口，保留托盘
                    self.hide()
                    self._restore_win.show()
                elif kind == "close":
                    # JS 端 × 菜单 → 主动退出（等同于托盘菜单的退出）
                    QApplication.instance().quit()
            if latest is not None:
                image = QImage.fromData(latest, "PNG")
                if not image.isNull():
                    self._frame = image
                    if not self._first_frame_received:
                        self._first_frame_received = True
                        try:
                            (ROOT / "data").mkdir(parents=True, exist_ok=True)
                            image.save(str(ROOT / "data" / "received-frame.png"), "PNG")
                            READY_FILE.parent.mkdir(parents=True, exist_ok=True)
                            READY_FILE.write_text("KURISU_READY", encoding="ascii")
                        except OSError:
                            pass  # 快照/就绪标记仅调试用途，frozen 只读环境失败不影响主流程
                    self.update()
            elif (
                not self._first_frame_received
                and not renderer.is_alive()
                and not getattr(self, "_renderer_exit_reported", False)
            ):
                self._renderer_exit_reported = True
                _write_runtime_log(
                    "renderer-error.log",
                    f"Renderer exited before first frame. exitcode={renderer.exitcode}",
                )
                if hasattr(self, 'status_line'):
                    self.status_line.hide()
                self.reply_bubble.show()
                self._set_bubble_text(
                    "Live2D 渲染进程提前退出。\n"
                    "请安装 Microsoft Edge WebView2 Runtime，"
                    "或查看 data/logs/renderer-error.log。"
                )

        def paintEvent(self, event) -> None:
            if self._frame.isNull():
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            # 合成帧尺寸 = 304×690（手机壳 + Live2D 一体，PyWeb 端已画完）
            # 直接按 1:1 贴到窗口，保持透明背景
            target = QRect(0, 0, self.width(), self.height())
            if self._frame.width() == self.width() and self._frame.height() == self.height():
                painter.drawImage(target, self._frame)
            else:
                scaled = self._frame.scaled(
                    self.width(), self.height(),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                painter.drawImage(
                    (self.width() - scaled.width()) // 2,
                    (self.height() - scaled.height()) // 2,
                    scaled,
                )

        def wheelEvent(self, event) -> None:
            if self._pinned:
                return
            delta = event.angleDelta().y() / 120
            new_zoom = self._zoom + delta * 0.1
            self._zoom = max(0.5, min(2.0, new_zoom))
            self.update()

        def eventFilter(self, obj, event) -> bool:
            # 气泡的头部名牌/底部注脚/四角括号/状态行跟随气泡显示状态（fauux 稿⑤⑩④）
            if obj is self.reply_bubble and event.type() in (QEvent.Show, QEvent.Hide):
                vis = event.type() == QEvent.Show
                if hasattr(self, 'bubble_header'):
                    self.bubble_header.setVisible(vis)
                    self.bubble_footer.setVisible(vis)
                    for lbl in self.bubble_corners:
                        lbl.setVisible(vis)
                    if not vis:
                        self.status_line.hide()
            return super().eventFilter(obj, event)

        def _relayout(self) -> None:
            """根据手机屏幕布局重新定位所有组件。
            手机屏幕区域（与 phone_live2d_page.html 对齐）：
              手机框  x=12 y=110 w=280 h=560 （2:1 比例）
              屏幕    x=20 y=118 w=264 h=496 （内缩 8px，底部留 56px Home 键）
            """
            # Dock：屏幕内底部槽位（居中）——角色合成帧已留 64px Dock 预留，不重叠
            dock_w = self.dock_bar.sizeHint().width()
            dock_w = min(dock_w, 250)
            dock_x = 20 + (264 - dock_w) // 2
            dock_y = 118 + 496 - 64
            self.dock_bar.setGeometry(dock_x, dock_y, dock_w, 56)
            # 输入面板：同位互斥
            panel_w = 248
            panel_x = 20 + (264 - panel_w) // 2
            panel_y = dock_y + 2
            self.input_panel.setGeometry(panel_x, panel_y, panel_w, 52)
            # CRT overlay 跟随 input_panel
            if hasattr(self, '_panel_crt') and self._panel_crt is not None:
                self._panel_crt.setGeometry(self.input_panel.rect())
            # 手机屏幕顶部状态栏：屏幕区顶部（y=118，高 26px）
            self.status_bar.setGeometry(20, 118, 264, 26)
            self.status_bar.raise_()
            # 历史抽屉：屏幕内右侧（保持 __init__ 初始位置）
            # 通话视图：覆盖整个屏幕区域
            self.call_view.setGeometry(20, 118, 264, 496)

        def resizeEvent(self, event) -> None:
            self._relayout()
            super().resizeEvent(event)

        def keyPressEvent(self, event: QKeyEvent) -> None:
            if event.key() == Qt.Key_Escape and self._in_call:
                self._hangup_call()
                return
            super().keyPressEvent(event)

        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton:
                pos = event.position().toPoint()
                if self._phone_menu_rect().contains(pos):
                    QApplication.instance().quit()
                    event.accept()
                    return
                if self._phone_home_rect().contains(pos):
                    self._home_click_timer.start()
                    event.accept()
                    return
                # 气泡还有剩余分段时，左键点击推进下一句。
                # 必须放在"收起输入面板"之前：发完消息面板仍展开，若先收面板，
                # 用户看下一句的第一击会被吃掉（表现为"点了没反应"）。
                # 流式期间即可点击（分段随句末标点增量生成，不等整条回复）
                if self._bubble_segments and self._bubble_index < len(self._bubble_segments):
                    self._stream_reading = True
                    self._show_next_bubble()
                    # 点完若已到队尾 = 追平，后续流式走"尾巴打字机"直播
                    self._stream_live = (self._bubble_index >= len(self._bubble_segments))
                    return
                # input 可见时点 PetWindow 空白区收起 input，不触发拖拽/聚焦
                if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
                    self._toggle_input_panel()
                    return
                if event.position().x() > 50:
                    self._focus_input()
                if not self._pinned:
                    self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            elif event.button() == Qt.RightButton:
                menu = QMenu(self)
                quit_action = menu.addAction("退出桌宠")
                if menu.exec(event.globalPosition().toPoint()) is quit_action:
                    QApplication.quit()

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_offset)

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            if self._drag_offset is not None:
                self._user_pos = self.pos()  # 记住用户拖拽后的位置
                self._was_desktop = True
            self._drag_offset = None

        def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton and self._phone_home_rect().contains(event.position().toPoint()):
                self._home_click_timer.stop()
                self.hide()
                self._restore_win.show()
                event.accept()
                return

    app = QApplication(sys.argv)
    app.setApplicationName("Amadeus Kurisu")
    _ensure_dither_texture()  # fauux 抖动纹理（幂等，缺失时生成）
    pet = PetWindow()
    app.aboutToQuit.connect(lambda: renderer.terminate() if renderer.is_alive() else None)
    pet.show()
    pet.raise_()

    # === Companion 主动问候子系统 ===
    # 在 PetWindow 实例化后接入（pet 实例已可用，pet._agent_delta / pet._show_status
    # 是 PetWindow 的方法，可复用为 companion 回复的流式表达回调）。
    # PySide6 延迟导入（此处已在 run_overlay 内部，但显式导入让依赖清晰）。
    from core.companion.controller import CompanionController
    from core.companion.sensors import (
        ActiveWindowSensor, ActivityTracker, IdleStateTracker,
        ClipboardSensor, ScreenSensor, build_snapshot,
    )
    from core.companion import storage as companion_storage
    from datetime import datetime as _dt_datetime

    _companion_cfg_full = load_config()
    companion_cfg = {**{"enabled": True, "frequency": "mid", "daily_limit": 30,
                        "quiet_hours": {"start": "23:00", "end": "08:00"},
                        "sensors": {"active_window": True, "activity": True,
                                    "idle": True, "clipboard": False, "screen": False}},
                     **(_companion_cfg_full.get("companion") or {})}
    sensors_cfg = companion_cfg.get("sensors", {})

    aw_sensor = ActiveWindowSensor(interval_seconds=2)
    at_sensor = ActivityTracker(interval_seconds=30)
    it_tracker = IdleStateTracker()
    clip_sensor = ClipboardSensor(interval_seconds=1, enabled=bool(sensors_cfg.get("clipboard", False)))
    screen_sensor = ScreenSensor(enabled=bool(sensors_cfg.get("screen", False)))

    companion_ctrl = CompanionController(
        config=companion_cfg,
        llm_config={
            "endpoint": _companion_cfg_full.get("endpoint", ""),
            "api_key": _companion_cfg_full.get("api_key", ""),
            "model": _companion_cfg_full.get("model", ""),
        },
    )

    def _companion_on_delta(text: str) -> None:
        """companion 回复流式 delta，复用 PetWindow._agent_delta。"""
        try:
            pet._agent_delta(text)
        except Exception:
            pass

    def _companion_on_status(text: str) -> None:
        """companion 状态文本，复用 PetWindow._show_status。"""
        try:
            pet._show_status(text)
        except Exception:
            pass

    # companion 回复的 Live2D 表现指令缓存：controller 在 on_finished 前回调
    last_expression = None

    def _companion_on_expression(expr) -> None:
        """companion 回复 → Live2D 表情/动作（本地小模型 + 规则综合判定）。"""
        nonlocal last_expression
        last_expression = expr
        try:
            if expr.motion:
                send_command(emotion=expr.emotion, motion=expr.motion)
        except Exception:
            pass

    def _companion_on_finished(reply: str) -> None:
        """companion 完整回复落到桌宠气泡，不写入聊天历史。

        C-01 修复：companion 内部情绪（idle/sleepy/concern/tease）通过
        COMPANION_TO_LIVE2D_EMOTION 映射为 Live2D 可识别的情绪标签。
        C-02 修复：同时发送 motion 命令驱动 Live2D 动作（歪头/点头/前倾等），
        让角色不只变表情，还有身体动作，更像真人。
        """
        try:
            from core.companion.prompts import COMPANION_TO_LIVE2D_EMOTION, COMPANION_EMOTION_MOTION
            parsed = parse_reply(reply)
            # 优先用 controller 的 on_expression 结果（本地小模型+规则综合判定），
            # 未注入时回退到 companion 内部情绪映射（C-01/C-02 逻辑不变）
            if last_expression is not None:
                live2d_emotion = last_expression.emotion
                motion = last_expression.motion or COMPANION_EMOTION_MOTION.get(parsed.emotion, "neutral")
            else:
                live2d_emotion = COMPANION_TO_LIVE2D_EMOTION.get(parsed.emotion, "neutral")
                motion = COMPANION_EMOTION_MOTION.get(parsed.emotion, "neutral")
            send_command(emotion=live2d_emotion, motion=motion)
            pet._streamed_reply = ""
            pet._stream_tts_started = False
            pet._show_layered_bubbles(parsed.chinese)
        except Exception:
            pass

    def _companion_last_topic() -> str | None:
        """取最近 2 小时内最新的话题，避免重复问候同一话题（C-08）。"""
        try:
            topics = companion_storage.recent_topics(hours=2)
            return next(iter(topics)) if topics else None
        except Exception:
            return None

    def _companion_tick() -> None:
        """周期性检查 companion 触发（每 30s 一次）。"""
        if not companion_cfg.get("enabled"):
            return
        # 通话中不主动问候：companion 走桌面 SpeechPlayer 播 TTS，会与通话
        # 管线抢输出流、其声音还会被通话 VAD 当成用户说话拾进去
        if getattr(pet, "_in_call", False):
            return
        try:
            aw_sensor._poll()
            at_sensor._poll()
            it_tracker.update(at_sensor.idle_seconds)
            now = _dt_datetime.now()
            local_time = now.strftime("%H:%M 周%w")
            is_deep_night = 23 <= now.hour or now.hour < 6
            snap = build_snapshot(
                active_window=aw_sensor, activity=at_sensor, idle=it_tracker,
                clipboard=clip_sensor, screen=screen_sensor,
                last_greeting_ts=companion_storage.last_greeting_ts(),
                last_topic=_companion_last_topic(),  # C-08 修复：从 storage 取最近话题
                greeting_count=companion_storage.greeting_count_today(),
                local_time=local_time, is_deep_night=is_deep_night,
            )
            companion_ctrl.handle_signal(
                snap, local_hour=now.hour + now.minute / 60,
                on_delta=_companion_on_delta, on_status=_companion_on_status,
                on_finished=_companion_on_finished,
                on_expression=_companion_on_expression,
            )
        except Exception:
            pass  # companion 永不影响主流程

    companion_timer = QTimer(pet)
    companion_timer.timeout.connect(_companion_tick)
    companion_timer.start(30000)  # 30s 周期

    # 启动各传感器独立 QTimer（parent=pet 保证随 pet 销毁）
    aw_sensor.start(parent=pet)
    at_sensor.start(parent=pet)
    clip_sensor.start(parent=pet)

    # === IM 消息接入（docs/PRD-im-message-notify.md M1：QQ / OneBot 11）===
    # IMManager 后台线程收消息，Qt signal 队列投递回主线程弹通知；
    # 通知通道（气泡/托盘）取自 im.notify 配置，免打扰时段由 filter 兜底。
    from core.im.manager import IMManager
    im_manager = IMManager(parent=pet)
    _im_state_text = {"connecting": "连接中", "connected": "已连接",
                      "disconnected": "连接断开（自动重连中）", "error": "连接错误"}

    def _on_im_message(msg) -> None:
        try:
            notify_cfg = (im_manager.config.get("notify") or {})
            if notify_cfg.get("tray", True):
                pet.tray.showMessage("Amadeus 消息", msg.display())
            # 通话中不弹气泡（分层气泡会盖在 CallView 上干扰通话界面）
            if getattr(pet, "_in_call", False):
                return
            if notify_cfg.get("bubble", True):
                pet._show_layered_bubbles(msg.display())
        except Exception:
            pass  # IM 通知永不影响主流程

    def _on_im_status(state: str, detail: str) -> None:
        try:
            pet.tray.showMessage("IM 接入", _im_state_text.get(state, state))
        except Exception:
            pass

    im_manager.message_notified.connect(_on_im_message)
    im_manager.status_changed.connect(_on_im_status)
    im_manager.start()


    # 用户发消息时更新 companion 冷却时间戳（包装原 _send）
    _original_pet_send = pet._send

    def _send_with_companion_cooldown(*args, **kwargs):
        companion_ctrl.on_user_message()
        return _original_pet_send(*args, **kwargs)

    pet._send = _send_with_companion_cooldown
    if os.environ.get("AMADEUS_UI_SNAPSHOT"):
        def save_snapshot() -> None:
            if os.environ.get("AMADEUS_UI_SNAPSHOT") == "bubble":
                pet._set_bubble_text("我已经理解你的任务。接下来会先检查当前桌面状态，\n再执行必要操作，并把最终结果告诉你。")
                pet.reply_bubble.show()
            pet.grab().save(str(ROOT / "data" / "ui-snapshot.png"), "PNG")
            app.quit()
        QTimer.singleShot(7000, save_snapshot)
    return app.exec()


def main() -> int:
    mp.freeze_support()
    if not acquire_single_instance("Amadeus2026.DesktopPet"):
        return 0
    _write_runtime_log(
        "desktop-pet-entry.log",
        f"executable={sys.executable}\nargv={sys.argv!r}\n"
        f"frozen={getattr(sys, 'frozen', False)}\n",
    )

    def start_voice_service() -> None:
        try:
            maybe_start_gpt_sovits()
        except Exception:
            _write_runtime_log("tts-autostart-crash.log", traceback.format_exc())

    # 语音服务探测/模型启动可能访问网络或加载数十秒，不能阻塞桌宠首屏。
    threading.Thread(target=start_voice_service, daemon=True).start()
    try:
        READY_FILE.unlink(missing_ok=True)
        parent_connection, child_connection = mp.Pipe(duplex=True)
        renderer = mp.Process(target=renderer_process, args=(child_connection,), daemon=True)
        renderer.start()
        _write_runtime_log(
            "desktop-pet-startup.log",
            f"executable={sys.executable}\nargv={sys.argv!r}\n"
            f"frozen={getattr(sys, 'frozen', False)}\nrenderer_pid={renderer.pid}\n",
        )
    except Exception:
        _write_runtime_log("desktop-pet-crash.log", traceback.format_exc())
        raise
    try:
        return run_overlay(parent_connection, renderer)
    finally:
        if renderer.is_alive():
            renderer.terminate()
        renderer.join(timeout=2)
        # 清理 GPT-SoVITS 本地子进程 + SSH 隧道（避免孤儿进程占显存/端口）
        try:
            stop_gpt_sovits()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
