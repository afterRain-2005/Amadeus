"""表现层解码器：把 Amadeus 模型回复 → Live2D 表情/动作/口型指令。

模型回复格式（KURISU_OUTPUT_FORMAT）：
    [emotion:blush]（别过脸）...突然说什么啊，笨蛋。
    ===
    （顔をそらす）...急に何言ってるのよ、バカ。

- emotion_parser.parse_reply 只提取 [emotion:xxx] 标签，丢弃（动作）括号。
- 本模块补全：解析动作括号（中日文词表）→ motion 名；配置本地 Ollama
  小模型（qwen2.5:0.5b 级）对回复做 emotion+motion 分类，规则解析兜底。
- 任何异常（Ollama 不可达、超时、非法返回）回退规则解析，永不影响主流程。

motion 词表与 live2d/live2d_page.html MOTIONS 字典一一对应：
  neutral/smile/blush/angry/sad/thinking（原有 6 种头部动作）
  + hands_on_hips（叉腰）/ arms_crossed（抱胸）/ facepalm（扶额）/
    shrug（摊手）/ chin_rest（托腮）（新肢体动作，驱动 Param6/7/4 手臂参数）
  + surprised（惊讶后仰）/ laugh（大笑点头）/ sleepy（困倦低头）/
    confused（困惑扫视）（情绪动作扩充，对齐 airi 情感表）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

# === 合法标签集合 ===

VALID_EMOTIONS: frozenset[str] = frozenset(
    {
        "neutral", "blush", "angry", "smile", "sad", "thinking",
        "surprised", "laugh", "sleepy", "confused",
    }
)

VALID_MOTIONS: frozenset[str] = frozenset(
    {
        "neutral", "smile", "blush", "angry", "sad", "thinking",
        "hands_on_hips", "arms_crossed", "facepalm", "shrug", "chin_rest",
        "surprised", "laugh", "sleepy", "confused",
    }
)

# === 动作括号 → motion 映射（中/日文词表） ===
# 匹配优先级：词条顺序即优先级（"叉腰" 在 "腰" 之前，避免子串误命中）。
ACTION_MOTION_MAP: list[tuple[str, str]] = [
    # 叉腰
    ("叉腰", "hands_on_hips"), ("手を腰に", "hands_on_hips"),
    # 抱胸
    ("抱胸", "arms_crossed"), ("腕を組む", "arms_crossed"), ("抱臂", "arms_crossed"),
    # 扶额
    ("扶额", "facepalm"), ("額に手を当て", "facepalm"), ("捂额", "facepalm"),
    # 摊手
    ("摊手", "shrug"), ("肩をすくめる", "shrug"), ("耸肩", "shrug"),
    # 托腮
    ("托腮", "chin_rest"), ("頬杖", "chin_rest"), ("托下巴", "chin_rest"),
    # 思考
    ("思考", "thinking"), ("考え", "thinking"), ("沉吟", "thinking"),
    # 歪头（疑问）
    ("歪头", "neutral"), ("首を傾げる", "neutral"), ("側頭", "neutral"),
    ("侧头", "neutral"), ("偏头", "neutral"),
    # 点头（开心/得意）
    ("点头", "smile"), ("うなずく", "smile"), ("颔首", "smile"),
    # 别过脸（害羞）
    ("别过脸", "blush"), ("顔をそらす", "blush"), ("脸红", "blush"),
    ("侧过脸", "blush"), ("扭头", "blush"),
    # 前倾（生气/吐槽）
    ("前倾", "angry"), ("探身", "angry"), ("挑眉", "angry"),
    # 低头（难过）
    ("低头", "sad"), ("うつむく", "sad"), ("垂下", "sad"), ("垂头", "sad"),
    # 惊讶（后仰/瞪大眼）
    ("惊讶", "surprised"), ("吃惊", "surprised"), ("瞪大眼", "surprised"),
    ("驚く", "surprised"), ("目を見開く", "surprised"), ("愣住", "surprised"),
    # 大笑
    ("大笑", "laugh"), ("笑出声", "laugh"), ("哈哈", "laugh"),
    ("大笑い", "laugh"), ("笑う", "laugh"),
    # 困倦（打哈欠/揉眼）
    ("打哈欠", "sleepy"), ("困", "sleepy"), ("揉眼", "sleepy"), ("犯困", "sleepy"),
    ("あくび", "sleepy"), ("欠伸", "sleepy"), ("眠い", "sleepy"),
    # 困惑
    ("困惑", "confused"), ("疑惑", "confused"), ("不解", "confused"),
    ("困惑する", "confused"), ("疑問", "confused"), ("？？", "confused"),
]

# 动作括号正则：中文/日文括号都认（（…）(…)（半角全角混用）
_ACTION_RE = re.compile(r"[（(]([^（()）]{1,12})[)）]")
_EMOTION_RE = re.compile(r"\[emotion:([a-z_]+)\]")

# emotion → 默认 motion（无动作括号时按情绪给动作）
EMOTION_DEFAULT_MOTION: dict[str, str] = {
    "neutral": "neutral",
    "blush": "blush",
    "angry": "angry",
    "smile": "smile",
    "sad": "sad",
    "thinking": "thinking",
    "surprised": "surprised",
    "laugh": "laugh",
    "sleepy": "sleepy",
    "confused": "confused",
}

# Ollama 分类 system prompt：只输出 JSON，一个词解释都不要
_CLASSIFY_SYSTEM = (
    "你是 Live2D 角色的表情动作分类器。给定红莉栖（牧瀬紅莉栖）的台词文本，"
    "判断她说话时的表情和肢体动作，只输出 JSON，不要输出任何其他内容。\n"
    '格式：{"emotion": "表情", "motion": "动作"}\n'
    "emotion 只能是：neutral | blush | angry | smile | sad | thinking | "
    "surprised | laugh | sleepy | confused\n"
    "motion 只能是：neutral | smile | blush | angry | sad | thinking | "
    "hands_on_hips | arms_crossed | facepalm | shrug | chin_rest | "
    "surprised | laugh | sleepy | confused\n"
    "含义：neutral=平静歪头, smile=开心点头, blush=害羞别过脸, angry=生气前倾, "
    "sad=难过低头, thinking=思考歪头, surprised=惊讶后仰, laugh=大笑点头, "
    "sleepy=困倦低头, confused=困惑歪头扫视, hands_on_hips=叉腰, arms_crossed=抱胸, "
    "facepalm=扶额, shrug=摊手耸肩, chin_rest=托腮\n"
    "文本中的（动作）括号或 [emotion:xxx] 标签是作者提示，优先遵循。"
)


@dataclass(frozen=True)
class ParsedExpression:
    """回复 → 表现指令。motion 为 "" 表示无需动作（保持待机）。"""

    emotion: str = "neutral"
    motion: str = "neutral"
    mouth: float = 0.0  # 0.0-1.0，口型强度（音量驱动时用，规则解析恒为 0.0）


@dataclass(frozen=True)
class ClassifyConfig:
    """Ollama 本地小模型配置（复用 agent_router.ollama）。"""

    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:0.5b"
    timeout: float = 3.0


def parse_expression(reply: str) -> ParsedExpression:
    """纯规则解析：emotion 标签 + 动作括号词表。

    - [emotion:xxx] 标签 → emotion（非法值回退 neutral）
    - （动作）括号逐词匹配 ACTION_MOTION_MAP（中文/日文）
    - 无括号命中 → motion = EMOTION_DEFAULT_MOTION[emotion]
    """
    text = (reply or "").strip()
    if not text:
        return ParsedExpression()

    emotion = "neutral"
    match = _EMOTION_RE.search(text)
    if match and match.group(1) in VALID_EMOTIONS:
        emotion = match.group(1)

    motion: Optional[str] = None
    for action_text in _ACTION_RE.findall(text):
        for keyword, motion_name in ACTION_MOTION_MAP:
            if keyword in action_text:
                motion = motion_name
                break
        if motion is not None:
            break
    if motion is None:
        motion = EMOTION_DEFAULT_MOTION.get(emotion, "neutral")

    return ParsedExpression(emotion=emotion, motion=motion)


def classify_expression(
    text: str,
    *,
    base_url: str,
    model: str,
    timeout: float = 3.0,
) -> Optional[ParsedExpression]:
    """调本地 Ollama 小模型分类回复 → (emotion, motion)。

    与 ollama_router.route_with_ollama 同款 fail-open 约定：
    任何异常（不可达/超时/非法返回）返回 None，由 caller 回退规则解析。
    """
    if not text or not text.strip():
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": _CLASSIFY_SYSTEM},
                        {"role": "user", "content": text[:500]},
                    ],
                },
            )
        if resp.is_error:
            return None
        content = resp.json().get("message", {}).get("content", "")
        data = json.loads(content)
        emotion = str(data.get("emotion", "")).strip()
        motion = str(data.get("motion", "")).strip()
        if emotion not in VALID_EMOTIONS:
            emotion = "neutral"
        if motion not in VALID_MOTIONS:
            motion = ""
        return ParsedExpression(emotion=emotion, motion=motion)
    except Exception:
        return None  # fail-open：任何异常（不可达/超时/意外）都回退规则解析


def decide_expression(
    reply: str,
    *,
    ollama: Optional[dict] = None,
) -> ParsedExpression:
    """综合判定：配置了 Ollama → 先小模型分类，失败回退规则解析。

    ollama 配置结构（来自 load_config()["agent_router"]["ollama"]）：
    {"base_url": str, "model": str, "timeout": float}；None/空 → 纯规则。
    """
    if ollama:
        try:
            cfg = ClassifyConfig(
                base_url=str(ollama.get("base_url") or ""),
                model=str(ollama.get("model") or ""),
                timeout=float(ollama.get("timeout") or 3.0),
            )
        except (TypeError, ValueError):
            cfg = ClassifyConfig()
        if cfg.base_url and cfg.model:
            classified = classify_expression(
                reply, base_url=cfg.base_url, model=cfg.model, timeout=cfg.timeout
            )
            if classified is not None and classified.motion:
                return classified
    return parse_expression(reply)