# Amadeus-py 项目教训总结

> 每轮对话结束时记录 5 条最重要的教训。开始项目时首先查看本文件。

## 2026-08-09 全盘代码审查

### 1. 实例变量必须在 __init__ 中初始化，否则运行时 AttributeError
desktop_pet.py 中 `_inactivity_timer` / `_user_pos` / `_was_desktop` / `_snap_anim` 四个变量分散在方法内赋值，
但 __init__ 未声明。导致：发送消息（_send 调用 _inactivity_timer.start）→ 崩溃；拖拽释放（mouseReleaseEvent
读 _user_pos）→ 崩溃；首次 _animate_to（读 _snap_anim）→ 崩溃。
**教训**：Qt 自定义控件的所有状态变量必须在 __init__ 中显式初始化（哪怕只是 `= None`），
不能用"首次使用时创建"的懒加载模式，因为 Qt 事件回调的触发顺序不可预测。

### 2. 跨进程通信文件路径必须用同一个真相源
pet_controller.py 用 `storage.APP_DIR`（冻结模式=exe同目录/data/），但 desktop_pet.py 用 `ROOT/data`
（冻结模式=sys._MEIPASS 临时解压目录）。打包后两个路径不一致，emotion/speaking 命令传不到 renderer。
**教训**：PyInstaller onefile 模式下，`sys._MEIPASS` 是只读临时解压目录，子进程之间共享状态文件
必须用 `Path(sys.executable).parent / "data"`（exe 同目录），并由 storage 模块统一计算，其他模块只 import 不重算。

### 3. 死代码不删会持续误导维护者
chat_window.py / companion_window.py / codex_bridge.py / hermes_process.py / run_direct_agent /
_on_chat_logout 都是死代码，但功能完整，容易让人误以为它们还在跑。尤其 companion_window 用 QWebEngineView，
desktop_pet 用 webview+PySide6 覆盖层，两套方案并存让人困惑。
**教训**：替换主流程时，旧实现要么删掉，要么在文件顶部加 [deprecated] 注释说明被什么替代、为何保留。
本轮统一加了 [deprecated] 标记。

### 4. QShortcut 与 win32api 全局热键不能重复注册同一个组合键
desktop_pet.py 同时用 `QShortcut("Ctrl+Space")` 和 `_poll_global_hotkey` 的 `GetAsyncKeyState` 监听
Ctrl+Space，导致每次按下触发两次 `_focus_input`。
**教训**：Qt 的 QShortcut 仅在窗口激活时有效；win32api GetAsyncKeyState 是全局的。两者目的不同，
不能注册同一组合键。全局热键只用 win32api 版本，窗口内快捷键（如 Escape）用 QShortcut。

### 5. QFontDatabase.addApplicationFont 需要字体文件路径，不是字体族名
main.py 原代码 `QFontDatabase.addApplicationFont("Cinzel")` 永远返回 -1，因为 "Cinzel" 是字体族名
不是 .ttf/.otf 文件路径。该调用静默失败，不会报错但也不会加载字体。
**教训**：Qt 字体加载 API 的参数是文件系统路径（绝对路径或相对路径），字体族名要在加载成功后
用 `QFont("Cinzel")` 引用。若系统已安装该字体，无需 addApplicationFont，直接用 QFont 即可；
若未安装，需打包 .ttf 文件并用 addApplicationFont 加载。

## 2026-08-13 产品化方向头脑风暴

### 1. "商业项目"一词必须先澄清语义——未必指变现
用户说"希望成为真正的商业项目，太玩具了"，初看像要商业化变现。逐句追问后发现实为
**追求商业级产品质感（美观/完成度/功能性），自用不卖钱**。若直接按"变现"路线设计会跑偏
（去 IP、加计费、加账号体系全是浪费）。
**教训**：遇到"商业""产品化"等模糊词，先用提问工具澄清语义（变现 vs 质感 vs 规模化），
不要自行假设。

### 2. 多角色机制常是过度设计——问清"你到底要几个角色"
原项目有红莉栖/真帆/真由理三角色 + 硬编码账号密码登录。追问后用户只要红莉栖、不要登录。
砍掉多角色机制与登录流程，产品瞬间聚焦，工程量大降。
**教训**：角色/账号/多租户这类"看起来像标配"的机制，对自用项目可能是纯负担。设计前必问
"你实际需要几个角色/要不要登录"，别照搬通用产品模板。

### 3. 头脑风暴必须先读 lessons.md，复用已有教训做诊断
本次诊断"玩具感"时直接复用了 8-09 教训：文件轮询 IPC（教训2）、死代码（教训3）、
硬编码字体（教训5）。读 lessons 让诊断又快又准，不重复踩坑。
**教训**：开始任何设计/审查工作前，lessons.md 是第一手资料，先读再用。

### 4. PowerShell 不支持 bash heredoc，git 多行 commit 用多个 -m
用 `git commit -m "$(cat <<'EOF' ... EOF)"` 在 PowerShell 报错（`<<` 是保留运算符）。
正确做法：多个 `-m "段落"`，每个 -m 成一个段落；或写临时文件用 `git commit -F`。
**教训**：本环境是 PowerShell 不是 bash，所有 shell 命令按 PowerShell 语法写；
heredoc/`&&`/`||` 都不可用，链式用 `;`。

### 5. 大范围改造设计应显式列 YAGNI 与待定项，防范围蔓延
产品化涉及四块（视觉/功能/工程/定位），易越做越大。spec 里显式列"不做（YAGNI）"清单
（不做平台化、不做自动更新、不做多角色、不做登录、不变现）和"待定/风险"清单
（资产清理、嵌入模型选型、IPC 重构风险），把边界钉死。
**教训**：设计文档除写"做什么"，必须同等力度写"不做什么"和"待定项"，否则实施期必蔓延。

## 2026-08-13 UI 重做 brainstorming

### 1. "太丑了"是整体判断，不必逐项追问，直接提多方向让用户选
问"玩具感来源"时用户答"太丑了"——整体感受而非具体指向。此时继续逐项追问会拖慢，
应直接提 2-3 个整体重做方向（A 极简/B 终端/C 卡片）配 mockup 让用户选。
**教训**：当用户给整体判断而非具体问题时，跳过澄清直接给方案对比更高效。

### 2. 调研同类项目能快速校准设计方向，发现核心架构
用户提 Lucas1479/Amadeus 仓库后，WebFetch 抓取 README 发现其核心是"流式表达时间线"——
TTS 分句流式播放 + 口型/表情/字幕绑定到播放时刻 + 可打断。这直接启发了我们的 TTS 架构设计
（StreamingTTS + sentence_start 信号驱动字幕 + amplitude 信号驱动口型）。
**教训**：用户提到的参考项目必须实地抓取，README/架构图是设计灵感的金矿，别凭名字猜。

### 3. 素材评估要实地检查，不能假设
GPT-SoVITS 少样本推理需 5-30 秒干净参考音频。voice_sample.mp3 未知时长，不能假设够用。
用 Python wave/PowerShell Shell.Application 实测：15 秒，正好落在黄金区间。系统通知音
21 个共 43 秒但全是碎句，不适合。若假设"应该够"或"可能不够"都会误导设计。
**教训**：涉及素材/硬件/依赖的客观条件，必须用工具实测，不能假设。

### 4. emoji 图标廉价感强，矢量 SVG 是商业级 UI 基本要求
Dock 工具栏用 emoji（💬📌⚙☰×）被用户直指"很廉价"。改 SVG 矢量线条（Phosphor/Tabler 圆润款）
后立刻有商业级质感。emoji 还有跨平台渲染不一、放大锯齿、颜色不可控等问题。
**教训**：商业级 UI 不用 emoji 作图标，统一用 SVG 矢量（Qt QSvgRenderer 原生支持，无新依赖）。

### 5. memory 里的旧约定可能被新设计推翻，要显式确认冲突点
memory 记录"回复气泡顶部居中"，但 Lucas1479 风格是底部字幕更沉浸。这是潜在冲突。
没有默认采用新设计，而是显式提"顶部 vs 底部"让用户选，用户选保留顶部。
若自行假设"既然学 Lucas1479 就改底部"会违反用户既有约定。
**教训**：新设计与 memory/project_memory 冲突时，必须显式列出冲突点让用户拍板，不自行覆盖。
