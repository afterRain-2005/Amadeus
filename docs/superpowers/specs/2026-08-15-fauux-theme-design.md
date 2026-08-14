# fauux 双主题系统设计（WIRED Rose / Aqua）

> 日期：2026-08-15
> 状态：设计已获用户批准（预览两轮迭代后定稿）
> 参考：https://fauux.neocities.org/ 及其 stylesheet.css（真实抓取数据）

## 1. 背景与目标

用户要求学习 fauux.neocities.org（《Serial Experiments Lain》同人站，Neocities 十年老站）
的 UI 设计，并将其应用于 Amadeus 桌宠。经预览迭代确认：

- **保留现有青蓝主题（Aqua）**，新增 fauux 风格主题（Wired）
- 设置面板下拉切换，**实时生效**，持久化到 config.json
- 图标运行时复染（单套 SVG 源文件）
- 立绘裸站桌面（窗口透明，仅面板有底色）
- 中文衬线宋体（原教旨 fauux 排版）
- 图标背景透明（不出现灰色按钮底）

## 2. 真实 fauux 设计数据（证据）

来源：`https://fauux.neocities.org/stylesheet.css`（2026-08-15 抓取）

| 元素 | 值 | 意义 |
|---|---|---|
| 主色 | `#d2738a` | 玫瑰粉紫（链接/b/hr/footer） |
| 正文 | `#c1b492` | 米黄做旧纸色 |
| 标题字体 | Times, "Times New Roman", serif | 衬线体 |
| 字距 | `letter-spacing: 8px` | 标志性大字距 |
| 质感 | `image-rendering: pixelated`、dither GIF 背景 | 颗粒抖动 |
| 动画 | `blink`（50% 变黑消失）、`wiredB`（米色+粉紫辉光） | 缓慢诡异闪烁 |
| 容器 | 透明底 + bg-rip-02.gif 纹理 + 黑色 box-shadow | 深色纹理面板 |
| 装饰 | `⌈ ⌉ ⌊ ⌋` 角括号 | navbar 四角 |

## 3. 主题令牌

### ui/theme.py 核心数据结构

```python
@dataclass
class Theme:
    id: str                 # "aqua" | "wired"
    name: str               # "青蓝 Aqua" | "WIRED Rose"
    # —— 颜色令牌 ——
    accent: str             # 强调/边框/hover 填充
    accent_soft: str        # 半透明强调（面板底色调）
    text: str               # 正文
    text_dim: str           # 次级文字/动作括号
    danger: str             # 退出/挂断
    panel_bg: str           # 面板底（QSS background）
    label_text: str         # AMADEUS/YOU 名牌色
    wave_color: QColor      # 波形条
    # —— 排版 ——
    font_stack: str         # 字体族
    letter_spacing: str     # QSS letter-spacing
    # —— 图标复染映射（旧 stroke -> 新 stroke）——
    icon_recolor: dict[str, str]
    # —— 纹理 ——
    texture: str | None     # QSS background-image url；None=无纹理
    # —— 各组件 QSS 模板 ——
    bubble_qss / input_qss / dock 按钮配色 / drawer_qss / call_qss / settings_qss
```

### 两主题令牌值

| 令牌 | Aqua（现状） | Wired（新增） |
|---|---|---|
| accent | `#00d4ff` | `#d2738a` |
| accent_soft | `rgba(0,212,255,0.16)` | `rgba(210,115,138,0.22)` |
| text | `#a0eaff` | `#c1b492` |
| text_dim | `#8e8e93` | `#8a7f63` |
| danger | `#ff3b30` | `#7a3040` |
| panel_bg | `rgba(8,14,22,0.92)` | `#171114` + 纹理 |
| label(Kurisu) | `#7be8ff` | `#d2738a` |
| label(You) | `#8e8e93` | `#8a7f63` |
| font | `'Segoe UI','Microsoft YaHei'` | `'Times New Roman','SimSun',serif` |
| letter-spacing | 无 | `1px`（中文气泡）/`3px`（标签） |
| icon normal | `#00d4ff` | `#c1b492` |
| icon danger | `#ff3b30` | `#d2738a` |
| texture | 无 | `resources/textures/dither_rose.png` 16×16 平铺 |
| 角装饰 | 无 | 气泡 `⌈⌉⌊⌋` 四角 QLabel |
| 气泡圆角 | `18px` | `0px`（直角+角括号） |

## 4. 组件改造清单

### 4.1 新增 ui/theme.py
- `THEMES: dict[str, Theme]`（"aqua"/"wired"）
- `get_theme(theme_id) -> Theme`（未知 id 回落 aqua）
- `recolor_svg_bytes(svg_bytes, mapping) -> QByteArray`：字节串替换 stroke 色值
- `generate_dither_texture(path)`：一次性生成 16×16 抖动纹理 PNG
  （程序化 QPainter 绘制 Bayer 4×4 网点 + 每 4px 一条 1px 暗扫描线，
  前景色 #d2738a/22%、#c1b492/10%、黑/50%；若文件已存在则跳过）

### 4.2 desktop_pet.py
- `PetWindow.__init__` 读取 `config["theme"]`（默认 "aqua"），调用 `_apply_theme()`
- `_apply_theme(theme_id)`：
  1. 存储 `self._theme`
  2. `reply_bubble` / `input_panel` / `input` / `collapse_button` / `send_button`
     重设 QSS（从 theme 取）
  3. Wired 时给气泡挂 4 个角括号 QLabel（QLabel 以 `⌈⌉⌊⌋` 为文本，
     rose 色，位于气泡四角外扩 7px）；Aqua 时隐藏之
  4. DockBar 重建按钮（DockButton 构造改为接收 Theme，paintEvent 用
     theme.accent/danger；SVG bytes 经 recolor_svg_bytes 复染）
  5. HistoryDrawer 重设 QSS（含 Kurisu/You 消息 HTML 的名牌色——
     `_build_kurisu_html/_build_you_html` 改为读当前主题色）
  6. 保存 `config["theme"]`
- `_build_kurisu_html/_build_you_html` 提为模块级函数并参数化颜色
  （现已是模块级，加 theme 参数或读全局 current_theme）
- DockButton.paintEvent：`QColor(0,212,255,*)` → `theme.accent` 系；
  danger `QColor(255,59,48,*)` → `theme.danger` 系

### 4.3 ui/widgets/call_view.py
- `_SvgButton` 构造接收 theme，paintEvent 用 theme 色画底/边
- `WaveformCanvas.paintEvent` 填充色 → `theme.wave_color`
- 状态条/字幕/时长 QSS → theme 模板
- `set_phase` 的 dot 颜色映射：Wired 时 `#ffb03a→#c1b492`、
  `#34c759→#d2738a`、`#8e8e93→#8a7f63`（保持"状态可辨"语义）

### 4.4 ui/settings_dialog.py
- 新增「外观」页（置于 Tab 首位）：
  - QComboBox「界面主题」：青蓝 Aqua / WIRED Rose
  - currentIndexChanged 触发回调 `on_theme_changed(theme_id)`
    （由 PetWindow 注入，调 `_apply_theme` 实时生效；对话框自身 QSS
    同步重刷）
- `_save` 写入 `config["theme"]`

### 4.5 SVG 图标（不新增文件）
- 现有 11 个 Feather 风格 SVG 的 stroke 只有两种值：`#00d4ff`、`#ff3b30`
- 复染映射（Wired）：`{"#00d4ff": "#c1b492", "#ff3b30": "#d2738a"}`
- Aqua 主题映射为恒等（不复染，直接原样渲染）

## 5. 实时切换时序

```
SettingsDialog「外观」下拉选择 "wired"
  → on_theme_changed("wired")（PetWindow 注入的闭包）
  → PetWindow._apply_theme("wired")
      → reply_bubble.setStyleSheet(theme.bubble_qss) …全部控件
      → 角括号 QLabel show()
      → dock_bar.rebuild(theme)（按钮销毁重建，复染图标）
      → history_drawer.setStyleSheet(...)
      → call_view.apply_theme(theme)（通话中也可切）
  → save_config({"theme": "wired"})
```

启动时：`__init__` 读 config["theme"] → `_apply_theme(默认 aqua)` → 正常初始化。

## 6. 错误处理

- config["theme"] 值非法 → `get_theme` 回落 "aqua"，日志提示
- 纹理 PNG 生成失败（目录只读）→ 面板退化为纯色 `#171114`，不阻塞启动
- SVG 复染后 bytes 非法（理论不发生，仅替换色值字符串）→
  QSvgRenderer 构造 try/except，失败用原始 bytes

## 7. 测试（pytest，沿用 tests/ 目录风格）

- `tests/test_theme.py`：
  - `get_theme("wired")` 令牌值正确；`get_theme("xxx")` 回落 aqua
  - `recolor_svg_bytes`：含 `#00d4ff` 的 bytes → 输出含 `#c1b492` 且不含旧值
  - `generate_dither_texture`：生成文件存在、16×16、幂等
- `tests/test_dock_bar.py` 补充：`rebuild(theme)` 后按钮数不变、tooltip 不变
- 回归：`test_bubble_animation / test_status_text / test_phone_dock_button`
  全量通过（QSS 变更不影响信号/槽逻辑）

## 8. 不做（YAGNI）

- 不做用户自定义主题编辑器（只内置两套）
- 不做主题热重载配置文件
- 不给 Aqua 主题加纹理/角括号（保持现状观感）
- 不改 Live2D 渲染管线（主题只影响 Qt 覆盖层）
- 不做设置面板以外的切换入口（Dock 快捷钮方案被否）
- 通话态 CallView 的字体不换衬线（通话字幕需高可读性，保持雅黑，
  仅换色——与用户"衬线原教旨"决策不冲突，该决策针对气泡/面板文案）

## 9. 待定风险

- QSS `letter-spacing` 在 QLabel 上 Windows 实测渲染兼容性——
  实施时先验证，若异常则 Wired 气泡退化为手动空格间隔
- 抖动纹理在高分屏（150% DPI）下的颗粒观感——纹理按物理像素绘制，
  必要时提供 8×8/16×16 两档
