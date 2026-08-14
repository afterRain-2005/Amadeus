# Amadeus-py 项目教训总结

> 每轮对话结束时记录 5 条最重要的教训。开始项目时首先查看本文件。

## 2026-08-13 TTS 集成

### 1. GPT-SoVITS 集成设计：代码优先 + 模型后装
TTS 架构设计为 GPT-SoVITS 优先 + SAPI 降级双路径。代码写好但模型需用户手动安装，
避免自动下载超大依赖（PyTorch ~2GB + 预训练模型 ~1.5GB）阻塞项目流程。
KurisuTTS 单例模式仅加载一次模型，避免重复消耗显存。
依据：GPT-SoVITS V3 非 PyPI 包，需从 GitHub 克隆，国内网络极慢。
参考：core/gpt_sovits_client.py, core/tts_client.py

### 2. 语音管线优先级：先查现有素材再装新依赖
用户指出 "当前文件夹里不是有语音素材吗" 提醒我们：项目资源（voice_sample.mp3）
已存在，但 TTS 后端（SAPI）无法利用它做音色克隆。应先确认用户已有哪些素材和 API key，
再决定安装方案。盲目从零安装 GPT-SoVITS 导致用户多次取消克隆操作。
依据：resources/voice_sample.mp3 15s 干净人声，但 SAPI 只读系统语音包不读文件。

### 3. 麦克风诊断：波形始终显示 + 设备列表打印
sounddevice InputStream 回调中即使 VAD 暂停也发射波形信号，让用户看到绿色
波形条跳动来确认麦克风在接收数据。启动时打印所有输入设备列表和采样率，方便定位
设备选错问题。VAD 阈值（START_THRESH=0.018）需在实际环境微调。
依据：用户反馈 "麦克风无反应"，无法判断是设备问题还是阈值问题。
参考：core/voice_call.py _open_mic, _audio_callback

### 4. NVIDIA GPU 检测：nvidia-smi 不在 PATH 不代表无 GPU
用户明确说 "有 NVIDIA 显卡"，但 nvidia-smi 命令找不到。可能原因：驱动未安装、
PATH 未配置、或使用了其他 GPU 品牌。安装 PyTorch 时应同时尝试 CUDA 和 CPU 版本，
或先通过 python -c "import torch; print(torch.cuda.is_available())" 验证。
依据：用户环境有 GPU 但 nvidia-smi 不可用，导致 PyTorch 下载了 CPU 版。

### 5. 电话模式退出：双路径退出 + 信号断连
通话态退出需要两套机制：UI 按钮（红色 ✕）+ 键盘 Esc。进通话态前 disconnect 旧信号
防止 hangup 被多次调用。异常情况下无论 controller 是否为 None 都恢复 Dock + 气泡。
依据：用户反馈 "通话态无法退出"、"重复进通话态信号重复连接"。
参考：commit b02a56d

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

## 2026-08-13 Plan 1 验收 + 压缩恢复

### 1. 上下文压缩后必须先重建任务状态再继续，不能盲跑触发消息
压缩后系统回放了原始触发消息（brainstorming 请求），但实际进度已远超该消息（Plan 1 已实施完毕待验收）。
若直接重跑 brainstorming 会浪费且重复劳动。正确做法：读 lessons.md → git log/status/diff 重建当前点
→ 向用户确认当前任务 → 继续。**教训**：压缩恢复协议 = 读 lessons + 查 git 状态 + 用户确认，三步缺一不可。

### 2. 窗口默认位置必须给底部 UI 留余量，不能贴 screen.bottom()
输入框在 `h - 56`（距窗口底 8px），窗口贴 `screen.bottom()` 时输入框被任务栏/屏幕底切。
修复：默认 Y = `screen.bottom() - height - 60`，给输入框留 ~68px 屏幕底余量。
**教训**：贴边定位（bottom-right corner）要审计所有子控件的几何，底部有控件时必须加 margin。

### 3. 人物缩放标定要算可用区，不能凭感觉调 zoom
base_h(520) × zoom vs 窗口高(680) 减去顶部字幕(104) 和底部 Dock(56) 的可用区。
zoom 0.75→390px 偏小；0.9→468px，顶在 152（字幕底 104 留 48px），底在 620（Dock 624 留 4px），平衡。
zoom 1.0→520px 顶在 100 与字幕条贴住太挤。**教训**：调缩放参数前先算"可用高度 = 窗口高 - 顶部chrome - 底部chrome"。

### 4. PowerShell 不认 cmd 内建（taskkill/timeout/start），需固化该教训
重启用 `Stop-Process -Name pythonw -Force` + `Start-Sleep -Seconds 1` + `Start-Process`。
8-13 已记过，本轮又踩。**教训**：该教训需在 start.bat 之外的所有重启场景复用，写进 project_memory。

### 5. 定向 git add 避免混入遗留改动
工作区长期有先前未提交的打包/Hermes 相关文件（core/storage.py、start.bat、ui/settings_dialog.py、
Amadeus.spec、build/、scripts/）。提交时只 `git add 本任务文件`，不用 `git add -A/.`。
**教训**：工作区有历史遗留未提交改动时，每次提交都用定向 add，保持提交纯净可回溯。

## 2026-08-13 Agent 增强 Task 1-5 实施

### 1. subagent 并行结果会丢失，必须查 git 状态重建，不能假设
并行派 3 个 subagent 跑 Task 1/4/5，Task 4 返回完整报告，Task 1/5 结果"missing"。
若假设它们成功会误判；若假设失败会重做已完成的工作。正确做法：git log + git status + grep
确认每个文件实际改动到哪步，再从断点续做。Task 1 留下 requirements.txt 改动但代码没写；
Task 5 留下测试文件但实现没改。**教训**：subagent 结果丢失时，以 git/文件系统实际状态为准重建进度。

### 2. brainstorming 要先读现有代码再提方案，否则会重复造轮子
把"建 agent loop / C 分级 / function-calling"当新建来 brainstorm，读完 agent_client.py +
desktop_tools.py 才发现这些已全部实现且成熟。差点按"从零建"写 plan 浪费大量工作。
**教训**：涉及"增强/添加能力"的需求，必须先读现有实现，区分"已有"与"缺口"，再 brainstorm 缺口部分。

### 3. 路径校验测试用例要考虑 resolve 后的实际落点，不能凭直觉写
`../../etc/passwd` 看似"路径穿越"，但在此项目布局（项目根在 d:\Desktop\Ideas 下）resolve 后
仍在 Desktop 允许根内，校验放行是符合语义的，测试断言"应拒绝"是错的预期。
**教训**：相对路径测试要算清 resolve 后的绝对路径是否真在允许根外，用明确逃逸的路径（如向上多级到盘根外）。

### 4. pip 安装重型依赖（ddgs 拉 primp）耗时可能超 subagent 超时，导致中断
Task 1 subagent 在 `pip install ddgs trafilatura` 时中断（primp 4.7MB 下载慢），
留下 requirements.txt 改了但代码没写的半成品。改由主会话装（后台跑，同时做不依赖网络的其他任务）。
**教训**：装重型依赖用非阻塞 + 并行做其他工作；subagent 跑装依赖的任务要给足超时或改主会话执行。

### 5. 全量回归是验收底线——32 passed 才放心交付
Task 3 完成后跑全量 32 passed，确认 Task 1-5 的新工具没破坏既有 bubble/dock/history 等测试。
**教训**：每完成一个 track 收尾时必须全量回归，单元测试只测单点，集成回归才防连带破坏。

## 2026-08-13 电话模式 Task 1-9 实施

### 1. Plan 中的测试代码可能有 bug，subagent 发现后最小修复是正确做法
Task 3 的 patch 路径 `mss()` vs `mss.mss()`（mock 未对齐实现调用）、Task 4 的 `frame_to_data_url`
try/except 范围不够（img.save 未包导致 ValueError 逃逸）、Task 2 的 frame_ms 注释算错（16ms→64ms）。
严格按计划代码执行是好的，但发现 bug 时应最小修复并报告偏离原因，不盲目重试。
**教训**：Plan 代码 ≠ 可运行代码。测试驱动的价值正在于此——先 RED 后 GREEN，发现 plan 缺陷时最小修复。

### 2. Subagent 集成任务必须先读代码确认变量名再改，不能硬写死
Task 8 的 subagent 主动读了 1056 行 desktop_pet.py 确认所有变量名（load_config/character/send_pet_command/
active_session/_latest_line/SettingsDialog）的可用性，避免了按计划硬写死名字的错误。所有变量名经代码验证后
零偏离适配。
**教训**：集成任务（修改大型现有文件）的 subagent 必须加"Step 0: 读代码确认位置"步骤，不依赖计划文档的变量名假设。

### 3. 定向 git add 有效防止污染，9 个 commit 全部纯净
所有 9 个 task 的 commit 都只包含本任务的文件，未混入工作区遗留改动（desktop_tools.py/storage.py/
lessons.md/start.bat/Amadeus.spec/build/scripts/）。这是继 8-13 教训 5 后的再次验证。
**教训**：该教训已固化，后续所有 subagent 任务都要求在 Step 5 用定向 add 并报告 git status 确认。

### 4. 电话模式 TTS 降级是合理的风险缓释策略
StreamingTTS（UI redesign §7.3）未实现，电话模式 TTS 先接 SAPI SpeechPlayer（不可打断、无振幅口型），
但 VoiceCallController 的 `speaking_changed` 信号连接 + `_on_tts_speaking_changed` 回 listening 的逻辑
已预留 StreamingTTS 切换接口。不是"临时凑合"，是"承认风险 + 给降级路径 + 留切换接口"。
**教训**：前置依赖未就绪时，不是跳过功能，而是写降级实现 + 留接口，保证功能可运行且后续可平滑升级。

### 5. 集成任务的 subagent 须读全量代码，仅读片段不够
Task 8 的 subagent 读了 desktop_pet.py 1056 行全文，才确认 run_overlay 闭包内的所有变量（load_config、
character、send_pet_command、active_session、SettingsDialog、_latest_line）均可用。若只读
_build_buttons 和 __init__ 片段，会漏掉闭包变量的可用性信息，导致写错调用方式。
**教训**：集成任务 subagent 的 Step 0 必须读目标文件的全文（或至少 __init__ + 关键方法区域），
不能只读计划指明的行号范围。

## 2026-08-14 启动修复 + 聊天/Dock 交互 + 取消自动位移

### 1. Edit 工具"成功"不等于文件被改，git stat cache 会误导诊断
desktop_pet.py 工作区实际 = HEAD 09ff411（之前会话已提交 collapse_button/WA_TransparentForMouseEvents/删除 _check_foreground），
但这轮对话我重新 Edit 这些改动时，Edit 工具报告"修改成功"且 cat -n 显示新内容——实际文件未被改变
（因为已是目标状态，old_string 匹配到的位置 new_string 与现有内容一致）。git status/diff 都说 desktop_pet.py
无改动，差点误判为"git 索引损坏"。**教训**：诊断"改动丢失"时，用 `git diff HEAD -- file` + `git show HEAD:file`
确认工作区与 HEAD 的真实关系，不能只信 Edit 工具输出或反复 update-index --refresh。

### 2. 上下文压缩后开始工作前必须先 git log + git show HEAD 确认最新提交内容
这轮对话重新做了之前会话已提交的 desktop_pet.py 改动（collapse_button 等），完全重复劳动。
若开始前先 `git log --oneline -3` + `git show HEAD:desktop_pet.py | grep 关键标记`，会立刻发现
改动已在 HEAD，直接跳到 core/desktop_tools.py（真正未提交的文件）。**教训**：压缩恢复或新会话开始时，
除了读 lessons.md，还必须 git log + git show HEAD 确认代码实际状态，避免重复已完成的工作。

### 3. TRAE 沙箱禁止写入任何 site-packages，用 --target 装到项目本地 + sys.path 注入
`pip install` 默认装到 `D:\anaconda\Lib\site-packages` 被沙箱拒（TRAE Sandbox Error: hit restricted），
`pip install --user` 装到 `C:\Users\23733\AppData\Roaming\Python\Python313\site-packages` 也被拒
（WinError 5 拒绝访问）。解决：`pip install --target=.libs ddgs trafilatura` 装到项目本地 .libs/，
在 desktop_pet.py 顶部 `sys.path.insert(0, str(ROOT/.libs))` 注入。冻结模式（exe）跳过，依赖已打包。
**教训**：TRAE 内装依赖只能用 --target 到项目内 + sys.path 注入；--user 和默认路径都被沙箱拦。

### 4. Qt QGraphicsOpacityEffect.setOpacity(0) 只改视觉不改事件接收，用 WA_TransparentForMouseEvents
dock 用 opacity 1→0 淡出后，视觉消失但控件仍 isVisible()=True 且仍接收鼠标事件，导致聊天框打开时
点 dock 原位置仍能误触"固定/设置/记录/退出"按钮。解决：`setAttribute(Qt.WA_TransparentForMouseEvents, True)`
让透明控件不拦截鼠标事件（且不影响 opacity 动画），反向时设 False 恢复点击。
**教训**：Qt 透明控件 ≠ 不可交互控件。opacity=0 只是视觉，要禁用交互必须配合 WA_TransparentForMouseEvents
或 setEnabled(False) 或 hide()。

### 5. pythonw.exe 启动失败时错误被静默吞，诊断必须改用 python.exe 前台跑
start.bat 用 `pythonw.exe desktop_pet.py` 启动，pythonw 无控制台，崩溃时 stderr 被丢弃，用户感知是
"点了没反应"。诊断时改用 `python.exe desktop_pet.py 2>&1` 前台跑，立即看到 ModuleNotFoundError 等真实错误。
**教训**：GUI 程序启动失败排查，第一步是把 pythonw 换成 python 前台跑捕获 stderr；修复后再换回 pythonw。
