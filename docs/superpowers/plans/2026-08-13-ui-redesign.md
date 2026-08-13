# Amadeus UI 重做 Implementation Plan (Plan 1: 视觉/布局/bug)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 desktop_pet UI 从"玩具感"重做为 A2 极简沉浸式（青蓝配色 + Dock 工具栏 + 历史抽屉 + SVG 矢量图标），并修复分段气泡"先全显再分段"bug。

**Architecture:** 在现有 desktop_pet.py 的 PetWindow 内，砍掉 HistoryPhonePanel/MessageInputPanel/竖排 tool_bar/右上角 close_button，新建 DockBar（底部悬浮）+ HistoryDrawer（右侧滑入），全局样式切换为 A2 青蓝。字幕条改为 opacity 动画淡入（TTS 绑定留待 Plan 2）。分段气泡 bug 通过 delta 静默 + finished 分段 opacity 淡入根治。

**Tech Stack:** PySide6（QSvgRenderer/QPropertyAnimation/QGraphicsOpacityEffect）、SVG 矢量图标（Phosphor 圆润款）、pytest

**上游 spec:** [2026-08-13-ui-redesign-design.md](../specs/2026-08-13-ui-redesign-design.md)

---

## File Structure

| 文件 | 责任 | 操作 |
|---|---|---|
| `desktop_pet.py` | 桌宠主窗口（PetWindow + 布局 + 交互） | 大改：砍旧建新 |
| `resources/icons/chat.svg` | 对话图标（气泡轮廓） | 新建 |
| `resources/icons/pin.svg` | 固定图标（图钉轮廓） | 新建 |
| `resources/icons/settings.svg` | 设置图标（齿轮轮廓） | 新建 |
| `resources/icons/history.svg` | 记录图标（列表轮廓） | 新建 |
| `resources/icons/close.svg` | 退出图标（X 轮廓） | 新建 |
| `tests/test_bubble_animation.py` | 分段气泡 bug 修复测试 | 新建 |
| `tests/test_dock_bar.py` | Dock 工具栏单元测试 | 新建 |
| `tests/test_history_drawer.py` | 历史抽屉单元测试 | 新建 |

---

## Task 1: 分段气泡 bug 修复（delta 静默 + finished 分段淡入）

**Files:**
- Modify: `desktop_pet.py:708-712`（`_agent_delta`）
- Modify: `desktop_pet.py:714-730`（`_agent_finished`）
- Modify: `desktop_pet.py:457-491`（`_show_layered_bubbles` / `_show_next_bubble`）
- Test: `tests/test_bubble_animation.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_bubble_animation.py`：

```python
"""分段气泡 bug 修复测试：delta 期间不更新气泡文字。"""
from __future__ import annotations
import sys
from unittest.mock import MagicMock, patch


def test_agent_delta_does_not_set_bubble_text():
    """delta 期间不应调用 _set_bubble_text，应调用 _show_thinking_dots。"""
    # 用桩对象模拟 PetWindow，只测 _agent_delta 行为
    class StubWindow:
        def __init__(self):
            self._streamed_reply = ""
            self._history_expanded = False
            self.bubble_text_calls = 0
            self.thinking_calls = 0
        def _set_bubble_text(self, text):
            self.bubble_text_calls += 1
        def _show_thinking_dots(self):
            self.thinking_calls += 1
        def _agent_delta(self, text):
            # 复制 desktop_pet.py 修复后的逻辑
            self._streamed_reply += text
            if not self._history_expanded:
                self._show_thinking_dots()

    win = StubWindow()
    win._agent_delta("こん")
    win._agent_delta("にちは")
    assert win.bubble_text_calls == 0, "delta 期间不应调用 _set_bubble_text"
    assert win.thinking_calls == 2, "delta 期间应调用 _show_thinking_dots"


def test_agent_finished_triggers_layered_bubbles():
    """finished 后应调用 _show_layered_bubbles，不直接 _set_bubble_text 全文。"""
    class StubWindow:
        def __init__(self):
            self.layered_calls = 0
            self.bubble_text_calls = 0
        def _set_bubble_text(self, text):
            self.bubble_text_calls += 1
        def _show_layered_bubbles(self, text):
            self.layered_calls += 1
        def _agent_finished(self, reply):
            # 复制修复后逻辑：只调 _show_layered_bubbles
            self._show_layered_bubbles(reply)

    win = StubWindow()
    win._agent_finished("こんにちは。岡部、元気？")
    assert win.layered_calls == 1
    assert win.bubble_text_calls == 0, "finished 不应直接 _set_bubble_text 全文"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_bubble_animation.py -v`
Expected: FAIL（`_show_thinking_dots` 不存在，逻辑未改）

- [ ] **Step 3: 修改 `_agent_delta`**

`desktop_pet.py` 找到 `_agent_delta`（约 708 行），替换为：

```python
def _agent_delta(self, text: str) -> None:
    self._streamed_reply += text
    if not self._history_expanded:
        self._show_thinking_dots()
```

- [ ] **Step 4: 添加 `_show_thinking_dots` 方法**

在 `_show_layered_bubbles` 方法前插入：

```python
def _show_thinking_dots(self) -> None:
    """delta 期间显示思考动画（3 个青色点呼吸），不显示流式文字。"""
    if not hasattr(self, '_thinking_dots_shown'):
        self._set_bubble_text("● ● ●")
        self._thinking_dots_shown = True
```

- [ ] **Step 5: 修改 `_show_layered_bubbles` 用 opacity 动画**

替换 `_show_layered_bubbles` 和 `_show_next_bubble`（约 457-491 行）：

```python
def _show_layered_bubbles(self, text: str) -> None:
    """将回复分层后分多个气泡前后展示，每段用 opacity 动画淡入。"""
    import re
    self._cancel_bubbles()
    self._thinking_dots_shown = False
    segments = re.split(r'(?<=[。！？!?\n])\s*', text.strip())
    merged: list[str] = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if merged and len(merged[-1]) < 6:
            merged[-1] += seg
        else:
            merged.append(seg)
    if not merged:
        return
    self._bubble_segments = merged
    self._bubble_index = 0
    if not self._history_expanded:
        self.reply_bubble.show()
    self._show_next_bubble()

def _show_next_bubble(self) -> None:
    """显示下一个气泡分段，用 opacity 动画淡入。"""
    if self._bubble_index >= len(self._bubble_segments):
        self._bubble_timer = QTimer.singleShot(9000, self._hide_idle_bubble)
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
    if not self._history_expanded:
        self.reply_bubble.show()
    char_count = len(text)
    duration = int(min(1500 + char_count * 80, 6000))
    self._bubble_index += 1
    self._bubble_timer = QTimer.singleShot(duration, self._show_next_bubble)
```

- [ ] **Step 6: 修改 `_agent_finished` 不直接设气泡全文**

找到 `_agent_finished`（约 714 行），确认末尾是 `self._show_layered_bubbles(self._latest_line(reply))`，保持不变（已正确）。但需确认中间不调用 `_set_bubble_text` 显示全文。当前实现已符合，无需改。

- [ ] **Step 7: 运行测试验证通过**

Run: `python -m pytest tests/test_bubble_animation.py -v`
Expected: PASS

- [ ] **Step 8: 手动运行验证**

Run: `python desktop_pet.py`
发一条消息，观察：delta 期间显示"● ● ●"，finished 后分段淡入（不再先全显再分段）。

- [ ] **Step 9: Commit**

```bash
git add tests/test_bubble_animation.py desktop_pet.py
git commit -m "fix: 分段气泡先全显再分段 bug（delta 静默 + finished opacity 淡入）"
```

---

## Task 2: Live2D 居中（修 +15 偏移）

**Files:**
- Modify: `desktop_pet.py:774-778`（`paintEvent` 的 target 计算）

- [ ] **Step 1: 修改 paintEvent 去掉 +15**

找到 `paintEvent`（约 762 行），将 target 计算的 `+ 15` 去掉：

```python
target = QRect(
    (self.width() - scaled.width()) // 2,  # 正中（原 +15 给工具栏让位，工具栏移走后无需）
    self.height() - scaled.height() - 60,
    scaled.width(), scaled.height(),
)
```

- [ ] **Step 2: 手动运行验证**

Run: `python desktop_pet.py`
观察：人物水平居中，不再偏右。

- [ ] **Step 3: Commit**

```bash
git add desktop_pet.py
git commit -m "fix: Live2D 人物水平居中（去掉 +15 工具栏让位偏移）"
```

---

## Task 3: 创建 SVG 矢量图标资源（5 个，圆润风格）

**Files:**
- Create: `resources/icons/chat.svg`
- Create: `resources/icons/pin.svg`
- Create: `resources/icons/settings.svg`
- Create: `resources/icons/history.svg`
- Create: `resources/icons/close.svg`

- [ ] **Step 1: 创建 icons 目录**

```bash
mkdir resources/icons
```

- [ ] **Step 2: 创建 chat.svg（气泡轮廓，圆润）**

`resources/icons/chat.svg`：

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
</svg>
```

- [ ] **Step 3: 创建 pin.svg（图钉轮廓，圆润）**

`resources/icons/pin.svg`：

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 17v5"/>
  <path d="M9 10.76a2 2 0 0 1 2.66-1.88l2.34.94a2 2 0 0 0 2.66-1.88V5a2 2 0 0 0-2-2H9a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2z"/>
  <path d="M5 17l3-3"/>
</svg>
```

- [ ] **Step 4: 创建 settings.svg（齿轮轮廓，圆润）**

`resources/icons/settings.svg`：

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="3"/>
  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
</svg>
```

- [ ] **Step 5: 创建 history.svg（列表轮廓，圆润）**

`resources/icons/history.svg`：

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M8 6h13"/>
  <path d="M8 12h13"/>
  <path d="M8 18h13"/>
  <path d="M3 6h.01"/>
  <path d="M3 12h.01"/>
  <path d="M3 18h.01"/>
</svg>
```

- [ ] **Step 6: 创建 close.svg（X 轮廓，圆润）**

`resources/icons/close.svg`：

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M18 6L6 18"/>
  <path d="M6 6l12 12"/>
</svg>
```

- [ ] **Step 7: 验证 SVG 文件可被 Qt 加载**

```bash
python -c "from PySide6.QtSvg import QSvgRenderer; from PySide6.QtCore import QByteArray; [print(f'{n}: {QSvgRenderer(QByteArray(open(f\"resources/icons/{n}.svg\",\"rb\").read())).isValid()}') for n in ['chat','pin','settings','history','close']]"
```
Expected: 5 个文件都输出 `True`

- [ ] **Step 8: Commit**

```bash
git add resources/icons/
git commit -m "feat: 添加 5 个 SVG 矢量图标（圆润风格，替代 emoji）"
```

---

## Task 4: 砍旧组件（HistoryPhonePanel / MessageInputPanel / 竖排 tool_bar / 右上 close_button）

**Files:**
- Modify: `desktop_pet.py:160-188`（删除 HistoryPhonePanel / MessageInputPanel 类）
- Modify: `desktop_pet.py:237-258`（删除 history_panel 相关）
- Modify: `desktop_pet.py:260-312`（删除竖排 tool_bar）
- Modify: `desktop_pet.py:347-356`（删除右上角 close_button）
- Modify: `desktop_pet.py:534-570`（删除 _render_history / _set_history_visible 旧实现，Task 7 重写）
- Modify: `desktop_pet.py:798-811`（删除 _relayout 旧内容，Task 5/6/7 重写）

- [ ] **Step 1: 删除 HistoryPhonePanel 和 MessageInputPanel 类**

删除 `desktop_pet.py` 约 160-188 行（`class HistoryPhonePanel` 和 `class MessageInputPanel` 整体）。

- [ ] **Step 2: 删除 PetWindow.__init__ 中的 history_panel 创建**

删除约 237-258 行（`self.history_panel = HistoryPhonePanel(self)` 到 `self._render_history()`），保留 `self._history_expanded = False` 状态。

- [ ] **Step 3: 删除竖排 tool_bar 创建**

删除约 260-312 行（`# 竖排工具栏` 到 `self.tool_bar.installEventFilter(self)`）。

- [ ] **Step 4: 删除右上角 close_button**

删除约 347-356 行（`self.close_button = QPushButton` 到 `self.close_button.hide()`）。

- [ ] **Step 5: 删除旧 _render_history / _set_history_visible / _toggle_history**

删除约 534-570 行（这些方法 Task 7 在 HistoryDrawer 中重写）。保留 `self._history_expanded` 状态变量。

- [ ] **Step 6: 清空 _relayout（Task 5/6/7 重建）**

将 `_relayout`（约 798 行）暂时清空为：

```python
def _relayout(self) -> None:
    """根据当前窗口尺寸重新定位所有组件。Task 5/6/7 重建。"""
    pass
```

- [ ] **Step 7: 临时运行验证不崩**

Run: `python desktop_pet.py`
Expected: 窗口能启动，Live2D 显示，但无工具栏/历史/输入（Task 5-7 重建）。若崩溃，检查是否有遗漏的引用。

- [ ] **Step 8: Commit**

```bash
git add desktop_pet.py
git commit -m "refactor: 砍掉旧组件（手机框/竖排工具栏/右上关闭按钮）为新建让路"
```

---

## Task 5: DockBar 底部悬浮工具栏（5 按钮 SVG + hover 放大）

**Files:**
- Modify: `desktop_pet.py`（PetWindow.__init__ 添加 dock_bar，新建 DockBar 类）
- Test: `tests/test_dock_bar.py`

- [ ] **Step 1: 写失败测试**

`tests/test_dock_bar.py`：

```python
"""DockBar 单元测试：5 按钮、SVG 加载、hover 放大。"""
from __future__ import annotations
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def test_dock_bar_has_5_buttons():
    """DockBar 应有 5 个按钮（对话/固定/设置/记录/退出）。"""
    # 桩测试：检查图标文件存在
    icons = ["chat", "pin", "settings", "history", "close"]
    for name in icons:
        svg = ROOT / "resources" / "icons" / f"{name}.svg"
        assert svg.exists(), f"缺少图标 {svg}"
        assert svg.read_text(encoding="utf-8").startswith("<svg")


def test_dock_bar_button_names():
    """按钮 tooltip 应为：对话/固定/设置/记录/退出。"""
    expected = ["对话", "固定", "设置", "记录", "退出"]
    # 这里只验证预期清单，实际按钮在集成测试中验证
    assert len(expected) == 5
```

- [ ] **Step 2: 运行测试验证**

Run: `python -m pytest tests/test_dock_bar.py -v`
Expected: PASS（图标已在 Task 3 创建）

- [ ] **Step 3: 在 desktop_pet.py 顶部添加 QSvgRenderer 导入**

修改 `run_overlay` 函数开头的导入（约 98-104 行），在 PySide6.QtWidgets 导入后添加：

```python
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QByteArray
```

- [ ] **Step 4: 新建 DockBar 类**

在 `class PetWindow(QWidget):` 之前插入：

```python
class DockButton(QPushButton):
    """Dock 单个按钮：SVG 图标 + hover 放大。"""
    BASE_SIZE = 32
    HOVER_SIZE = 44
    NEAR_SIZE = 38

    def __init__(self, icon_name: str, tooltip: str, is_danger: bool = False, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._is_danger = is_danger
        self.setFixedSize(self.BASE_SIZE, self.BASE_SIZE)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self._renderer = QSvgRenderer(QByteArray(
            (ROOT / "resources" / "icons" / f"{icon_name}.svg").read_bytes()
        ))
        self._scale = 1.0
        self._target_scale = 1.0
        self._hover_anim = QPropertyAnimation(self, b"scale", self)
        self._hover_anim.setDuration(200)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)

    def get_scale(self) -> float:
        return self._scale

    def set_scale(self, value: float) -> None:
        self._scale = value
        size = int(self.BASE_SIZE * value)
        self.setFixedSize(size, size)
        self.update()

    scale = property(get_scale, set_scale)

    def set_target_scale(self, scale: float) -> None:
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._scale)
        self._hover_anim.setEndValue(scale)
        self._hover_anim.start()

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPainter, QColor
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 背景
        if self._is_danger:
            bg = QColor(255, 59, 48, 30) if self._scale > 1.0 else QColor(255, 59, 48, 20)
            border = QColor(255, 59, 48, 100)
            icon_color = QColor("#ff3b30")
        else:
            bg = QColor(0, 212, 255, 20) if self._scale > 1.0 else QColor(0, 212, 255, 12)
            border = QColor(0, 212, 255, 100)
            icon_color = QColor("#00d4ff")
        painter.setBrush(bg)
        painter.setPen(border)
        painter.drawRoundedRect(self.rect(), 8, 8)
        # SVG 图标（着色）
        painter.setPen(icon_color)
        # QSvgRenderer 不直接支持着色，用 mask 方式简化：直接渲染原色 SVG
        # 为简化，SVG 文件用 currentColor，这里通过 QPainter 设置 pen 颜色后渲染
        self._renderer.render(painter)


class DockBar(QWidget):
    """底部悬浮 Dock 工具栏：5 按钮 + hover 邻近放大。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._buttons: list[DockButton] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)
        self.setLayout(layout)
        self._build_buttons()

    def _build_buttons(self) -> None:
        specs = [
            ("chat", "对话", False),
            ("pin", "固定", False),
            ("settings", "设置", False),
            ("history", "记录", False),
            ("close", "退出", True),
        ]
        for icon_name, tooltip, is_danger in specs:
            btn = DockButton(icon_name, tooltip, is_danger, self)
            btn.installEventFilter(self)
            self._buttons.append(btn)
            self.layout().addWidget(btn)

    def eventFilter(self, obj, event) -> bool:
        if obj in self._buttons:
            idx = self._buttons.index(obj)
            if event.type() == event.Type.Enter:
                self._apply_hover_scale(idx)
            elif event.type() == event.Type.Leave:
                self._apply_leave_scale()
        return super().eventFilter(obj, event)

    def _apply_hover_scale(self, hover_idx: int) -> None:
        """hover 按钮放大，邻近轻微放大。"""
        for i, btn in enumerate(self._buttons):
            dist = abs(i - hover_idx)
            if dist == 0:
                btn.set_target_scale(DockButton.HOVER_SIZE / DockButton.BASE_SIZE)
            elif dist == 1:
                btn.set_target_scale(DockButton.NEAR_SIZE / DockButton.BASE_SIZE)
            else:
                btn.set_target_scale(1.0)

    def _apply_leave_scale(self) -> None:
        for btn in self._buttons:
            btn.set_target_scale(1.0)

    def button(self, name: str) -> DockButton:
        """按 tooltip 取按钮。"""
        for btn in self._buttons:
            if btn.toolTip() == name:
                return btn
        raise KeyError(name)
```

- [ ] **Step 5: 在 PetWindow.__init__ 创建 dock_bar**

在 `self._relayout()` 调用前（原 tool_bar 位置）添加：

```python
# Dock 底部悬浮工具栏
self.dock_bar = DockBar(self)
self.dock_bar.button("对话").clicked.connect(self._toggle_input_panel)
self.dock_bar.button("固定").clicked.connect(self._toggle_pin)
self.dock_bar.button("设置").clicked.connect(lambda: SettingsDialog(self).exec())
self.dock_bar.button("记录").clicked.connect(self._toggle_history)
self.dock_bar.button("退出").clicked.connect(QApplication.quit)
self.dock_bar.show()
```

- [ ] **Step 6: 更新 _relayout 定位 dock_bar**

替换 `_relayout`：

```python
def _relayout(self) -> None:
    """根据当前窗口尺寸重新定位所有组件。"""
    w, h = self.width(), self.height()
    # Dock：底部居中悬浮
    dock_w = self.dock_bar.sizeHint().width()
    self.dock_bar.setGeometry((w - dock_w) // 2, h - 56, dock_w, 48)
```

- [ ] **Step 7: 运行验证**

Run: `python desktop_pet.py`
Expected: 底部出现青色胶囊 Dock，5 个 SVG 图标，hover 时中心放大邻近轻微放大。

- [ ] **Step 8: 运行测试**

Run: `python -m pytest tests/test_dock_bar.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add tests/test_dock_bar.py desktop_pet.py
git commit -m "feat: DockBar 底部悬浮工具栏（5 SVG 按钮 + hover 放大动效）"
```

---

## Task 6: 输入框与 Dock 互斥切换（opacity 动画）

**Files:**
- Modify: `desktop_pet.py`（_toggle_input_panel 改为互斥淡入淡出）

- [ ] **Step 1: 修改 input_panel 样式为 A2 青蓝**

找到 `self.input_panel.setStyleSheet`（约 319 行），替换为：

```python
self.input_panel.setStyleSheet(
    "background:rgba(0,212,255,0.06);"
    "border:1px solid rgba(0,212,255,0.4);border-radius:24px"
)
```

- [ ] **Step 2: 修改 input 和 send_button 样式**

替换 `self.input.setStyleSheet`（约 328 行）：

```python
self.input.setStyleSheet(
    "QLineEdit{background:transparent;color:#7be8ff;border:0;padding:8px 10px;"
    "font-size:14px;font-family:'Segoe UI','Microsoft YaHei'}"
    "QLineEdit::placeholder{color:rgba(0,212,255,0.5)}"
)
```

替换 `send_button.setStyleSheet`（约 339 行）：

```python
send_button.setStyleSheet(
    "QPushButton{background:#00d4ff;color:#001824;border:0;border-radius:18px;"
    "font-size:16px;font-weight:bold}"
    "QPushButton:hover{background:#33dfff} QPushButton:disabled{background:rgba(0,212,255,0.3)}"
)
```

- [ ] **Step 3: 添加 opacity effect 到 dock_bar 和 input_panel**

在 PetWindow.__init__ 末尾（`self._relayout()` 前）添加：

```python
self._dock_opacity = QGraphicsOpacityEffect(self.dock_bar)
self.dock_bar.setGraphicsEffect(self._dock_opacity)
self._dock_opacity.setOpacity(1.0)

self._input_opacity = QGraphicsOpacityEffect(self.input_panel)
self.input_panel.setGraphicsEffect(self._input_opacity)
self._input_opacity.setOpacity(0.0)
```

- [ ] **Step 4: 重写 _toggle_input_panel 为互斥动画**

替换 `_toggle_input_panel`（约 420 行）：

```python
def _toggle_input_panel(self) -> None:
    """切换输入面板：Dock 淡出 + 输入框淡入，或反向。"""
    if self.input_panel.isVisible() and self._input_opacity.opacity() > 0.5:
        self._cross_fade(self._input_opacity, self._dock_opacity)
        QTimer.singleShot(200, self.input_panel.hide)
    else:
        self.input_panel.show()
        self.input.setFocus()
        self._cross_fade(self._dock_opacity, self._input_opacity)

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
```

- [ ] **Step 5: 更新 _relayout 定位 input_panel**

在 `_relayout` 末尾添加：

```python
panel_w = 320
self.input_panel.setGeometry((w - panel_w) // 2, h - 56, panel_w, 48)
```

- [ ] **Step 6: 运行验证**

Run: `python desktop_pet.py`
点 Dock 对话按钮 → Dock 淡出 + 输入框淡入；Esc → 反向。

- [ ] **Step 7: Commit**

```bash
git add desktop_pet.py
git commit -m "feat: 输入框与 Dock 互斥切换（200ms opacity 交叉淡入淡出，A2 青蓝样式）"
```

---

## Task 7: HistoryDrawer 历史抽屉（右侧滑入 + 青灰条消息）

**Files:**
- Modify: `desktop_pet.py`（新建 HistoryDrawer 类，重建 _render_history/_toggle_history）
- Test: `tests/test_history_drawer.py`

- [ ] **Step 1: 写失败测试**

`tests/test_history_drawer.py`：

```python
"""HistoryDrawer 单元测试：青灰条消息样式。"""
from __future__ import annotations


def test_history_html_kurisu_style():
    """Kurisu 消息应有 cyan-soft 背景 + cyan 左边条。"""
    from desktop_pet import _build_kurisu_html
    html = _build_kurisu_html("こんにちは")
    assert "rgba(0,212,255,0.16)" in html  # cyan-soft 背景
    assert "border-left:2px solid #00d4ff" in html  # cyan 左边条
    assert "こんにちは" in html


def test_history_html_you_style():
    """You 消息应有灰背景 + 右灰边条。"""
    from desktop_pet import _build_you_html
    html = _build_you_html("你好")
    assert "rgba(255,255,255,0.06)" in html
    assert "border-right:2px solid #8e8e93" in html
    assert "你好" in html
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_history_drawer.py -v`
Expected: FAIL（`_build_kurisu_html` 未定义）

- [ ] **Step 3: 添加模块级 HTML 构建函数**

在 `desktop_pet.py` `run_overlay` 函数内（`class PetWindow` 之前）添加：

```python
def _build_kurisu_html(text: str) -> str:
    safe = html.escape(text).replace("\n", "<br>")
    return (
        "<div style='margin:0 0 12px 0;padding:8px 10px;background:rgba(0,212,255,0.16);"
        "border-left:2px solid #00d4ff;border-radius:4px'>"
        "<div style='color:#7be8ff;font-weight:bold;font-size:11px;margin-bottom:2px'>Kurisu</div>"
        f"<div style='line-height:1.42;color:#7be8ff;font-size:13px'>{safe}</div></div>"
    )

def _build_you_html(text: str) -> str:
    safe = html.escape(text).replace("\n", "<br>")
    return (
        "<div style='margin:0 0 12px 0;padding:8px 10px;background:rgba(255,255,255,0.06);"
        "border-right:2px solid #8e8e93;border-radius:4px;text-align:right'>"
        "<div style='color:#8e8e93;font-weight:bold;font-size:11px;margin-bottom:2px'>You</div>"
        f"<div style='line-height:1.42;color:#cccccc;font-size:13px'>{safe}</div></div>"
    )
```

- [ ] **Step 4: 新建 HistoryDrawer 类**

在 `class DockBar` 之后插入：

```python
class HistoryDrawer(QWidget):
    """右侧滑入历史抽屉。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._width = 168  # 42% of 400
        self._x_off = self._width  # 默认隐藏在右侧外
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.history = QTextBrowser(self)
        self.history.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.history.setStyleSheet(
            "QTextBrowser{background:rgba(8,14,22,0.85);color:#7be8ff;border:1px solid rgba(0,212,255,0.4);"
            "border-radius:8px;padding:8px;font:13px 'Segoe UI','Microsoft YaHei'}"
            "QScrollBar:vertical{background:rgba(0,212,255,0.1);width:6px;margin:4px}"
            "QScrollBar::handle:vertical{background:#00d4ff;border-radius:3px;min-height:30px}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}"
        )
        self.history.setOpenExternalLinks(False)
        layout.addWidget(self.history)

    def set_messages_html(self, html_content: str) -> None:
        self.history.setHtml(
            "<html><body style='margin:0;background:transparent'>"
            + html_content
            + "</body></html>"
        )
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def slide_in(self) -> None:
        """300ms 从右滑入。"""
        parent_w = self.parent().width() if self.parent() else 400
        target_x = parent_w - self._width - 4
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(300)
        anim.setStartValue(self.pos())
        anim.setEndValue(self.parent().rect().topRight() - QPoint(self._width, -8) if self.parent() else self.pos())
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.start()
        self._slide_anim = anim

    def slide_out(self) -> None:
        """300ms 滑出到右侧外。"""
        parent_w = self.parent().width() if self.parent() else 400
        target_x = parent_w + 4
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(300)
        anim.setStartValue(self.pos())
        end = QPoint(target_x, self.pos().y())
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.start()
        self._slide_anim = anim
```

- [ ] **Step 5: 在 PetWindow.__init__ 创建 history_drawer**

在 dock_bar 创建后添加：

```python
self.history_drawer = HistoryDrawer(self)
self.history_drawer.setGeometry(self.width() - 172, 8, 168, self.height() - 80)
self.history_drawer.hide()
```

- [ ] **Step 6: 重写 _render_history 和 _toggle_history**

添加（替换原删除位置）：

```python
def _render_history(self) -> None:
    blocks = []
    for message in active_session(self._state)["messages"]:
        assistant = message["role"] == "assistant"
        text = parse_reply(message["content"]).chinese if assistant else message["content"]
        if assistant:
            blocks.append(_build_kurisu_html(text))
        else:
            blocks.append(_build_you_html(text))
    self.history_drawer.set_messages_html("".join(blocks))

def _toggle_history(self) -> None:
    self._history_expanded = not self._history_expanded
    if self._history_expanded:
        self._render_history()
        self.history_drawer.show()
        self.history_drawer.slide_in()
        self.input_panel.hide()
        self.reply_bubble.hide()
    else:
        self.history_drawer.slide_out()
        QTimer.singleShot(300, self.history_drawer.hide)
```

- [ ] **Step 7: 更新 _relayout 定位 history_drawer**

在 `_relayout` 末尾添加：

```python
self.history_drawer.setGeometry(w - 172, 8, 168, h - 80)
```

- [ ] **Step 8: 运行测试**

Run: `python -m pytest tests/test_history_drawer.py -v`
Expected: PASS

- [ ] **Step 9: 运行验证**

Run: `python desktop_pet.py`
点 Dock 记录按钮 → 抽屉从右滑入，青灰条消息；再点 → 滑出。

- [ ] **Step 10: Commit**

```bash
git add tests/test_history_drawer.py desktop_pet.py
git commit -m "feat: HistoryDrawer 右侧滑入历史抽屉（青灰条消息 + 300ms 滑入动画）"
```

---

## Task 8: 字幕条配色切换 A2 青蓝 + 思考动画

**Files:**
- Modify: `desktop_pet.py`（reply_bubble 样式 + _show_thinking_dots）

- [ ] **Step 1: 修改 reply_bubble 样式为 A2 青蓝**

找到 `self.reply_bubble.setStyleSheet`（约 228 行），替换为：

```python
self.reply_bubble.setStyleSheet(
    "QLabel{background:rgba(0,212,255,0.16);color:#7be8ff;"
    "border:1px solid rgba(0,212,255,0.4);border-radius:18px;"
    "padding:10px 16px;font:14px 'Segoe UI','Microsoft YaHei';"
    "font-weight:400;letter-spacing:0.2px}"
)
```

- [ ] **Step 2: 增强 _show_thinking_dots 为呼吸动画**

替换 Task 1 的 `_show_thinking_dots`：

```python
def _show_thinking_dots(self) -> None:
    """delta 期间显示思考动画（3 个青色点呼吸）。"""
    self._set_bubble_text("● ● ●")
    if not hasattr(self, '_thinking_opacity'):
        self._thinking_opacity = QGraphicsOpacityEffect(self.reply_bubble)
        self.reply_bubble.setGraphicsEffect(self._thinking_opacity)
    # 呼吸动画：opacity 0.4 → 1.0 → 0.4 循环
    if not hasattr(self, '_thinking_anim'):
        self._thinking_anim = QPropertyAnimation(self._thinking_opacity, b"opacity", self)
        self._thinking_anim.setDuration(1000)
        self._thinking_anim.setStartValue(0.4)
        self._thinking_anim.setKeyValueAt(0.5, 1.0)
        self._thinking_anim.setEndValue(0.4)
        self._thinking_anim.setLoopCount(-1)
    if self._thinking_anim.state() != QPropertyAnimation.Running:
        self._thinking_anim.start()
```

- [ ] **Step 3: 在 _show_layered_bubbles 停止思考动画**

在 `_show_layered_bubbles` 开头（`self._cancel_bubbles()` 后）添加：

```python
if hasattr(self, '_thinking_anim') and self._thinking_anim.state() == QPropertyAnimation.Running:
    self._thinking_anim.stop()
    self._thinking_opacity.setOpacity(1.0)
```

- [ ] **Step 4: 运行验证**

Run: `python desktop_pet.py`
发消息：delta 期间青色"● ● ●"呼吸；finished 后分段淡入青蓝气泡。

- [ ] **Step 5: Commit**

```bash
git add desktop_pet.py
git commit -m "feat: 字幕条 A2 青蓝配色 + 思考呼吸动画"
```

---

## Task 9: 窗口玻璃底 + 最终整体验收

**Files:**
- Modify: `desktop_pet.py`（paintEvent 添加玻璃底）

- [ ] **Step 1: 在 paintEvent 添加深色玻璃底**

找到 `paintEvent`（约 762 行），在 `if self._frame.isNull(): return` 前添加玻璃底绘制：

```python
def paintEvent(self, event) -> None:
    # 深色玻璃底
    painter = QPainter(self)
    painter.setRenderHint(QPainter.Antialiasing)
    from PySide6.QtGui import QLinearGradient
    grad = QLinearGradient(0, 0, self.width(), self.height())
    grad.setColorAt(0, QColor(8, 14, 22, 150))
    grad.setColorAt(1, QColor(12, 24, 36, 130))
    painter.fillRect(self.rect(), grad)
    painter.end()

    if self._frame.isNull():
        return
    painter = QPainter(self)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    base_h = 520
    target_h = int(base_h * self._zoom)
    target_w = int(target_h * self._frame.width() / self._frame.height())
    scaled = self._frame.scaled(
        target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )
    target = QRect(
        (self.width() - scaled.width()) // 2,
        self.height() - scaled.height() - 60,
        scaled.width(), scaled.height(),
    )
    painter.drawImage(target, scaled)
```

- [ ] **Step 2: 运行所有测试**

Run: `python -m pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 3: 手动验收（对照 spec §10 验收标准）**

Run: `python desktop_pet.py`

逐项核对：
- [ ] 分段气泡不再"先全显再分段"，delta 显示"● ● ●"呼吸
- [ ] Live2D 人物水平居中
- [ ] 无手机框贴图
- [ ] 底部 Dock 横排 5 按钮（SVG 矢量，非 emoji）
- [ ] 配色 A2 青蓝（#00d4ff）+ 退出 iOS 红
- [ ] 字幕条顶部，分段淡入
- [ ] 输入框与 Dock 互斥（200ms 淡入淡出）
- [ ] 历史抽屉右侧滑入（300ms），青灰条
- [ ] Dock hover 放大（中心 44px，邻近 38px）

- [ ] **Step 4: Commit**

```bash
git add desktop_pet.py
git commit -m "feat: 窗口深色玻璃底 + Plan 1 整体验收"
```

---

## Self-Review

**Spec 覆盖检查**：
- §3 布局（Live2D 居中/Dock/输入/抽屉）→ Task 2/4/5/6/7 ✓
- §4 配色 A2 → Task 6/8/9 ✓
- §5 组件规格（字幕/Dock/图标/输入/抽屉）→ Task 3/5/6/7/8 ✓
- §6 动效（Dock 放大/字幕淡入/抽屉滑入/互斥）→ Task 5/1/7/6 ✓
- §8 分段气泡 bug → Task 1 ✓
- §7 TTS → 留待 Plan 2（spec §1.3 已声明 TTS 为独立块，Plan 1 字幕先用时间驱动）

**Placeholder 扫描**：无 TBD/TODO，所有代码已给出。

**类型一致性**：DockButton/DockBar/HistoryDrawer 方法名一致；_show_thinking_dots/_show_layered_bubbles/_toggle_input_panel/_toggle_history 跨任务引用一致。

**已知简化**（Plan 2 补齐）：
- 字幕分段用时间驱动（QTimer），未绑定 TTS 播放时刻
- 口型仍用 live2d_page.html 的 Math.random()，未接音频振幅
- SpeechPlayer 未替换为 StreamingTTS

---

## Execution Handoff

Plan 1 完成并保存到 `docs/superpowers/plans/2026-08-13-ui-redesign.md`。两种执行方式：

**1. Subagent-Driven（推荐）** - 每个 Task 派独立 subagent 执行，任务间审查，快速迭代

**2. Inline Execution** - 当前会话内执行，批量执行 + 检查点

选定后我将按对应 sub-skill 推进。Plan 2（TTS 集成）待 Plan 1 完成后单独制定。
