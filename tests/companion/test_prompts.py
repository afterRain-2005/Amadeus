"""prompts.py 全量测试：模板条件/文本格式化/映射表完整性/prompt 字符串。

覆盖：

- KURISU_PROACTIVE_TEMPLATES 的 5 个模板的条件触发、文本格式化、emotion/topic

- COMPANION_TO_LIVE2D_EMOTION 映射完整性 + 有效值

- COMPANION_EMOTION_MOTION 映射完整性 + 有效值

- KURISU_PROACTIVE_INSTRUCTION / KURISU_PROACTIVE_PASS_THROUGH 内容验证

"""

from __future__ import annotations

from core.companion.prompts import (

    KURISU_PROACTIVE_TEMPLATES,

    KURISU_PROACTIVE_INSTRUCTION,

    KURISU_PROACTIVE_PASS_THROUGH,

    COMPANION_TO_LIVE2D_EMOTION,

    COMPANION_EMOTION_MOTION,

)

from core.companion.sensors import ContextSnapshot

def _snap(**kwargs) -> ContextSnapshot:

    defaults = dict(

        timestamp="2026-08-16T10:00:00Z", local_time="14:30 周二",

        is_deep_night=False, idle_seconds=10, work_session_minutes=5,

        idle_state="active", active_window_title="main.py - Code",

        active_process="Code.exe", window_changed_recently=False,

        last_companion_greeting_ts=None, last_companion_topic=None,

        greeting_count_today=0,

    )

    defaults.update(kwargs)

    return ContextSnapshot(**defaults)

# === 模板结构验证 ===

def test_templates_have_required_keys():

    """每个模板必须含 topic/condition/text/emotion 四个键。"""

    for tpl in KURISU_PROACTIVE_TEMPLATES:

        assert "topic" in tpl, f"模板缺少 topic 键"

        assert "condition" in tpl, f"模板缺少 condition 键"

        assert "text" in tpl, f"模板缺少 text 键"

        assert "emotion" in tpl, f"模板缺少 emotion 键"

        assert callable(tpl["condition"]), f"模板 {tpl.get('topic')} 的 condition 不是可调用对象"

        assert isinstance(tpl["text"], str), f"模板 {tpl.get('topic')} 的 text 不是字符串"

        assert isinstance(tpl["emotion"], str), f"模板 {tpl.get('topic')} 的 emotion 不是字符串"

def test_templates_have_unique_topics():

    topics = [tpl["topic"] for tpl in KURISU_PROACTIVE_TEMPLATES]

    assert len(topics) == len(set(topics)), f"模板 topic 不唯一: {topics}"

# === L1 硬阈值条件 ===

def test_template_idle_condition_triggers():

    """idle 模板：idle_seconds > 900 时命中。"""

    idle_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "idle")

    assert idle_tpl["condition"](_snap(idle_seconds=901)) is True

    assert idle_tpl["condition"](_snap(idle_seconds=900)) is False

    assert idle_tpl["condition"](_snap(idle_seconds=10)) is False

def test_template_sleepy_condition_triggers():

    """sleepy 模板：is_deep_night and work_session_minutes > 30 时命中。"""

    sleepy_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "sleepy")

    assert sleepy_tpl["condition"](_snap(is_deep_night=True, work_session_minutes=31)) is True

    assert sleepy_tpl["condition"](_snap(is_deep_night=True, work_session_minutes=30)) is False

    assert sleepy_tpl["condition"](_snap(is_deep_night=False, work_session_minutes=60)) is False

def test_template_concern_condition_triggers():

    """concern 模板：work_session_minutes > 120 时命中。"""

    concern_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "concern")

    assert concern_tpl["condition"](_snap(work_session_minutes=121)) is True

    assert concern_tpl["condition"](_snap(work_session_minutes=120)) is False

    assert concern_tpl["condition"](_snap(work_session_minutes=10)) is False

def test_template_focus_break_condition_triggers_for_one_hour_coding():

    """focus_break 模板：活跃写代码满 60 分钟时命中。"""

    focus_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "focus_break")

    assert focus_tpl["condition"](_snap(
        work_session_minutes=60,
        active_window_title="main.py - Visual Studio Code",
        active_process="Code.exe",
    )) is True

    assert focus_tpl["condition"](_snap(
        work_session_minutes=59,
        active_window_title="main.py - Visual Studio Code",
        active_process="Code.exe",
    )) is False

    assert focus_tpl["condition"](_snap(
        work_session_minutes=60,
        active_window_title="Inbox - Mail",
        active_process="Mail.exe",
    )) is False

    assert focus_tpl["condition"](_snap(
        idle_seconds=300,
        work_session_minutes=60,
        active_window_title="main.py - Visual Studio Code",
        active_process="Code.exe",
    )) is False

def test_template_tease_condition_triggers():

    """tease 模板：window_changed_recently and greeting_count_today == 0 时命中。"""

    tease_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "tease")

    assert tease_tpl["condition"](_snap(window_changed_recently=True, greeting_count_today=0)) is True

    assert tease_tpl["condition"](_snap(window_changed_recently=True, greeting_count_today=1)) is False

    assert tease_tpl["condition"](_snap(window_changed_recently=False, greeting_count_today=0)) is False

def test_template_away_long_condition_triggers():

    """away_long 模板：idle_state == 'away' and idle_seconds > 3600 时命中。"""

    away_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "away_long")

    assert away_tpl["condition"](_snap(idle_state="away", idle_seconds=3601)) is True

    assert away_tpl["condition"](_snap(idle_state="away", idle_seconds=3600)) is False

    assert away_tpl["condition"](_snap(idle_state="active", idle_seconds=5000)) is False

    assert away_tpl["condition"](_snap(idle_state="idle", idle_seconds=5000)) is False

# === 模板文本格式化 ===

def test_template_sleepy_text_formats_local_time():

    """sleepy 模板文本应包含 {local_time} 占位符替换结果。"""

    sleepy_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "sleepy")

    text = sleepy_tpl["text"].format(local_time="03:15 周三", work_session_minutes=45)

    assert "03:15" in text

    assert "{local_time}" not in text

def test_template_concern_text_formats_work_session():

    """concern 模板文本应包含 {work_session_minutes} 占位符替换结果。"""

    concern_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "concern")

    text = concern_tpl["text"].format(local_time="14:30", work_session_minutes=150)

    assert "150" in text

    assert "{work_session_minutes}" not in text

def test_template_focus_break_text_formats_work_session():

    """focus_break 模板文本应包含 {work_session_minutes} 占位符替换结果。"""

    focus_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "focus_break")

    text = focus_tpl["text"].format(local_time="14:30", work_session_minutes=60)

    assert "60" in text

    assert "{work_session_minutes}" not in text

def test_template_idle_text_no_placeholders():

    """idle 模板文本无格式化占位符。"""

    idle_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "idle")

    text = idle_tpl["text"].format(local_time="14:30", work_session_minutes=5)

    assert text == idle_tpl["text"]

def test_template_away_long_text_no_placeholders():

    away_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "away_long")

    text = away_tpl["text"].format(local_time="14:30", work_session_minutes=5)

    assert text == away_tpl["text"]

def test_template_tease_text_no_placeholders():

    tease_tpl = next(t for t in KURISU_PROACTIVE_TEMPLATES if t["topic"] == "tease")

    text = tease_tpl["text"].format(local_time="14:30", work_session_minutes=5)

    assert text == tease_tpl["text"]

# === 模板 emotion 验证 ===

def test_template_emotions_all_have_live2d_mapping():

    """所有模板 emotion 必须在 COMPANION_TO_LIVE2D_EMOTION 和 COMPANION_EMOTION_MOTION 中有映射。"""

    for tpl in KURISU_PROACTIVE_TEMPLATES:

        em = tpl["emotion"]

        assert em in COMPANION_TO_LIVE2D_EMOTION, f"模板 {tpl['topic']} 的 emotion '{em}' 缺少 Live2D 映射"

        assert em in COMPANION_EMOTION_MOTION, f"模板 {tpl['topic']} 的 emotion '{em}' 缺少动作映射"

# === COMPANION_TO_LIVE2D_EMOTION 映射 ===

VALID_LIVE2D_EMOTIONS = {"neutral", "blush", "angry", "smile", "sad"}

def test_emotion_map_idle_to_neutral():

    assert COMPANION_TO_LIVE2D_EMOTION["idle"] == "neutral"

def test_emotion_map_sleepy_to_sad():

    assert COMPANION_TO_LIVE2D_EMOTION["sleepy"] == "sad"

def test_emotion_map_concern_to_sad():

    assert COMPANION_TO_LIVE2D_EMOTION["concern"] == "sad"

def test_emotion_map_tease_to_angry():

    assert COMPANION_TO_LIVE2D_EMOTION["tease"] == "angry"

def test_emotion_map_happy_to_smile():

    assert COMPANION_TO_LIVE2D_EMOTION["happy"] == "smile"

def test_emotion_map_neutral_identity():

    assert COMPANION_TO_LIVE2D_EMOTION["neutral"] == "neutral"

def test_emotion_map_passthrough_emotions():

    """Live2D 原生情绪应身份映射（不变）。"""

    for native in ("neutral", "blush", "angry", "smile", "sad"):

        assert COMPANION_TO_LIVE2D_EMOTION[native] == native

def test_emotion_map_thinking_to_neutral():

    assert COMPANION_TO_LIVE2D_EMOTION["thinking"] == "neutral"

def test_all_mapped_emotions_are_valid_live2d():

    """所有映射后的值必须是 Live2D 可识别的情绪标签。"""

    for companion_em, live2d_em in COMPANION_TO_LIVE2D_EMOTION.items():

        assert live2d_em in VALID_LIVE2D_EMOTIONS, \
            f"companion '{companion_em}' → 无效 Live2D 情绪 '{live2d_em}'"

def test_emotion_map_keys_are_superset_of_motion_map_keys():

    """两个映射表的 key 集合应完全一致。"""

    assert set(COMPANION_TO_LIVE2D_EMOTION.keys()) == set(COMPANION_EMOTION_MOTION.keys())

# === COMPANION_EMOTION_MOTION 映射 ===

VALID_LIVE2D_MOTIONS = {"neutral", "smile", "blush", "angry", "sad", "thinking"}

def test_motion_map_idle_to_thinking():

    assert COMPANION_EMOTION_MOTION["idle"] == "thinking"

def test_motion_map_sleepy_to_sad():

    assert COMPANION_EMOTION_MOTION["sleepy"] == "sad"

def test_motion_map_concern_to_sad():

    assert COMPANION_EMOTION_MOTION["concern"] == "sad"

def test_motion_map_tease_to_angry():

    assert COMPANION_EMOTION_MOTION["tease"] == "angry"

def test_motion_map_happy_to_smile():

    assert COMPANION_EMOTION_MOTION["happy"] == "smile"

def test_motion_map_neutral_to_neutral():

    assert COMPANION_EMOTION_MOTION["neutral"] == "neutral"

def test_motion_map_thinking_to_thinking():

    assert COMPANION_EMOTION_MOTION["thinking"] == "thinking"

def test_all_mapped_motions_are_valid():

    """所有映射后的动作名必须在 live2d_page.html MOTIONS 中存在。"""

    for companion_em, motion in COMPANION_EMOTION_MOTION.items():

        assert motion in VALID_LIVE2D_MOTIONS, \
            f"companion '{companion_em}' → 无效 Live2D 动作 '{motion}'"

# === KURISU_PROACTIVE_INSTRUCTION 验证 ===

def test_instruction_contains_role_definition():

    """指令应包含角色定义（牧濑红莉栖）。"""

    assert "牧濑红莉栖" in KURISU_PROACTIVE_INSTRUCTION

def test_instruction_contains_style_requirements():

    """指令应包含风格要求关键词。"""

    assert "傲娇" in KURISU_PROACTIVE_INSTRUCTION

    assert "毒舌" in KURISU_PROACTIVE_INSTRUCTION

def test_instruction_contains_json_format():

    """指令应包含 JSON 输出格式说明。"""

    assert "should_speak" in KURISU_PROACTIVE_INSTRUCTION

    assert "emotion" in KURISU_PROACTIVE_INSTRUCTION

    assert "topic" in KURISU_PROACTIVE_INSTRUCTION

def test_instruction_contains_emotion_options():

    """指令应列出可选 emotion 值。"""

    assert "neutral" in KURISU_PROACTIVE_INSTRUCTION

    assert "tease" in KURISU_PROACTIVE_INSTRUCTION

    assert "concern" in KURISU_PROACTIVE_INSTRUCTION

    assert "sleepy" in KURISU_PROACTIVE_INSTRUCTION

    assert "idle" in KURISU_PROACTIVE_INSTRUCTION

    assert "angry" in KURISU_PROACTIVE_INSTRUCTION

def test_instruction_contains_topic_options():

    """指令应列出可选 topic 值。"""

    assert "idle" in KURISU_PROACTIVE_INSTRUCTION

    assert "work" in KURISU_PROACTIVE_INSTRUCTION

    assert "deep_night" in KURISU_PROACTIVE_INSTRUCTION

    assert "focus_break" in KURISU_PROACTIVE_INSTRUCTION

    assert "window_change" in KURISU_PROACTIVE_INSTRUCTION

    assert "general" in KURISU_PROACTIVE_INSTRUCTION

def test_instruction_forbids_ai_reveal():

    """指令应禁止暴露 AI 身份。"""

    assert "AI" in KURISU_PROACTIVE_INSTRUCTION or "ai" in KURISU_PROACTIVE_INSTRUCTION.lower()

# === KURISU_PROACTIVE_PASS_THROUGH 验证 ===

def test_pass_through_formats_with_text():

    """pass-through 模板应能格式化 {text} 占位符。"""

    result = KURISU_PROACTIVE_PASS_THROUGH.format(text="测试问候文本")

    assert "测试问候文本" in result

    assert "{text}" not in result

def test_pass_through_preserves_meaning_instruction():

    """pass-through 应包含"不要改变意思"指令。"""

    assert "不要改变意思" in KURISU_PROACTIVE_PASS_THROUGH or "微调" in KURISU_PROACTIVE_PASS_THROUGH
