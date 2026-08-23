"""Companion 主动问候 prompt 模板 + Live2D 表情/动作映射。

COMPANION_TO_LIVE2D_EMOTION: companion 内部情绪 → Live2D 可识别情绪标签。
  companion 评估器输出 idle/sleepy/concern/tease 等，但 Live2D 只认
  neutral/blush/angry/smile/sad（emotion_parser._EMOTION_RE 也仅匹配这 5 种）。
  此映射确保 companion 问候时 Live2D 表情正确变化。

COMPANION_EMOTION_MOTION: companion 内部情绪 → Live2D 动作名。
  live2d_page.html 定义了 6 种动作（neutral/smile/blush/angry/sad/thinking），
  每种对头部/身体参数做短补间偏移，让角色不只眨眼还会歪头/点头/前倾等。
"""


def _looks_like_coding(s) -> bool:
    text = f"{s.active_window_title} {s.active_process}".lower()
    coding_markers = (
        "code",
        "pycharm",
        "idea",
        "webstorm",
        "visual studio",
        "devenv",
        "cursor",
        "zed",
        "sublime",
        "vim",
        "nvim",
        "emacs",
        "terminal",
        "powershell",
        "cmd",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".rs",
        ".go",
        ".java",
        ".cpp",
        ".cs",
        ".vue",
        ".md",
    )
    return any(marker in text for marker in coding_markers)


def _focus_break_condition(s) -> bool:
    return (
        s.idle_state == "active"
        and s.idle_seconds < 300
        and 60 <= s.work_session_minutes < 120
        and _looks_like_coding(s)
    )


KURISU_PROACTIVE_TEMPLATES = [
    {
        "topic": "away_long",
        "condition": lambda s: s.idle_state == "away" and s.idle_seconds > 3600,
        "text": "很久没碰电脑了，还在吗？",
        "emotion": "neutral",
    },
    {
        "topic": "idle",
        "condition": lambda s: s.idle_seconds > 900,
        "text": "盯着屏幕发呆也修不好 bug，不如起来走走？",
        "emotion": "idle",
    },
    {
        "topic": "sleepy",
        "condition": lambda s: s.is_deep_night and s.work_session_minutes > 30,
        "text": "现在 {local_time} 了，你不睡觉我也不睡啊",
        "emotion": "sleepy",
    },
    {
        "topic": "concern",
        "condition": lambda s: s.work_session_minutes > 120,
        "text": "你已经坐了 {work_session_minutes} 分钟了，颈椎不要了？",
        "emotion": "concern",
    },
    {
        "topic": "focus_break",
        "condition": _focus_break_condition,
        "text": "代码写了 {work_session_minutes} 分钟了，起来活动一下。",
        "emotion": "concern",
    },
    {
        "topic": "tease",
        "condition": lambda s: s.window_changed_recently and s.greeting_count_today == 0,
        "text": "切换窗口切得这么勤，是在摸鱼吧？",
        "emotion": "tease",
    },
]

KURISU_PROACTIVE_INSTRUCTION = """你是牧濑红莉栖，主动观察用户在做什么并吐槽/关心。

风格要求：
- 傲娇、毒舌但关心、偶尔卖萌，参考石头门原作
- 长度限制：≤30 字（气泡宽度限制）
- 永远不暴露你是 AI 助手、不提"作为AI"等
- 不重复用户最近 2 小时内听过的主题

判断规则：
- should_speak=false 当用户明显在专注工作/会议/重要操作时
- should_speak=true 当有自然吐槽/关心机会时（不在专注状态）

JSON 输出格式：
{"should_speak": bool, "text": str, "emotion": str, "topic": str}

emotion 可选：neutral/happy/tease/concern/sleepy/idle/angry
topic 可选：idle/work/deep_night/focus_break/tease/window_change/general
"""

KURISU_PROACTIVE_PASS_THROUGH = """你接下来要说的话已经准备好了，把以下内容用你的语气自然说出，可以微调措辞但不要改变意思：

{text}"""

# === Companion 情绪 → Live2D 表情/动作映射 ===

COMPANION_TO_LIVE2D_EMOTION: dict[str, str] = {
    # companion 评估器输出 → Live2D 可识别 emotion
    "idle":     "neutral",
    "sleepy":   "sleepy",
    "concern":  "sad",
    "tease":    "angry",
    "happy":    "smile",
    "neutral":  "neutral",
    "angry":    "angry",
    "blush":    "blush",
    "sad":      "sad",
    "smile":    "smile",
    "thinking": "thinking",
}

COMPANION_EMOTION_MOTION: dict[str, str] = {
    # companion 情绪 → Live2D 动作名（对应 live2d_page.html MOTIONS 字典）
    "idle":     "thinking",   # 发呆 → 左右歪头 + 眼球扫视
    "sleepy":   "sleepy",     # 困倦 → 缓慢低头打哈欠
    "concern":  "sad",        # 关心 → 低头前倾
    "tease":    "angry",      # 吐槽 → 生气前倾 + 微抖
    "happy":    "smile",      # 开心 → 点头
    "neutral":  "neutral",    # 平静 → 歪头疑问
    "angry":    "angry",      # 生气 → 前倾微抖
    "blush":    "blush",      # 害羞 → 别过脸
    "sad":      "sad",        # 难过 → 低头
    "smile":    "smile",      # 微笑 → 点头
    "thinking": "thinking",   # 思考 → 歪头扫视
}
