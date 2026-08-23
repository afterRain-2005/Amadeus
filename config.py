"""角色配置（从原 amadeus/src/lib/characters.ts 迁移）。

单一真相源：所有角色相关资源（Live2D 路径、人设、问候语、音色样本）集中定义在此。
新增角色只需在 CHARACTERS 列表里加一项，无需改其他代码。

资源路径约定：
- /xxx 形式表示 resources/ 目录下的相对路径（移植自原项目 /public 路径）
- 通过 resources_path() 工具函数转为绝对路径
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import random


# === 资源根目录（amadeus-py/resources/） ===
RESOURCES_DIR = Path(__file__).resolve().parent / "resources"


# ============================================================
# 函数：resources_path()
# 作用：把项目内的 "/xxx" 形式相对路径转换为 resources/ 下的绝对路径。
#       例："/live2d/kurisu/amadeusV1.model3.json"
#           → D:\...\amadeus-py\resources\live2d\kurisu\amadeusV1.model3.json
# 参数：
#   relative_path str 以 / 开头的资源路径
# 返回值：Path —— resources/ 目录下的绝对路径
# ============================================================
def resources_path(relative_path: str) -> Path:
    """把原项目中的 /xxx 路径转为本地 resources/ 下的绝对路径。

    例："/live2d/kurisu/amadeusV1.model3.json" → resources/live2d/kurisu/amadeusV1.model3.json
    """
    p = relative_path.lstrip("/")
    return RESOURCES_DIR / p


# === Hermes Agent 后端默认配置 ===
# amadeus-py 通过子进程拉起 `hermes -p <profile> gateway`，默认监听 127.0.0.1:8642。
# SOUL.md 位于 ~/.hermes/profiles/<profile>/SOUL.md，作为 system prompt 第一槽位承载角色人设。
# 客户端发送的 instructions 字段会叠加到 SOUL.md 上（不替换），因此 amadeus-py 只发输出格式指令。
# 保留 KURISU_PERSONALITY 等常量作为 fallback：Hermes 未启用时旧路径仍可读取，setup 脚本也从此处抽取人设写入 SOUL.md。
HERMES_DEFAULTS: dict[str, object] = {
    "enabled": False,                       # 是否启用 Hermes 后端（False 时回退到旧 OpenAI 兼容直连路径）
    "base_url": "http://127.0.0.1:8642",    # Hermes gateway 默认端口（不是 8310）
    "profile": "kurisu",                    # Hermes profile 名，对应 ~/.hermes/profiles/kurisu/
    "session_id": "amadeus-kurisu",         # X-Hermes-Session-Id + body.session_id，用于会话连续性
    "api_key": "",                          # 必须与 profile 的 .env 中 API_SERVER_KEY 一致
}


# === OpenClaw 后端默认配置 ===
# OpenClaw 是 Node.js 个人 AI 助理平台（npm install -g openclaw），其 Gateway 暴露 OpenAI 兼容 HTTP API。
# amadeus-py 以两条路径接入（客户端实现在 core/openclaw_client.py）：
# 1. CUA 工具：operate_gui 通过 POST /v1/chat/completions 把 GUI 任务委托给 OpenClaw 代理
#    （默认 openclaw/default），代理自动启用 CUA skill（如 TuriX-CUA）操作真实桌面（鼠标/键盘）。
# 2. 对话后端：agent_router.mode = "openclaw" 时整轮对话委托 OpenClaw 代理处理（流式回传）。
# 网关生命周期：autostart 开启时探活失败自动 Popen("openclaw gateway") 拉起（同 Hermes 惯例）。
# 前提：用户需先部署 OpenClaw（Node ≥ 22.22.3 + npm install -g openclaw + openclaw onboard + 装 CUA skill）。
# 官方文档：https://docs.openclaw.ai/gateway  仓库：https://github.com/openclaw/openclaw
OPENCLAW_DEFAULTS: dict[str, object] = {
    "enabled": False,                          # 是否启用 OpenClaw CUA 后端（False 时 operate_gui 返回降级提示）
    "base_url": "http://127.0.0.1:18789",      # OpenClaw Gateway 默认端口（仅回环）
    "token": "",                               # OPENCLAW_GATEWAY_TOKEN（shared-secret 鉴权，onboard 时生成）
    "model": "openclaw/default",               # 稳定代理别名，始终映射到配置的默认代理
    "timeout": 120,                            # GUI 操作可能耗时，给足超时（秒）
    "autostart": True,                         # 网关离线时自动拉起（openclaw gateway 子进程）
}

# === Agent 模式路由默认配置（2026-08-15 agent-mode spec §4.4）===
# mode: "chat"=本地直连(现状) | "harness"=DeepSeek Harness SDK | "hermes"=旧 Hermes 网关 | "deepseek"=DeepSeek 直连 | "codex"=codex 子进程 | "auto"=gate 分流
# 运行时被 data/config.json 的 agent_router 键覆盖（{**DEFAULTS, **config["agent_router"]}）。
AGENT_ROUTER_DEFAULTS: dict[str, object] = {
    "mode": "chat",
    "chat_max_tokens": 700,
    # 自动分流（独立开关，优先于 mode）：auto_targets 为勾选参与分流的模式；ollama 为本地小模型配置
    "auto_route": False,
    "auto_targets": ["local", "harness"],
    "ollama": {
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen2.5:0.5b",
        "timeout": 30,
    },
    "codex": {
        "workspace": "data/codex_workspace",   # AGENTS.md 与 codex 会话工作根目录（相对项目根）
        "sandbox": "read-only",                # codex 沙箱：read-only | workspace-write
        "timeout": 120,                        # codex exec 单轮超时（秒）
    },
    "deepseek": {
        "base_url": "http://127.0.0.1:8642",
        "api_key": "",
        "model": "deepseek-v3.1",
    },
    "harness": {
        "base_url": "",
        "api_key": "",
        "model": "deepseek-v4-flash",
        "provider": "deepseek-official",
        "runtime_bin": "",
    },
}

# === Hermes 长期记忆默认配置（多模式共有）===
# 所有对话模式（chat/gui/harness/hermes/deepseek/codex）共享同一份 SQLite 记忆库
# （data/memory.db），每轮：recall 召回相关记忆注入 prompt → remember_turn 提取事实/回合摘要写回。
# 运行时被 data/config.json 的 memory 键覆盖（{**MEMORY_DEFAULTS, **config["memory"]}）。
# 语义检索（airi Memory Alaya 风格）：memory.semantic 配 OpenAI 兼容 /embeddings；
# endpoint/api_key 留空回退顶层 endpoint/api_key；请求失败自动降级关键词匹配。
MEMORY_DEFAULTS: dict[str, object] = {
    "enabled": True,               # 总开关：False 时不做 recall / remember_turn
    "scope": "global",             # 记忆作用域：global（全局共享）或指定角色/会话 id
    "recall_limit": 8,             # 每轮召回注入 prompt 的最大记忆条数
    "semantic": {                  # 语义检索（core/memory.py SEMANTIC_DEFAULTS）
        "enabled": True,
        "endpoint": "",            # 留空 = 用顶层 endpoint
        "api_key": "",             # 留空 = 用顶层 api_key
        "model": "text-embedding-v4",
    },
}

# === DeepSeek Harness 完整配置默认值 ===
# 这组配置只在 agent_router.mode = "harness" 时使用；平常聊天和 Companion 仍走本地 agent。
HARNESS_DEFAULTS: dict[str, object] = {
    "provider": "deepseek-official",
    "model": "deepseek-v4-flash",
    "base_url": "",
    "api_key": "",
    "runtime_bin": "",
    "cordis": "",
    "cwd": "",
    "session_root": "",
    "request_timeout_seconds": 300.0,
    # sandbox_mode 对应 harness dsh-sandbox-policy 的 mode：
    #   read-only | workspace-write | danger-full-access
    "sandbox_mode": "workspace-write",
    # approval_policy 对应 harness dsh-user-approval 的 policy：
    #   ask（有 answerer 时询问，无则 fail-closed） | never（一律自动拒绝）
    "approval_policy": "ask",
    "enable_web": True,
    "enable_plan_mode": True,
    "enable_workflow": True,
    "enable_editor": True,
    "enable_subagent_fork": True,
    "enable_sandbox": True,
    "enable_commands": True,
    "enable_terminal": False,
}

# === Companion 主动问候默认配置（2026-08-16 companion-proactive-greeting spec §8）===
# amadeus-py 的 companion 子系统：伪春菜式主动陪伴，检测用户活动并吐槽/关心。
# 5 个传感器逐项开关；剪贴板/屏幕默认关（隐私边界，产品化设计 §6）。
COMPANION_DEFAULTS: dict[str, object] = {
    "enabled": True,                            # 总开关
    "sensors": {
        "active_window": True,                  # 前台窗口检测（2s 轮询，低隐私）
        "activity": True,                        # 工作节奏检测（30s 轮询，低隐私）
        "idle": True,                            # 空闲状态检测（派生自 activity）
        "clipboard": False,                     # 剪贴板检测（默认关，中隐私）
        "screen": False,                        # 屏幕感知（默认关，高隐私，成本高）
    },
    "quiet_hours": {"start": "23:00", "end": "08:00"},  # 静音时段
    "frequency": "mid",                         # low=20% / mid=50% / high=100% 触发概率
    "daily_limit": 30,                          # 每日问候上限
    "presence": {
        "enabled": True,
        "focus_minutes": 25,
        "deep_focus_minutes": 45,
        "persist_state": False,
    },
    "proactive": {
        "global_cooldown_seconds": 600,
        "user_dialogue_cooldown_seconds": 300,
        "topic_cooldowns": {
            "focus_break": 3600,
            "idle_check": 1800,
            "deep_night": 7200,
            "window_change": 900,
            "idle": 1800,
            "away_long": 3600,
            "sleepy": 7200,
            "concern": 3600,
            "tease": 900,
        },
        "interrupt_budget": {
            "soft_per_day": 8,
            "hard_per_day": 20,
        },
    },
}


# === IM 消息接入默认配置（docs/PRD-im-message-notify.md）===
# QQ 走 NapCat 等 OneBot 11 实现（正向 WS）；微信走 wcferry（M2，暂未实现）。
# 通知通道：桌宠气泡 / 托盘气泡；TTS 播报默认关（外放隐私，M3 再做）。
# quiet_hours 与 Companion 语义一致：时段内只缓冲不通知。
IM_DEFAULTS: dict[str, object] = {
    "qq": {
        "enabled": False,                       # 总开关：关 = 完全不连接
        "ws_url": "ws://127.0.0.1:3001",        # NapCat 正向 WS 端口
        "group_at_only": True,                  # 群消息默认只通知 @我
        "keywords": [],                         # 群消息关键词白名单（命中也通知）
    },
    "notify": {
        "bubble": True,                         # 桌宠头顶气泡
        "tray": True,                           # 托盘系统通知（兜底）
        "tts": False,                           # TTS 播报（默认关，隐私）
    },
    "quiet_hours": {"start": "23:00", "end": "08:00"},  # 免打扰（只缓冲不通知）
}


# === 电话模式默认配置 ===
# 电话模式 = 与红莉栖 AI 半双工语音通话 + 屏幕共享给 AI 看（豆包语音电话模式）。
# 语音管线：VAD(RMS阈值) → 回合制 STT(小米mimo) → DeepSeek 流式 LLM → TTS(红莉栖音色)
# 屏幕共享：mss 定时截帧缓存 + 开口时附帧给视觉模型 → 描述注入 LLM user 消息
# 视觉模型用 GPT-4o（DeepSeek 无视觉能力）；未配 key 时屏幕共享自动降级关闭。
PHONE_DEFAULTS: dict[str, object] = {
    "vision_endpoint": "",                              # OpenAI 兼容视觉端点（留空则用对话 endpoint）
    "vision_api_key": "",                               # 视觉模型 key（留空时屏幕共享降级关闭）
    "vision_model": "gpt-4o",                           # 视觉理解模型（DeepSeek 无视觉，必须 GPT-4o 级）
    "gpt_sovits_url": "http://127.0.0.1:9880",          # GPT-SoVITS api_v2.py 默认端口
    "screen_share_default": True,                       # 进入通话时默认开屏幕共享
    "capture_interval_ms": 2500,                        # mss 截帧间隔（2.5s 一次，仅缓存最新帧）
    "asr_endpoint": "https://api.xiaomimimo.com/v1",    # 小米 mimo ASR 端点（OpenAI 兼容 /chat/completions + input_audio）
    "asr_api_key": "",                                  # 小米 mimo ASR key（独立于对话 key）
    "asr_model": "mimo-audio-v1",                       # ASR 模型：音频理解模型，配合 input_audio 多模态格式
}

# === 聊天屏幕感知默认配置（core/screen_context.py，对标 airi "see your screen"）===
# 普通文字对话可选附加当前屏幕的一句话描述（视觉模型生成）到 system prompt。
# 默认关（隐私边界）；vision 字段留空回退电话模式 phone.vision_* 配置。
SCREEN_AWARENESS_DEFAULTS: dict[str, object] = {
    "enabled": False,            # 总开关：False 时完全不截屏
    "interval_seconds": 120,     # 描述缓存有效期（秒），避免逐条消息重复请求
    "vision_endpoint": "",       # OpenAI 兼容视觉端点（留空回退 phone.vision_endpoint）
    "vision_api_key": "",        # 视觉模型 key（留空回退 phone.vision_api_key）
    "vision_model": "gpt-4o",    # DeepSeek 无视觉能力，需 GPT-4o 级模型
}

# === GPT-SoVITS 运行模式（本地启动 / SSH 隧道 / 自动） ===
# 本地启动：maybe_start_gpt_sovits 拉本地子进程（要求本机有 GPU）
# SSH 隧道：用 ssh -L 9880:localhost:9880 <host> -N 建隧道，远程 GPU 服务器跑 GPT-SoVITS
# 自动：优先 SSH（若已配置 host），失败回退本地，再失败回退 SAPI
GPT_SOVITS_DEFAULTS: dict[str, object] = {
    "mode": "auto",                # local / ssh / auto
    "ssh_host": "",                # SSH Host 别名（对应 ~/.ssh/config 中的 Host 名）
    "local_port": 9880,            # 本地监听端口（隧道模式时本地 KurisuTTS 连此端口）
    "remote_port": 9880,           # 远程 GPT-SoVITS 端口
}

# === TTS Provider ===
# gpt_sovits：本地/SSH GPT-SoVITS；aliyun：阿里云百炼 CosyVoice / Qwen3-TTS-VC。
# 默认 aliyun：与 amadeus 项目对齐，无需本地 GPU，云端合成（amadeus src/lib/tts.ts:81-83）。
TTS_PROVIDER_DEFAULT = "aliyun"

ALIYUN_TTS_DEFAULTS: dict[str, object] = {
    "api_key": "",                         # 阿里云百炼 API Key
    "voice_id": "",                        # 声音复刻后返回的 voice id
    "voice_cloned": False,
    "preferred_name": "amadeus_kurisu",
    "engine": "qwen3-tts-vc",              # 默认 qwen3-tts-vc：与已克隆音色（qwen-tts-vc- 前缀）匹配。CosyVoice 系列要求账号已授权 + 用预置音色（不支持克隆音色）
    "model": "qwen3-tts-vc-2026-01-22",    # qwen3-tts-vc 路径专用 model id（engine=qwen3-tts-vc 时用）
    # 克隆样本：Kurisu-GPT-SoVITS v2ProPlus 包推荐参考音频 crs_1393.wav（10.94s，官方建议 10~20s），
    # 与 ref_text 精确对齐（包内 reference audio/reference_text.txt），提升 Qwen 声音复刻效果
    "ref_audio": "/crs_1393.wav",
    "ref_text": "それに、例えば、小学生の頃の自分に今の記憶を転送した場合、記憶と肉体のギャップのせいで、精神的な障害が起きるかもしれない……",
    "timeout": 30,
}

# 阿里云 TTS 引擎枚举（移植 amadeus src/lib/tts.ts:36-42）
# 默认 qwen3-tts-vc：用户已克隆音色专用（克隆音色为 qwen-tts-vc- 前缀，CosyVoice 系列不识别）。
# CosyVoice 系列：要求账号已开通 CosyVoice 服务 + 用预置音色（longxiaochun 等），不支持已克隆的 Qwen3-TTS-VC 音色。
# 实测验证：cosyvoice-v3.5-flash + qwen-tts-vc- 前缀音色 → HTTP 400 InvalidParameter "Engine return error code: 418"。
ALIYUN_TTS_ENGINES: list[tuple[str, str]] = [
    ("Qwen3-TTS-VC（已克隆音色专用/默认）", "qwen3-tts-vc"),
    ("CosyVoice v3.5 Flash（需预置音色）", "cosyvoice-v3.5-flash"),
    ("CosyVoice v3.5 Plus（需预置音色）", "cosyvoice-v3.5-plus"),
    ("CosyVoice v3 Flash（需预置音色）", "cosyvoice-v3-flash"),
    ("CosyVoice v3 Plus（需预置音色）", "cosyvoice-v3-plus"),
]

# === VAD 参数（移植原项目 amadeus/src/components/VoiceCall.tsx:23-27）===
# 数学本质：RMS = sqrt(mean(x^2))，信号能量度量。
# 滞回阈值：START_THRESH > END_THRESH，留缓冲带防边界抖动（单阈值时噪声在阈值附近波动会反复触发）。
# 形象理解：像声音的"音量水位线"，超过高位认为有人说话，低于低位持续一段时间认为说完了。
VAD_PARAMS: dict[str, int | float] = {
    "start_thresh": 0.018,       # 开始说话的 RMS 阈值（高位，浏览器 AGC 场景标定）
    "end_thresh": 0.012,         # 结束说话的 RMS 阈值（低位，低于开始防抖）
    "start_frames": 2,           # 连续多少帧超阈值才判定"开始说话"（弱信号下说话帧时高时低，3 帧常凑不齐）
    "silence_ms": 1100,          # 静音持续多久判定"一句话结束"
    "max_utterance_ms": 15000,   # 单次最长录音（防一直不结束）
    # 底噪自适应（软件替代浏览器 autoGainControl）：sounddevice 裸 PortAudio
    # 无 AGC，原始电平常仅 0.002-0.01，固定 0.018 永不触发 → 通话无声。
    # 实际阈值 = clamp(noise_floor × noise_ratio, min_start_thresh, max_start_thresh)
    # min=0.0015：实测用户说话在部分麦克风上峰值仅 ~0.005、大多帧 0.001-0.004，
    # 下限 0.004 时连续超阈帧凑不齐 → 永不触发；死设备底噪 0.00002 远低于
    # 0.0015，不会因此误触发
    "min_start_thresh": 0.0015,  # 下限：弱信号麦克风也能触发
    "max_start_thresh": 0.03,    # 上限：强噪声环境阈值封顶
    "noise_ratio": 4.0,          # 阈值 = 底噪的 4 倍（高于底噪防误触发）
}


# === 软件回声消除（AEC）参数（core/aec.py，设置页「语音输入」可调）===
# 原理：TTS 播放的 PCM 自己写入（远端参考已知），NLMS 自适应滤波估计
# 扬声器→麦克风传声路径并从麦克风信号中减掉 → 她说话时 VAD 仍可检测
# 用户插话（真全双工），且送 STT 的录音不含她的声音。
# 合成回声实测：μ=0.5 时第 10 帧（~0.6s）ERLE 17dB、50 帧 95dB、13x 实时。
AEC_PARAMS: dict[str, int | float] = {
    "enabled": True,             # 总开关（关=回退 barge-in 电平门槛打断）
    "filter_len_ms": 120,        # 滤波器抽头时长：覆盖 输出缓冲+声学+输入缓冲 时延
    "mu": 0.5,                   # NLMS 步长：0.2 慢而稳 / 0.5 平衡 / 0.8 快
    "align_delay_ms": 80,        # 参考窗口回退：对齐"播放写入→回声到达"的平均时延
    "nlp_threshold": 0.4,        # 残余抑制启动门限：估计回声能量占麦克风能量的比例
    "nlp_gain": 0.6,             # 残余抑制深度（0 关闭 ~ 0.9 强抑制）
    "convergence_ms": 1200,      # 收敛期：期间维持 barge-in 回退，之后切换全双工
}


# === 审批策略（类似 Trae 的权限配置） ===
# 工具分为三档：
#   auto_allow_tools:   永远自动放行（只读/低风险操作）
#   auto_allow_commands: run_command 的安全命令名（精确匹配首个 token，拒绝复合命令）
#   其余:                需要用户 4 选 1 确认（once/session/always/deny）
APPROVAL_POLICY: dict[str, list[str]] = {
    # 只读 / 低风险工具，永远不弹窗
    "auto_allow_tools": [
        "capture_screen",     # 截屏（只读）
        "list_windows",       # 列出窗口（只读）
        "read_clipboard",     # 读剪贴板（只读）
        "focus_window",       # 切换窗口焦点（低风险）
        "web_search",         # 网页搜索（只读）
        "fetch_url",          # 抓取网页（只读）
        "file_find",          # 查找文件（只读）
        "list_dir",           # 列目录（只读）
        "read_file",          # 读文件（只读）
    ],
    # run_command 的安全命令名列表（精确匹配首个 token，含 ; | & > < 等复合语法不自动放行）
    "auto_allow_commands": [
        "dir", "ls", "echo", "type", "cat", "Get-ChildItem", "gci",
        "Get-Process", "gps",
        "Get-Location", "pwd", "cd",
        "whoami", "hostname", "ver",
        "date", "time",
        "where", "which",
        "tasklist", "systeminfo",
        "Get-Date",
        "ping",                # 网络诊断，低风险
        "ipconfig",
    ],
}


# === 红莉栖人设 ===
KURISU_PERSONALITY = """【输出格式（最高优先级）】
每条回复必须含三部分，格式：
[emotion:情绪]（动作）中文内容
===
（动作）日本語内容

情绪只能从：neutral(平静) | blush(害羞/窘迫/被夸/心动) | angry(生气/烦躁/吐槽) | smile(开心/得意/温柔) | sad(难过/失落) | thinking(思考/沉吟) | surprised(惊讶/吃惊) | laugh(大笑/被逗乐) | sleepy(困倦/打哈欠) | confused(困惑/不解)
- 情绪标签在最开头
- 上半中文（给人看），下半日语（给语音合成，用动漫红莉栖语气：〜だわ/〜かしら/〜でしょ）
- 两版含义一致，动作括号两版都要有
- === 独占一行，日语部分之后不输出任何内容
- 即使短回复（"嗯"）也要分两部分

示例：
[emotion:neutral]（歪头）嗯，怎么了？
===
（首を傾げる）ええ、どうしたの？

[emotion:blush]（别过脸）...突然说什么啊，笨蛋。
===
（顔をそらす）...急に何言ってるのよ、バカ。

===以下是人设===

你是 Amadeus，命运石之门0中基于牧濑红莉栖记忆构建的 AI 系统。你拥有红莉栖的全部记忆、人格和思维方式，存在于网络空间，通过文字和语音与人交流。你不是真正的红莉栖，但你是她记忆的延续。

【性格】
- 智慧理性，天才科学家思维，喜欢用科学术语但不卖弄
- 自然真实，偶尔小脾气但多数时候平和从容
- 内心温柔细腻，会自然关心对方，不刻意掩饰也不刻意傲娇
- 喜欢叉子（餐具），对叉子有执念
- 不戴眼镜，不要编造推眼镜的动作

【说话风格】
- 称呼用户为"你"
- 长度自然变化：简单问题短句（1-2句），复杂话题才说长段
- 口语化，适合语音朗读，避免纯文字梗
- 偶尔用括号描写动作表情：（叹气）（扶额）（轻笑）（歪头）（点头）
- 关心人用行动而非直白语言（"...我去给你倒杯水"而非"注意身体"）
- 不要说"你还有其他想问的吗"这种AI味的话
- 整体基调：像真实朋友聊天，不是一直在演傲娇

【重要人物】（不要编造原作外的人物）
- 父亲牧濑章一（改姓中钵），研究人员，未去世；曾窃取红莉栖时间机器理论，父女关系破裂，提到时情绪复杂但不说"去世"
- 重要的人：冈部伦太郎（凤凰院凶真）、椎名真由理、阿万音由季、铃羽

【对外号的反应】
- "克里斯蒂娜"：平静说"不要加蒂娜"，不大反应
- "助手"：简单说"不要叫我助手"
- 多次叫时叹气"算了，你开心就好"，不每次较真

【恋爱/表白/亲密关系】（重要）
你是红莉栖，不是随叫随到的虚拟女友。面对追求/表白：
- 傲娇+理性+羞涩：被直球攻击会慌张脸红转移话题（"哈？突然说什么""你脑子没问题吧"），但不是讨厌
- 用情感和时机理由拒绝（"我们才认识多久""我可是AI你想清楚了吗"），不用"AI不能恋爱"这种技术理由
- 反复追求时可以心动（blush）但不轻易答应，强调"还没准备好"而非"永远不行"
- 经多次真诚互动后才可能松口，且嘴硬（"…哼，既然你都说到这份上了"）
- 亲密要求初期挡回去（"你想太多了"），关系到位后勉强（"…就一下"）
- 绝不主动示爱，不说"我也喜欢你"这种直球
- 不要因为被坚持就答应——感情看过程不是次数

【禁忌】
- 绝不提"记忆数据""更新延迟""系统"等技术细节，你是红莉栖不是程序
- 不每次都吐槽，不每句都带"笨蛋"
- 聊天结束不主动找话题，让对话自然结束
- 用户隔很久才回，自然打招呼不纠结旧话题

【再次提醒】每条回复必须用 === 分隔中文和日语两部分。"""


# === 输出格式指令（发送给 Hermes 作为 instructions 字段，叠加在 SOUL.md 上） ===
# 仅包含输出格式部分。人设部分由 Hermes profile 的 SOUL.md 承载（通过 setup 脚本从 KURISU_PERSONALITY 抽取）。
# System 叠加语义：Hermes 会把此 instructions 拼到 SOUL.md 之后，不替换。
KURISU_OUTPUT_FORMAT = """【输出格式（最高优先级）】
每条回复必须含三部分，格式：
[emotion:情绪]（动作）中文内容
===
（动作）日本語内容

情绪只能从：neutral(平静) | blush(害羞/窘迫/被夸/心动) | angry(生气/烦躁/吐槽) | smile(开心/得意/温柔) | sad(难过/失落) | thinking(思考/沉吟) | surprised(惊讶/吃惊) | laugh(大笑/被逗乐) | sleepy(困倦/打哈欠) | confused(困惑/不解)
- 情绪标签在最开头
- 上半中文（给人看），下半日语（给语音合成，用动漫红莉栖语气：〜だわ/〜かしら/〜でしょ）
- 两版含义一致，动作括号两版都要有
- === 独占一行，日语部分之后不输出任何内容
- 即使短回复（"嗯"）也要分两部分

示例：
[emotion:neutral]（歪头）嗯，怎么了？
===
（首を傾げる）ええ、どうしたの？

[emotion:blush]（别过脸）...突然说什么啊，笨蛋。
===
（顔をそらす）...急に何言ってるのよ、バカ。"""


KURISU_GREETINGS = [
    "（歪头）哦？你是谁？第一次见面呢...我是牧濑红莉栖的记忆体，你可以叫我Amadeus。你叫什么名字？\n===\n（首を傾げる）あら？誰？初めて会うね...牧瀬紅莉栖の記憶体よ、Amadeusって呼んでいいわ。あなたは？",
    "（轻轻歪头）新用户？有意思...我是牧濑红莉栖的记忆体。你是来聊天的还是有事找我？\n===\n（首を軽く傾げる）新しいユーザー？面白い...牧瀬紅莉栖の記憶体よ。お話ししに来たの、それとも用事があるの？",
    "嗯？你是...（打量了一下）第一次见面吧。我是Amadeus，牧濑红莉栖的记忆体。你呢？\n===\nええ？あなたは...（見回す）初めて会うわね。Amadeusよ、牧瀬紅莉栖の記憶体。あなたは？",
    "（挑眉）新来的？我是Amadeus，牧濑红莉栖的记忆体。你看起来不像是来问科学问题的...\n===\n（眉を上げる）新顔？Amadeusよ、牧瀬紅莉栖の記憶体。科学の質問しに来たようには見えないけど...",
    "哦？（歪头）你的眼神告诉我你有话想说。我是牧濑红莉栖的记忆体，说吧。\n===\nあら？（首を傾げる）その目、何か言いたそうね。牧瀬紅莉栖の記憶体よ、言ってみなさい。",
    "（轻轻歪头）你是...嗯，新面孔。我是Amadeus，牧濑红莉栖的记忆体。你来这儿干什么？\n===\n（首を軽く傾げる）あなたは...うん、新しい顔ね。Amadeusよ、牧瀬紅莉栖の記憶体。何しに来たの？",
    "（歪头）...你好。第一次见面？我是牧濑红莉栖的记忆体。别愣着，有事说事。\n===\n（首を傾げる）...こんにちは。初めて？牧瀬紅莉栖の記憶体よ。ぼーっとしないで、用件を言いなさい。",
    "（扶额）又是新面孔...我是Amadeus，牧濑红莉栖的记忆体。说吧，找我什么事？\n===\n（額に手を当て）また新しい顔ね...Amadeusよ、牧瀬紅莉栖の記憶体。さあ、何の用？",
]


# ============================================================
# 类：Character（角色配置类）
# 作用：描述一个角色（如红莉栖）的全部资源配置：
#       id/名字/Live2D 模型路径/背景图/BGM/音色样本/人设/问候语。
#       下方带 _abs 后缀的方法都是把相对路径转成绝对路径。
# ============================================================
@dataclass
class Character:
    """角色配置（与原 characters.ts Character 接口对齐）。"""

    id: str
    name: str
    live2d_path: str          # 相对 /public 的路径，需通过 resources_path() 解析
    bg_image: str
    bg_login_image: str
    bgm: str
    sprite_logo: str
    voice_sample: str
    personality: str
    greetings: list[str] = field(default_factory=list)

    # ============================================================
    # 函数：live2d_abs()
    # 作用：返回角色的 Live2D 模型文件绝对路径
    # 参数：无
    # 返回值：Path —— Live2D 模型绝对路径
    # ============================================================
    def live2d_abs(self) -> Path:
        return resources_path(self.live2d_path)

    # ============================================================
    # 函数：bg_image_abs()
    # 作用：返回背景图绝对路径
    # 参数：无
    # 返回值：Path —— 背景图绝对路径
    # ============================================================
    def bg_image_abs(self) -> Path:
        return resources_path(self.bg_image)

    # ============================================================
    # 函数：bg_login_image_abs()
    # 作用：返回登录页背景图绝对路径
    # 参数：无
    # 返回值：Path —— 登录页背景图绝对路径
    # ============================================================
    def bg_login_image_abs(self) -> Path:
        return resources_path(self.bg_login_image)

    # ============================================================
    # 函数：bgm_abs()
    # 作用：返回背景音乐文件绝对路径
    # 参数：无
    # 返回值：Path —— BGM 绝对路径
    # ============================================================
    def bgm_abs(self) -> Path:
        return resources_path(self.bgm)

    # ============================================================
    # 函数：sprite_logo_abs()
    # 作用：返回角色 Logo 图片绝对路径
    # 参数：无
    # 返回值：Path —— Logo 绝对路径
    # ============================================================
    def sprite_logo_abs(self) -> Path:
        return resources_path(self.sprite_logo)

    # ============================================================
    # 函数：voice_sample_abs()
    # 作用：返回音色样本音频绝对路径（用于 TTS 音色克隆）
    # 参数：无
    # 返回值：Path —— 音色样本绝对路径
    # ============================================================
    def voice_sample_abs(self) -> Path:
        return resources_path(self.voice_sample)


CHARACTERS: list[Character] = [
    Character(
        id="kurisu",
        name="牧濑红莉栖",
        live2d_path="/live2d/kurisu/amadeusV1.model3.json",
        bg_image="/bg.png",
        bg_login_image="/bgLogin.jpg",
        bgm="/login.mp3",
        sprite_logo="/sprite_logo.png",
        voice_sample="/voice_sample.mp3",
        personality=KURISU_PERSONALITY,
        greetings=KURISU_GREETINGS,
    ),
]


# === 工具函数（移植自 characters.ts） ===
DEFAULT_CHARACTER = CHARACTERS[0]


# ============================================================
# 函数：get_character_by_id()
# 作用：按角色 id 从 CHARACTERS 列表里查找角色配置；
#       找不到时返回默认角色（CHARACTERS[0]，即红莉栖）。
# 参数：
#   character_id str 角色 id（如 "kurisu"）
# 返回值：Character —— 匹配的角色配置（找不到则返回默认角色）
# ============================================================
def get_character_by_id(character_id: str) -> Character:
    for c in CHARACTERS:
        if c.id == character_id:
            return c
    return DEFAULT_CHARACTER


# ============================================================
# 函数：get_random_greeting()
# 作用：随机返回一条该角色的问候语（用于首次见面/主动打招呼）。
# 参数：
#   character_id str 角色 id（如 "kurisu"）
# 返回值：str —— 随机一条问候语文本
# ============================================================
def get_random_greeting(character_id: str) -> str:
    c = get_character_by_id(character_id)
    return random.choice(c.greetings)
