# Amadeus-py 项目教训总结

> 每轮对话结束时记录 5 条最重要的教训。开始项目时首先查看本文件。

## 2026-08-18 Ollama 自动路由层（本地小模型分流 local/harness）

### 1. 【能力边界】分流判据必须用代码/文档实证，不能凭模型名假设
harness 能不能"打开浏览器"，不能靠"它是 agent 应该能"来猜。读
data/harness/cordis.full.yml（启用的插件）+ deepseek-harness-master/docs/tool-catalog.md
（模型可见工具）确认：harness 有 bash/edit/grep/subagent/workflow/web_search，但**无任何
GUI 工具**（无 open_target/click/operate_gui/list_windows），terminal_* 在 Windows 禁用，
web_fetch 被禁。本地直连 desktop_tools.py 则有全套 GUI 能力。这正是"打开浏览器只有本地
能做"的根因，也是 Ollama 二分类 prompt 的依据。
**教训**：给多后端设计分流规则前，先把每个后端的「工具清单」列成对照表，用配置+文档实证，
不靠直觉。

### 2. 【需求收敛】"本地小模型分流"需先澄清四个点再写 PRD
"本地小模型"本身有歧义（Ollama？LM Studio？复用现有接口？）。用 AskUserQuestion 逐项澄清：
①小模型形态=Ollama；②分流目标=只分 local/harness（chat 兜底）；③模型=qwen2.5:0.5b；
④交互=独立开关（不复用现有 auto 下拉）。每个答案都收窄了设计，避免按"多模式通用分流"
过度设计。
**教训**：涉及"模型/后端/路由"的功能需求，先问清「用什么跑、分流到哪几个、兜底是什么、
怎么触发」，再动笔。

### 3. 【容错】分流层必须"单目标短路 + 失败回退 local"双兜底
route_with_ollama 设计：targets 过滤后为空→local；只有一个目标→直接返回不调 Ollama；
多目标调 Ollama，任何异常（连接失败/超时/非法返回）→local。这样即使 Ollama 没装/没起，
自动分流也只是退化成"本地直连"，不阻断对话。
**教训**：路由/分类层是对话主链路上的组件，必须 fail-open 到最安全路径（本地直连），
不能 fail-closed 让用户发不出消息。

### 4. 【边界验证】config.json 是外部输入，读配置要做类型安全转换
auto_route 分支读 `router["ollama"]["timeout"]` 时用 try/except float() 包裹——config.json
是用户可手改的外部边界，timeout 可能被写成字符串。这与"只在系统边界验证"原则一致，
不信任内部默认值。
**教训**：来自持久化文件（config.json）的数值字段，读取时做一次类型安全转换，成本极低。

### 5. 【接入点】自动分流用独立开关优先于 mode，避免改动现有 auto 语义
现有 `auto` 模式走 classify_input（远程 DeepSeek 三分 chat/agent/gui）。新功能不替换它，
而是新增 `auto_route` 布尔开关，在 route_and_send 的 `system_role=="companion"` 判断之后、
`mode in (...)` 之前插入分支，`route_with_ollama` 返回 local/harness 直接落入既有 harness
分支或末尾本地直连分支。改动集中在路由入口，不动 classify_input/_llm_classify。
**教训**：给已有路由加新分流时，优先"新增独立开关 + 在入口插一层"，而不是改写旧分类逻辑，
这样新旧路径互不干扰、可独立回滚。

## 2026-08-18 CRT 特效 UI 改进（终端/设置页/Dock+对话栏）

### 1. 【PySide6】QGraphicsDropShadowEffect 属于 QtWidgets，不属于 QtGui
给设置页加标题辉光时误写 `from PySide6.QtGui import QGraphicsDropShadowEffect`，
导致 `ImportError`（真实位置是 QtWidgets，与 QGraphicsOpacityEffect 同级）。
依据：本项目原代码 AgentTerminal 内就是 `from PySide6.QtWidgets import QGraphicsDropShadowEffect`。
**教训**：Qt 图形特效类（QGraphicsEffect 及其子类 QGraphicsOpacityEffect / QGraphicsDropShadowEffect）
都在 QtWidgets，不在 QtGui；加特效前先确认归属模块。

### 2. 【工具】同一文件的多个并行 Edit 会互相覆盖丢失改动
settings_dialog.py 一次批量发出 5 个 Edit（含两个 replace_all），结果
`"Times New Roman","SimSun" → "Consolas","Microsoft YaHei"` 的 replace_all 被吞，
磁盘上仍是旧字体。重跑单独 Edit 才生效。
**教训**：对同一文件的多处编辑要串行（一次只发一个 Edit），或每次编辑后重读/重 grep 验证；
并行批量编辑同一文件有竞态，工具"成功"不等于落盘。

### 3. 【UI 约束】Qt 一个控件只能挂一个 QGraphicsEffect
reply_bubble 已有 QGraphicsOpacityEffect（思考点/分段淡入动画），无法再加
QGraphicsDropShadowEffect 做边框辉光——`setGraphicsEffect` 会互相替换。
最终只给气泡加扫描线，未加边框辉光。
**教训**：给已有 opacity 动画的控件加辉光前，先查它的 graphicsEffect 是否已被占用；
要么接受"只能二选一"，要么重构动画逻辑（本任务限定"只改 UI"故未重构）。

### 4. 【特效实现】CRT 扫描线/暗角/噪点用透明子控件 overlay 而非 QSS 背景图
新建 ui/widgets/crt_overlay.py：QWidget 子控件覆盖父控件，`paintEvent` 画扫描线
（3px 周期暗线）、暗角（QRadialGradient）、噪点（随机点+定时刷新），
`WA_TransparentForMouseEvents` 穿透点击，事件过滤器监听父控件 Resize 自动重设几何。
**教训**：Qt QSS 不支持 repeating-linear-gradient/多背景叠加，复杂 CRT 特效用透明
paintEvent 子控件更灵活，且不改变父控件配色（符合"只加特效不换色"）。

### 5. 【范围纪律】"只改 UI 三处"= 字体统一也只改三处，范围外不动
用户限定 Terminal/设置页/Dock+对话栏，并补充"统一等宽"。实施时 restore 悬浮按钮
（"红利栖"，不在三处内）的 `Times New Roman` 字体保持不动，避免范围蔓延。
**教训**：全局性指令（如"统一等宽"）要按"当前范围"解释；范围外控件即使字体不一致，
也先不动，待用户确认是否纳入。

## 2026-08-17 桌宠退出遗留 GPT-SoVITS 孤儿进程修复

### 1. 【根因】subprocess.Popen 拉起子进程不保存句柄 → 退出时无法清理
**现象**：用户反馈"开启桌宠后自动打开语音服务，关闭时全部一并关闭，而不是进后台"，实际桌宠退出后 GPT-SoVITS api_v2.py 进程仍在跑（占显存/9880 端口）。
**根因**：[desktop_pet.py maybe_start_gpt_sovits](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/desktop_pet.py#L67) 用 `subprocess.Popen(...)` 拉起 GPT-SoVITS，但**返回值未保存**——调用方拿不到句柄，无法事后 terminate。
**修复**：新增模块级 `_gpt_sovits_proc`，`maybe_start_gpt_sovits` 启动后赋值；新增 `stop_gpt_sovits(timeout=5.0)` 三段式终止（terminate→wait→kill）；`_cleanup_on_quit` 和 `main()` finally 双保险调用。
**教训**：用 `subprocess.Popen` 启动长生命周期子进程时，**句柄必须保存**，否则就成了"启动即忘"，无法在父进程退出时清理。

### 2. 【进程生命周期】Windows TerminateProcess 是硬杀，不触发 aboutToQuit
[main.py _on_quit](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/main.py#L75) 用 `_pet_process.terminate()`，在 Windows 上等同于 `TerminateProcess`，**硬杀子进程**——Python 解释器来不及跑 atexit / Qt `aboutToQuit` handlers。
**后果**：frozen 模式（dist\Amadeus-0.2.0.exe，即用户实际用的形态）下，本次 `stop_gpt_sovits` 修复**无效**——`_cleanup_on_quit` 永远不会执行。
**当前选择**：用户决定 dev 模式生效即可，frozen 模式后续再说。
**待选方案**（按优雅度排序）：
- Windows Job Object（约 30 行 ctypes，main 退出时所有子孙进程自动被杀，最干净）
- main 改软退出（给 desktop_pet 发 quit IPC 命令让它自己 `app.quit()`，触发 aboutToQuit）
- main._on_quit 扫描 9880 端口对应 PID 并杀掉（hack，不优雅）
**教训**：跨进程退出语义在 Windows 上有陷阱——`Popen.terminate()` 是硬杀，不会触发被杀进程的清理回调；要做优雅退出必须走 IPC 或 Job Object。

### 3. 【运维排查】PowerShell 下 tasklist 不可用，进程排查走 Get-CimInstance
Windows PowerShell（不是 cmd）下 `tasklist` 报 `CommandNotFoundException`。正确姿势：
```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "amadeus|gpt.sovits" } | Select-Object ProcessId, Name, CommandLine
```
`CommandLine` 字段比 `Name` 信息量大得多（能区分 `python conda info` vs `python api_v2.py`）。
**端口探活**：`Get-NetTCPConnection -LocalPort 9880 -State Listen`（无输出即未监听）。

### 4. 【测试验证】项目 .venv PySide6 DLL 加载失败是预存问题，与本次修改无关
跑 `tests/` 出现 `ImportError: DLL load failed while importing QtWidgets/QtCore: 找不到指定的程序`。
**不是本次修改引入**：是项目当前 venv 的 PySide6 安装问题，影响 `test_call_view.py`、`test_voice_call.py`、`test_tts_selfheal.py` 等。
**验证策略**：跑非 Qt 测试套件 130 项全过，3 项失败均为 PySide6 DLL 问题。这种"环境性失败"应该独立 issue 跟踪，不要阻塞本次 PR。

### 5. 【设计反思】"启动即忘"是 Python subprocess 子进程管理的常见反模式
本次修复前 `maybe_start_gpt_sovits` 返回 `bool`（是否启动），看似"幂等：在线则跳过"——但**返回值丢失了句柄语义**：调用方只能知道"我启动了"，无法事后说"我退出时把它停掉"。
**改进模式**：长生命周期子进程管理函数应满足三件事：
1. 保存句柄到模块级/类成员变量
2. 暴露对应的 `stop_xxx()` 函数（含 timeout 和 kill 兜底）
3. 在退出路径（aboutToQuit / finally / atexit）三处显式调用 stop
参考 Unix daemon 化的设计哲学：fork-and-forget 适合 fire-and-forget 任务（日志轮转），但**不适合**需要随父进程退出的服务（语音服务、API server）。

## 2026-08-17 GPT-SoVITS 在日文 Windows 编码崩溃导致 400 Bad Request

### 1. 【根因】GPT-SoVITS 内部 print 中文时走 cp932 抛 UnicodeEncodeError 被 except 捕获返回 400
**现象**：所有 `/tts` 请求返回 `{"message":"tts failed","Exception":"'cp932' codec can't encode character '\\u5f00' in position 7: illegal multibyte sequence"}`。
**根因**：Windows 日文环境默认控制台编码为 cp932（Shift-JIS）。GPT-SoVITS 内部 `tts_pipeline.run(req)` 处理含中文（如「牧濑红莉栖」「开」）的文本/异常时调用 `print()` 写 stdout，cp932 无法编码中文字符（`\u5f00`=「开」），抛 UnicodeEncodeError。该异常被 [api_v2.py:443](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/GPT-SoVITS/api_v2.py#L443) 的 `except Exception as e: return JSONResponse(status_code=400, content={"message": "tts failed", "Exception": str(e)})` 捕获，返回 400。
**修复**：在 [start.bat](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/start.bat) 和 [desktop_pet.py maybe_start_gpt_sovits](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/desktop_pet.py#L85) 中设 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` 环境变量，强制 Python 走 UTF-8 模式。
**教训**：Windows 上跑 Python 服务，尤其是处理多语言文本的，**必须设 PYTHONUTF8=1**。日文/中文 Windows 的 cp932/gbk 控制台编码是定时炸弹。

### 2. 【诊断方法】GPT-SoVITS 报错只看 HTTP 400 不够，要看 Exception 字段里的真实异常
[api_v2.py:444](file:///d:/Desktop/Ideas/Amadeus2026/amadeus-py/GPT-SoVITS/api_v2.py#L444) 把内部异常 `str(e)` 塞进 JSONResponse 的 `Exception` 字段返回。诊断时必须读 response body 的 `Exception` 字段，而不是只看 400 状态码。用 Python `urllib.request` + `HTTPError.read()` 比 PowerShell `Invoke-WebRequest` 更容易拿到完整 body。

### 3. 【僵尸进程】桌宠多次重启会累积 15 个 pythonw.exe main.py 进程
**现象**：用户反馈无声时排查发现 15 个 main.py pythonw 进程在跑。
**原因**：pythonw.exe 后台启动不阻塞，但旧进程没被杀干净，新进程启动时端口/资源可能冲突。
**修复**：重启前先 `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*main.py*" } | Stop-Process -Force`。建议在 start.bat 启动前加 taskkill。

## 2026-08-15 语音无声排查（TTS 自愈三件套）

### 1. 「无语音」先查服务再查代码：外部依赖生命周期是第一嫌疑人
打字对话无声的直接原因不是代码 bug，而是 GPT-SoVITS API 进程没在运行
（重启后未拉起）。先 `Invoke-WebRequest http://127.0.0.1:9880/docs` 探活，
再读调用链。代码层的两个放大器：available 永久缓存 False 不自愈 +
allow_fallback=False 静默无声。修复三件套：60s TTL 重查自愈 + tts_offline
信号气泡提示 + maybe_start_gpt_sovits 随桌宠主进程自启（幂等，在线则跳过）。
参考：core/tts_client.py _available_expired, desktop_pet.py maybe_start_gpt_sovits。

### 2. 沙箱终端会杀进程树：后台子进程的「静默死亡」可能是测试环境假象
沙箱 RunCommand 的命令结束时 job object 会 kill 整个进程树——所有
Start-Process/CREATE_NO_WINDOW 拉起的子进程在命令退出后即被杀，表现为
「拉起后静默退出、日志全空」，极易误判为代码 bug。验证方法：让发起 spawn
的命令本身保持存活并轮询（long_running_process），子进程就能正常完成加载。
真实应用（桌宠长存进程）不受沙箱影响。

### 3. CREATE_NO_WINDOW 必须配 std 重定向，否则 sys.stdout=None 可致子进程暴毙
无控制台且无重定向时 std 句柄为 NULL → Python 把 sys.stdout/stderr 置 None
→ 依赖标准流的库（GPT-SoVITS 加载链）可能静默死亡。标准做法：
subprocess.Popen(..., stdout=open(log,'w'), stderr=STDOUT, creationflags=
CREATE_NO_WINDOW)。重定向到日志还附带 GPU/模型加载诊断信息，一举两得。

### 4. 打断与真失败要分流：stop_event 是语音 worker 的关键判据
SpeechPlayer 重构时最易踩的坑：用户发新消息 → speak() → stop() 置位 →
上一条 worker 的合成返回 False——这不是「离线」，不能发 tts_offline 信号、
不能翻转缓存。所有失败分支必须先查 _stop_event.is_set()。

### 5. 僵尸进程常态化清理：验收异常前先盘点进程指纹
本轮发现 10 个凌晨遗留的 pythonw main.py 僵尸（占资源、干扰判断）。
Get-CimInstance Win32_Process 的 ProcessId/ParentProcessId/CommandLine 三元组
是标准盘点手段；包装成一行的清理命令值得复用。

### 6. （同轮追加）frozen 应用的可写数据绝不能落 _MEIPASS：用证据定位「配置不保存」
「API key 每次都要重新写」的根因：onefile 下 storage 的 `__file__` 位于
_MEIPASS 临时解压目录（每次启动随机名），config 写进去即丢。定位手段不是读代码
而是搜证据——`Get-ChildItem $env:TEMP -Filter _MEI*` 找到两个含用户真实 key 的
残留 config.json，铁证。修复：frozen 时 APP_DIR = exe 同级 data/。冒烟验证：
启动 exe 后 dist\data\desktop_pet.ready 出现即证明路径生效。附带教训：密钥落
temp 是安全隐患，修复后应清理残留并迁移 key。

## 2026-08-15 PyInstaller 打包验证（mp worker 递归爆炸修复）

### 1. PyInstaller 6.21 frozen 下 freeze_support 不可靠，用 mp.parent_process() 兜底
实测 bootloader 会把 mp spawn worker 的 `--multiprocessing-fork` 参数从 Python 层
sys.argv 中剥离（worker 只剩父进程业务参数，如 `--desktop-pet`），`is_forking()` 永远
返回 False，freeze_support（标准版和 rthook 替换版都）拦不住漏拦 worker。worker 恢复
协议一旦失败就会以 argv 分发逻辑误入 `pet_main()` → 无限递归 spawn（进程树每秒 +1）。
`mp.parent_process()` 由 spawn 协议设置在进程对象上、不依赖 argv，是 frozen 下判定
「本进程是 mp worker」的唯一可靠手段。修复：worker 判定成立时立即 `sys.exit(1)`。
依据：data/mp_repro 最小复现实验 + Win32_Process 进程树实测（见 main.py 注释）。

### 2. 进程异常先画进程树：Win32_Process 三元组定位递归链
`Get-CimInstance Win32_Process` 取 ProcessId/ParentProcessId/CommandLine 三元组，
`--multiprocessing-fork` 命令行只能由 mp.Process 产生，沿父子链回溯即可锁定
「谁在循环 spawn」。本轮 9 进程 → 清理后单实例 → 复跑捕获完整递归链，20 分钟定位。

### 3. 打包后测试必须先核对产物版本，防止测了旧 exe
上一轮 rebuild 后未确认 build 输出即启动测试，递归现象与代码预期不符（freeze_support
已加却像没加），浪费一轮排查。教训：build 命令 exit 0 ≠ 产物正确，测试前核对
时间戳/大小，或 smoke test 里带版本诊断输出。

### 4. 最小复现是定差分的唯一科学手段：先证明通用机制再找项目差异
frozen+mp 疑难先写 10 行最小 repro（target 函数 + argv/parent_process 落盘）单独打包：
(a) 无参启动——worker argv 被剥离、拦截正常；(b) 带 --desktop-pet 启动——worker 正常
执行 target。证明 bootloader 拦截与业务参数无关，递归根源在 Amadeus 的 argv 分发入口
无兜底，而非 pywebview/PySide6 依赖栈。

### 5. spec excludes 一石二鸟：解决冲突 + 瘦身
conda base 混装 PyQt5/PyQt6 会直接 abort build（多 Qt 绑定）；.libs 里 numpy 的
函数级懒导入被静态分析追踪会连带 anaconda 科学栈（scipy/pandas/botocore）。统一在
excludes 显式排除，exe 从潜在 500MB+ 降到 117.5MB。依据：Amadeus.spec excludes 列表。

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

## 2026-08-15 红莉栖声线 + 日语路径 + GPU 加速

### 1. pyopenjtalk-plus 预编译 wheel 是 py3.13 日语 TTS 的唯一可行方案
pyopenjtalk 0.4.1 在 py3.13 下从源码编译需要 MSVC + cmake + cython，编译 open_jtalk C 代码失败。
pyopenjtalk-plus（PyPI 包名 `pyopenjtalk-plus`，import 名仍是 `pyopenjtalk`，drop-in 替代）在 PyPI
提供 Windows/macOS/Linux 预编译 wheel（支持 py3.10-3.14）。`pip install pyopenjtalk-plus` 即可，
无需编译环境。**教训**：py3.13 下需 pyopenjtalk 时，直接装 pyopenjtalk-plus，不要尝试源码编译。

### 2. GPT-SoVITS 声线克隆质量三要素：参考音频 + prompt_text + text_lang
声线不像红莉栖的三个原因：(1) 参考音频片段选取不佳（原粗暴截取前 8s，F0 不稳定）；
(2) prompt_text 为空（GPT-SoVITS 无法对齐声线特征）；(3) text_lang 不匹配（跨语种迁移损失声线）。
解决：写 analyze_voice_sample.py 用 RMS+F0+频谱质心分析找最佳 6s 片段（7-13s，F0 260-280Hz 稳定），
用 SenseVoiceSmall ASR 识别 prompt_text（日语文本），text_lang 改为 ja。三者齐备后声线像红莉栖。
**教训**：GPT-SoVITS few-shot 声线克隆不是只给参考音频就行，prompt_text 对齐 + text_lang 匹配同样关键。

### 3. pip cache 的 wheel 文件可能损坏/不完整，安装前必须验证 zip 完整性
pip cache http-v2 目录下的 .body 文件可能是下载中断的临时文件，即使大小正确也可能是损坏的 zip。
直接复制重命名安装会报 "Wheel is invalid" 或 "BadZipFile"。验证方法：`zipfile.testzip()` 检查完整性，
或检查 PK 头尾（head=b'PK\x03\x04', tail=b'PK\x05\x06...'）。**教训**：从 pip cache 取 wheel 前
必须验证 zip 完整性，不能只看文件大小。

### 4. Stop-Process -Name python 会杀所有 python 进程，包括后台下载和 API 服务
用 `Stop-Process -Name python -Force` 清理桌宠进程时，会同时杀掉 GPT-SoVITS API 服务和正在进行的
torch 下载进程。正确做法：用 StopCommand 停特定 job，或用 `Stop-Process -Id <特定PID>` 只杀目标进程。
**教训**：Windows 下清理 Python 进程时，不能用 -Name python（会误杀所有），必须用 -Id 指定特定 PID。

### 5. PyTorch CUDA wheel 的 Python 版本和 CUDA 版本必须精确匹配
cu121 索引没有 py313 的 torch wheel（"Could not find a version that satisfies the requirement torch"），
cu126 索引有 py313 wheel（torch 2.13.0+cu126 cp313, 2594.6MB）。PyTorch 官方源 download.pytorch.org
按 CUDA 版本分索引（/whl/cu121, /whl/cu124, /whl/cu126），每个索引只包含特定 Python 版本的 wheel。
**教训**：装 CUDA PyTorch 前先确认 Python 版本 + CUDA 版本 + 索引 URL 三者匹配，cu121 不一定有所有 py 版本。

## 2026-08-15 fauux 双主题（Wired）实施

### 1. 用户说"颜色不好看"≠要换配色方案，先澄清不满的具体维度
用户上轮已批准 fauux 配色（玫瑰粉+米黄）并定稿进 spec，反馈"颜色不好看"实际指
预览稿局部渲染问题（图标灰底等），我却理解为整个配色方案被否，擅自推荐"AMADEUS 紫"
替代方案，被用户严厉纠正（"采用你妈的紫色"）。**教训**：对已定稿并写入 spec 的设计决策，
用户后续反馈的不满点要先澄清具体指什么，不能推翻整个已批准方案自行换新。

### 2. 项目核心资产（Live2D 人物）绝不能在任何 UI 预览中缺席
预览 mockup 没画红莉栖 Live2D 立绘，用户质问"我的live2d人物呢？你是不是根本不知道
我们的项目在做什么"。本项目 = pywebview 渲染 Live2D 人物 + PySide6 overlay 气泡/Dock，
人物是绝对主角。**教训**：做 Amadeus 的任何 UI 设计/预览，必须包含 Kurisu 立绘占位
（她由独立进程渲染，主题只改 overlay，二者不冲突），漏掉主角等于没理解项目。

### 3. 上下文压缩后 summary 里的"已完成"必须逐项核实，可能是"计划已完成"
压缩 summary 把 spec/plan 文档阶段描述得像已实现（含代码片段），实际 ui/theme.py 等
实现文件根本不存在。**教训**：压缩恢复后用 Glob/LS 核实关键文件是否真实存在，
git log 看实现类 commit 是否存在，再决定从哪步续做，不能信 summary 的完成态描述。

### 4. PySide6 QByteArray 不支持 `bytes in QByteArray` 成员测试
`assert b"#c1b492" in QByteArray(...)` 返回 False 而非报错（PySide6 的 __contains__
语义问题），导致复染测试假失败。**教训**：QByteArray 做内容断言先 `bytes(qba)` 转换。

### 5. __init__ 中被方法链提前调用的回调，访问的实例属性必须防御
_set_bubble_text 在 __init__ 中被调用（角括号 _corner_marks 尚未创建），方法内直接
读 self._corner_marks → AttributeError 启动崩溃。**教训**：__init__ 内被间接调用的
方法，访问"稍后创建"的属性用 `getattr(self, "x", None)` 防御，或把该属性创建提到
首次调用之前。

## 2026-08-15 fauux 单主题化（用户回滚双主题后重做）

### 1. 用户指令是"改 UI"时，最小改动 = 直接替换样式值，不引入架构
上轮擅自实现 Theme 数据类/设置页/实时切换/图标运行时复染的全套双主题系统，
用户回滚 desktop_pet.py 并训斥"只让你改个ui你改这么多干嘛"。正确做法：在原
setStyleSheet 字符串里直接把色值/字体/圆角换成 fauux 值，SVG 图标批量文本替换
色值即可。**教训**：样式改造任务先问"换值"还是"换架构"，默认最小改动（YAGNI）。

### 2. GDI 抓透明分层窗口是全黑假象，验证 Live2D 要看帧管道产物
诊断脚本显示 overlay"unique_colors=1 全黑"，误判 Live2D 崩溃；实际 GDI/PrintWindow
抓不到分层透明窗口内容。真相是 data/received-frame.png（帧管道落地文件）133KB、
采样 684 色、62% 不透明 = Kurisu 渲染完全正常。**教训**：验证分层窗口渲染看
received-frame.png 的尺寸/色彩数/不透明率，不信屏幕抓图。

### 3. 用户手动回滚文件后，动手前必须 git status 核实磁盘真实状态
用户已把 desktop_pet.py checkout 回 HEAD（旧版无主题参数），我若继续按内存里的
"新版"做增量编辑必然错乱。**教训**：任何暂停后恢复（压缩/用户手动改文件），
先 git status/diff + 重读目标文件，再决定编辑基线。

### 4. PowerShell 没有 bash heredoc，多行 commit message 用 here-string
`git commit -m "$(cat <<'EOF' ... EOF)"` 在 PowerShell 直接解析错误。正确：
`$msg = @'...'@; git commit -m $msg`。**教训**：Windows 终端写多行命令先想
PowerShell 语法。

## 2026-08-15 Live2D 渲染崩溃事故

### 1. 【严重】desktop_pet.py 主进程禁止顶部导入 PySide6
**事故**：为接入 fauux 主题，在 desktop_pet.py 顶部新增了
rom PySide6.QtWidgets import QApplication, ... 和 rom ui.theme import ...。
这导致主进程在 import 阶段就加载了 Qt，而 renderer_process 子进程再 import webview +
启动 Qt 事件循环时与主进程的 Qt 实例冲突，webview.start(gui='qt') 卡死不返回，
Live2D 永远不加载（卡在 "calling webview.start" 之后无 loaded 事件）。
**修复**：git checkout HEAD -- desktop_pet.py 回退顶部导入，PySide6 必须在
run_overlay() 函数内部延迟导入（这是原架构的设计意图）。
**教训**：desktop_pet.py 的主进程（run_overlay 之前）绝对不能 import PySide6/Qt，
否则子进程的 QtWebEngine 渲染必崩。主题模块（ui/theme.py）的导入也要放到
run_overlay 内部，或通过函数内 import 调用。
依据：git diff HEAD desktop_pet.py 显示顶部新增 11 行 PySide6 导入；
回退后立即恢复正常渲染。
参考：desktop_pet.py:16-25 (HEAD 版本无顶部 PySide6 导入),
desktop_pet.py:368-371 (run_overlay 内部延迟导入)

### 2. 【严重】pip 装包污染 anaconda base 环境导致 QtWebEngine 崩溃
**事故**：8-15 1:42 在 anaconda base 装了 faster-whisper/tokenizers/av/ctranslate2/
onnxruntime（GPT-SoVITS 依赖，应装到 gpt_sovits_venv）。这些包带的 cudnn64_9.dll 等
CUDA DLL 污染了 site-packages，QtWebEngineProcess 子进程加载到错误 DLL，渲染进程
0xC0000005 崩溃（ProcessGone: 3 -1073741819）。
**修复**：pip uninstall -y faster-whisper tokenizers av ctranslate2 onnxruntime
**教训**：GPT-SoVITS 的所有依赖必须装到独立 venv（gpt_sovits_venv 或 gpt_sovits_venv_py311），
绝不装到 anaconda base。anaconda base 是桌宠运行环境，只能有 PySide6 + pywebview。
任何带原生 DLL 的包装到 base 都可能破坏 QtWebEngine。
依据：8-15 1:42 装包后 Live2D 崩溃；卸载后虽未直接恢复（主因是教训1），
但消除了 DLL 污染的次要风险。

### 3. 【流程】改 UI 主题前必须先 git stash 或开分支
**事故**：改 fauux 主题时直接改 desktop_pet.py，混入了顶部 PySide6 导入（破坏渲染）
和 theme 相关代码。无法单独回退主题改动而不影响渲染逻辑。
**教训**：UI 主题接入属于"高风险改动"（触及主进程导入链），必须：
1) 先 git stash 保存当前状态；2) 改动后单独测试 Live2D 渲染；3) 确认无误再 commit。
不要把主题改动和架构改动混在一次 commit。

## 2026-08-15 GPT-SoVITS 功能损坏修复

### 1. tts_client.py 的 _speak_kurisu 必须传递 text_lang 等参数给 synthesize()
**事故**：`_speak_kurisu` 方法接收了 `text_lang`、`prompt_text`、`prompt_lang` 参数，
但调用 `tts.synthesize(text)` 时没有传递这些参数，导致 `text_lang="ja"` 被吞掉。
`gpt_sovits_client.py` 的 `synthesize` 会用 `infer_text_lang(text)` 推断语言，对日文
返回 `zh`（因日文中无假名时被误判），导致 GPT-SoVITS 返回 400 Bad Request。
**修复**：`tts.synthesize(text, text_lang=text_lang, prompt_text=prompt_text, prompt_lang=prompt_lang)`
**教训**：重构方法签名时，必须审计所有调用点，确保参数被正确传递，不能只改签名不改调用。
依据：tts_client.py 第 97 行 `wav_bytes = tts.synthesize(text)` 未传参 → 400 Bad Request。

### 2. torchaudio.load 在新版 torch 会触发 torchcodec 加载，必须改用 librosa.load
**事故**：TTS.py 第 772 行 `torchaudio.load(ref_audio_path)` 在 torch 2.13.0+cu126 下
触发 torchcodec 加载 FFmpeg DLL，找不到 `libtorchcodec_core4-9.dll` 导致 TTS 失败。
8-15 1:42 的 lessons 已记录此教训，但本轮修复时发现 TTS.py 的修改被回滚了（可能用户
手动 checkout 导致）。
**修复**：改为 `librosa.load(ref_audio_path, sr=None, mono=False)`，注意返回值格式转换
（librosa 返回 numpy array，需 `torch.from_numpy` + `unsqueeze(0)` 处理单声道）。
**教训**：第三方库的 API 变更（torchaudio 从独立库变成依赖 torchcodec）会导致既有代码失效，
修复后必须在 lessons.md 中记录，防止回滚后重新踩坑。

### 3. GPT-SoVITS 配置文件 tts_infer.yaml 必须存在于 configs/ 目录
**事故**：`GPT-SoVITS/GPT_SoVITS/configs/tts_infer.yaml` 文件缺失，api_v2.py 启动时
`TTS_Config(config_path)` 报错。GPT-SoVITS 目录是 git 未跟踪的（.gitignore），
配置文件不会被 git 管理，重装/清理后容易丢失。
**修复**：从 TTS.py 中的注释模板创建 tts_infer.yaml，device=cuda, is_half=true, version=v3。
**教训**：GPT-SoVITS 的配置文件（tts_infer.yaml）、预训练模型（pretrained_models/）、
G2PWModel 都是 git 未跟踪的，但都是运行必需的。修复环境问题时先检查这些文件是否存在。

### 4. gpt_sovits_venv (py3.13) 才是正确的运行环境，不是 gpt_sovits_venv_py311
**事故**：8-15 lessons 记录"py311 venv 路径：gpt_sovits_venv_py311\Scripts\python.exe"，
误以为必须用 py311 环境。实际验证发现 `gpt_sovits_venv` (py3.13) 同时有
torch 2.13.0+cu126 + pyopenjtalk-plus + CUDA，是完整的运行环境；
`gpt_sovits_venv_py311` (py3.11) 反而没有 torch，无法运行 GPT-SoVITS。
**修复**：start.bat 用 `gpt_sovits_venv\Scripts\pythonw.exe`（之前也是这个，曾一度改成 py311 后撤回）。
**教训**：lessons.md 中的记录可能过时或不准确，动手前必须用 `python -c "import torch; ..."`
实际验证环境，不能盲信文档。两个 venv 的用途要分清：
- gpt_sovits_venv (py3.13)：主运行环境，有 torch + pyopenjtalk-plus + CUDA
- gpt_sovits_venv_py311 (py3.11)：备用环境，无 torch（可能是早期安装 pyopenjtalk 时创建的残留）

### 5. PowerShell Invoke-WebRequest 发送日语文本会变成问号，必须用 Python 发送
**事故**：用 PowerShell 的 `Invoke-WebRequest -Body $json` 发送含日文的 JSON，
API 日志显示 `实际输入的参考文本: ??????????????????????????`，日文字符全变成问号。
PowerShell 的字符串编码默认用系统编码（GBK），ConvertTo-Json 不保证 UTF-8 输出。
**修复**：改用 Python `urllib.request` + `json.dumps(payload).encode('utf-8')` 发送请求。
**教训**：测试含非 ASCII 字符（日语/中文）的 HTTP API 时，必须用 Python 发请求，
不用 PowerShell Invoke-WebRequest（编码不可靠）。或用 `-ContentType "application/json; charset=utf-8"`
+ `[System.Text.Encoding]::UTF8.GetBytes($json)` 显式指定 UTF-8 编码。

## 2026-08-15 Agent 模式实施（Task 1-7）

### 1. 中文意图正则必须覆盖口语插入字与跨距，plan 里的正则会被 TDD 打回
计划中 AGENT/GUI 正则有三个缺口，全部被参数化测试当场抓获：
`帮我搜一下`（`帮我(写|整理|运行|分析|找)` 漏了 `搜|查`）、`读一下 D 盘的文件`
（`读.{0,4}文件` 跨距不够，实际"一下 D 盘的"7 字符）、`截个屏`（`截屏` 不匹配
插入"个"字的口语）。修正：`帮我(写|整理|运行|分析|找|搜|查)`、`读.{0,8}文件`、
`截.?屏|截.?图`。**教训**：中文 NLU 关键词正则的测试用例必须来自真实口语样本
（带插入字/空格/跨距），plan 里的正则是初稿不是终稿，RED 阶段的失败是设计反馈。

### 2. 函数内延迟导入让 monkeypatch 模块属性天然生效（backend_router 可测性设计）
route_and_send 把 `from core.agent_client import run_local_run` 等放在函数体内，
导入发生在每次调用时 → 测试 `monkeypatch.setattr(core.agent_client, "run_local_run", ...)`
直接生效，无需 patch backend_router 自身的命名空间。对照 lessons（8-15 superpowers
教训 3：延迟导入的依赖要 patch 源模块）——本轮是该教训的正面运用：**依赖注入点
放在函数内 import 源模块，是"零依赖注入参数 + 可 mock"的两全做法**。

### 3. 测试 mock 双层罩住策略成功防住真实 config 写入（8-15 事故教训的直接复用）
test_settings_agent_tab 的 `_make_dialog` 罩构造期 load/save，`test_agent_tab_save`
再用第二个 with 块罩住 `dlg._save()` 调用——上轮"真实 data/config.json 被覆盖丢 key"
事故的修复模式首次在新测试中落地即通过。**教训**：凡测 `_save()` 类落盘方法，
mock 作用域必须显式罩住被测调用行，不能依赖构造期的 patch 残留。

### 4. 执行计划前核实依赖签名，本次零偏离完成 Task 4-6
动手前 Grep 核实了 run_local_run/run_hermes_run 签名、config 四常量、APP_DIR、
settings_dialog 的锚点行与 QWidget 导入，全部与计划假设一致，三个 task 一次通过。
**教训**：lessons（8-15 superpowers 教训 4"执行旧计划前先核实文件现状"）的成本
极低（3 个并行 Grep），收益是整个实施期零返工。

### 5. codex 真机事件与 fixture 的宽容契约设计经受住了校准
parse_event_line 对未知结构返回 None 的"宽容契约"让 codex 版本间事件格式差异
被隔离在适配层；真机校准（Task 3 已做）确认 agent_message 全量快照语义后，
Task 4-6 的流式增量转换（快照→增量）未再出问题。**教训**：跨进程 JSONL 适配
层宁可漏事件（None）不可错解析，配合"产物文件是真相兜底"的双通道设计，
流式 UI 与最终回复可以各自容错。

## 2026-08-15 superpowers 计划收尾（P0 Task4-7 + Agent Task6 + Phone Task9）

### 1. 【事故】测试 mock 作用域没罩住被测调用，真实 data/config.json 被覆盖丢 key
test_settings_about 的 _save 测试：_make_dialog 里 patch 的 save_config 随 with 块退出，
dlg._save() 调到了真实 save_config，把只含表单默认值的部分 config 写进 data/config.json，
用户真实 api_key/asr_api_key 等全部丢失（该文件在 gitignore，无法从 git 恢复）。
**教训**：测落盘方法时，load/save 的 mock 作用域必须完整罩住被测调用；
真实 config.json 这类敏感不可恢复文件，改配置类代码前先手工备份一份。

### 2. PATH 里的 python 未必是项目环境，本项目命令一律用绝对路径
Hermes 安装后 `C:\Users\23733\AppData\Local\hermes\...\venv\Scripts\python.exe` 抢占了 PATH，
`python -m pytest` 报 No module named pytest。
**教训**：本项目（anaconda base 是运行环境）所有 python 命令显式用 `D:\anaconda\python.exe`。

### 3. 延迟导入的依赖要 patch 源模块，且 conftest 注入 .libs 路径
DDGS 改为 desktop_tools 函数内延迟导入后，`patch("core.desktop_tools.DDGS")` 报
AttributeError（模块级无该属性）。正确做法 `patch("ddgs.DDGS")`（patch 导入源）。
mss 装在 .libs（pip --target），测试进程看不到 → 新建 conftest.py 把 .libs 插入 sys.path。
**教训**：mock 跟着 import 位置走；--target 安装的依赖需要 conftest 统一注入路径。

### 4. 执行旧计划前先核实文件现状，按现实适配而非照抄计划
P0 Task4 计划假设设置页有 Hermes tab（实际无）；Agent Task6 计划假设 openclaw_runner.py
不存在（实际 _operate_gui 已用 Gateway HTTP API 实现，只缺测试）；Amadeus.spec 计划里
说修改（实际文件已丢失，需新建）。
**教训**：逐条执行计划文档前，先 grep/glob 核实每个目标文件的真实状态，
"改什么"以磁盘为准，计划只是意图。

### 5. PowerShell 给原生命令传含双引号的参数会被拆碎
`git commit -m $msg`（$msg 是 here-string，内含 "Chat 模型"）→ 双引号在 Win32 参数
解析时被剥掉，git 收到断裂的多段参数报 pathspec 错误。
**教训**：commit message 里避免英文双引号（中文引号安全），或改用 `git commit -F 文件`。

## 2026-08-15 流式 TTS 句间间隔优化（双缓冲预取 vs 短句合并）

### 1. 【算法证伪】短句合并在 GPT-SoVITS batch_size=5 + cut5 下反而更慢 12%
**实验**：4 句短句（はい。/そうね。/うん。/わかった。）单独合成 13.70s vs 合并 15 字合成 15.35s，
合并反而慢 1.65s。**原因**：batch_size=5 在 cut5 切分回 4 句后实际是**串行推理**而非并行
（15.35s ≈ 4 × 3.84s），batch 没生效。**致命代价**：首句延迟从 3.4s 暴涨到 15.35s。
**结论**：短句合并撤销。**教训**：算法优化不能只看 API 调用次数指标（4→1），必须实测端到端
耗时和首句延迟。batch_size 参数在 cut5 切分后不保证并行，需实测验证。

### 1b. 【算法反转】逗号连接合并让 batch 真正并行，撤销之前的撤销决策
**关键发现**：之前撤销短句合并是因为用**句号**连接（はい。そうね。うん。わかった。），
cut5 切分后 4 段长度差异大（2-4字），split_bucket 分到不同桶无法 batch，串行 14.31s。
改用**逗号**连接（はい、そうね、うん、わかった。），4 段长度相似（2-4字），分到同桶，
batch_size=5 并行生效，合成 4.30s（≈单段时间）。**端到端收益**：6 句话从 23.72s→9.61s（-59%），
句间间隔从 11.99s→0s（-100%）。**教训**：split_bucket 按段长分桶，段长相似才能 batch。
合并短句用逗号（非句号）连接，让 cut5 切分后段长均匀，batch 并行才生效。
参考：core/tts_client.py _dispatch_sentence, _flush_merge_buffer。

### 2. 【算法证实】双缓冲预取在合成 ≤ 播放时 100% 消除句间间隔
**实验**：3 句典型日语（23-25 字），合成时间 S_i ∈ [4.28, 4.86]s，播放时间 P_i ∈ [5.39, 7.48]s。
所有 S_i < P_i（GPU 推理 4-5s vs 音频播放 5-7s），双缓冲理论句间间隔 = Σ max(0, S_{i+1}-P_i) = 0s。
串行模型句间总间隔 = S_2+S_3 = 9.14s，双缓冲 = 0s，**收益 9.14s（100%）**。
**物理意义**：合成用 GPU（CUDA kernel launch），播放用 CPU/音频设备，不同硬件资源天然并行。
**教训**：双线程用队列解耦是经典的 producer-consumer 模式，核心是把"等待"转为"预取"。

### 3. 实现：合成线程和播放线程用 _playback_queue 解耦
**架构**：合成循环（_stream_consumer）从 _stream_queue 取句→合成→塞 _playback_queue；
播放循环（_playback_worker）从 _playback_queue 取 wav→播放。两线程独立调度。
**stop() 必须唤醒两个队列**：合成线程阻塞在 _stream_queue.get，播放线程阻塞在 _playback_queue.get，
stop() 要往两个队列都 put(None) 唤醒，否则任一线程卡死会让 speaking_changed 信号悬空。
参考：core/tts_client.py _stream_consumer, _playback_worker, stop。

### 4. 验证 Live2D 渲染的"帧管道产物"原则再次复用
8-15 fauux 教训"验证分层窗口渲染看 received-frame.png 的尺寸/色彩数/不透明率"再次复用。
本轮桌宠重启后用 received-frame.png 133KB 证明 Live2D 正常，不信屏幕抓图（GDI 抓透明窗口全黑）。
**教训**：UI 验证看文件产物（data/desktop_pet.ready + data/received-frame.png），不看终端输出。

### 5. pythonw.exe 启动后进程消失，改用 python.exe 后台运行保留 stdout 诊断
本轮第一次 Start-Process pythonw.exe main.py 启动后桌宠进程消失（无错误信息）。
按 8-14 教训 5"pythonw 静默吞错误"改用 `python.exe main.py 2>&1` 后台运行，stderr 重定向到
stdout，能看到启动日志。**教训**：pythonw.exe 启动失败时改用 python.exe 前台/后台跑捕获 stderr；
启动成功后再换回 pythonw.exe 释放终端。诊断优先于美观。

## 2026-08-16 companion 主动问候实施（Task 1-8）

### 1. Plan 接入点架构假设需核实：行号与运行时对象可用性都要验证
plan 假设 desktop_pet.py 接入点在 276 行 `class AgentSignals` 之前，但实际接入点应在
1248 行 `pet = PetWindow()` 之后、run_overlay 函数内部（1257 行导入 companion 模块）。
原因：plan 假设 `_agent_delta`/`_show_status` 是 run_overlay 闭包内函数，实际是 PetWindow
的方法，接入必须在 pet 实例创建后才能引用 pet 的方法。**教训**：集成任务的 subagent 必须
先读全量代码确认接入点的运行时对象可用性（对象已创建 + 在正确作用域），不依赖计划文档的
行号假设；plan 的行号是计划阶段静态读码的产物，实施期代码可能已变。
参考：desktop_pet.py:248 (run_overlay), :276 (AgentSignals), :1248 (pet=PetWindow()),
:1257 (companion 导入)。

### 2. desktop_pet.py 顶部禁止 import PySide6 约束再次复用
Task 6 严格遵守 8-15 严重事故教训（"desktop_pet.py 主进程禁止顶部导入 PySide6"），
companion 模块导入（core.companion.controller / sensors / storage）全部放在 run_overlay
函数内部（1257 行），QTimer 直接复用 run_overlay 顶部 249 行已 import 的
`PySide6.QtCore.QTimer`，未在文件顶部引入任何新的 import。**教训**：desktop_pet.py 的
主进程（run_overlay 函数之前）绝对不能 import PySide6/Qt，否则子进程的 QtWebEngine 渲染
必崩（8-15 事故已证实）。该约束是项目级红线，所有接入 desktop_pet.py 的功能都必须遵守。
依据：8-15 Live2D 渲染崩溃事故教训 1；desktop_pet.py:249 (run_overlay 内 QTimer 导入),
:1257-1262 (companion 延迟导入)。

### 3. Plan 测试与实现的不自洽需最小修复：TDD 的 RED→GREEN 是发现 plan 缺陷的反馈机制
Task 1 发现 fake_run_local_run 返回元组（plan 写 `return "reply", "chat"`）与 run_local_run
真实签名（返回 str）不符；Task 2 发现测试 import 列表有 CompanionStorage 但 plan 实现没
定义该名字；Task 3 发现 ClipboardSensor 测试未传 enabled=True 导致默认禁用。**教训**：
Plan 代码 ≠ 可运行代码，TDD 的 RED→GREEN 阶段是发现 plan 缺陷的反馈机制，发现不自洽时
最小修复并报告偏离原因，不盲目照抄也不盲目重写。这是 8-13 电话模式教训 1"Plan 代码 ≠ 可运行
代码"的再次验证。

### 4. PowerShell 5.1 写 UTF-8 文件默认带 BOM，git commit -F 会把 BOM 塞进 commit message
用 `Set-Content -Encoding UTF8` 写 commit message 文件，`git commit -F` 会把 BOM 字符
塞进 commit message 第一行（变成 `﻿feat`）。正确做法用 .NET API：
`[System.IO.File]::WriteAllText($path, $msg, [System.Text.UTF8Encoding]::new($false))`
显式无 BOM。**教训**：PowerShell 5.1 的 UTF8 编码默认带 BOM，git 操作（commit -F / tag -F /
rebase -F 等读文件的命令）必须用 .NET API 显式无 BOM；8-13 教训 4"PowerShell 不支持 bash
heredoc 用 -F 文件"在本轮升级为"-F 文件还要保证无 BOM"。

### 5. subagent 全量回归用 pytest tests/ 而非裸 pytest
项目无 pytest.ini，裸 `pytest` 会递归扫描 .venv / gpt_sovits_venv / .libs/numpy 等目录，
导致 156 个 collection error（第三方库里的 test_*.py 被误收集）。**教训**：本项目全量回归
必须显式指定 tests/ 目录（`pytest tests/`），或在项目根放 pytest.ini 配置
`testpaths = tests`。subagent 执行 plan 里的 `python -m pytest tests/ -v` 时不能图省事
省略 tests/ 路径参数。本轮全量回归 171 passed（既有 129 + companion 新增 42）。

## 2026-08-17 amadeus TTS 方案照搬（阿里云 CosyVoice 流式 + 通话流式 TTS）

### 1. 【架构】流式 TTS 通话方案照搬：=== 分隔符检测统一中日分流
通话功能的流式 TTS 触发逻辑与普通对话完全一致：检测 LLM 输出 `\n===\n` 分隔符，
仅日语段送 TTS 合成（中文段不送）。voice_call._on_llm_delta 照搬 desktop_pet._agent_delta，
避免通话场景重复造轮子。**教训**：跨场景（普通对话/通话）的 LLM 解析逻辑应统一为
可复用模块（如 core/llm_stream_parser.py），而非每个调用方各自实现一份 === 检测。
当前两处重复：desktop_pet.py:1232-1250（_agent_delta） / voice_call.py:_on_llm_delta。
依据：desktop_pet.py:1232-1250, core/voice_call.py:_on_llm_delta。

### 2. 【测试】删除方法前必须 grep 测试引用：_play_tts 删除导致 3 个测试失败
voice_call.py 删除 `_play_tts` 方法后，test_voice_call.py 三个测试
（test_utterance_end_attaches_screen_frame / test_screen_share_off_skips_vision /
test_vision_empty_key_skips_vision）`patch.object(ctrl, "_play_tts")` 失败
（AttributeError: does not have the attribute）。修复：替换为
`patch.object(ctrl._tts, "speak_with_options")`。**教训**：删除实例方法前必须
grep 测试代码引用，提前修复测试 mock；pytest collection 阶段不报错不代表运行时
不报错。参考：tests/test_voice_call.py:52-98。

### 3. 【测试】config.py 默认值改动会波及所有调用方：TTS_PROVIDER_DEFAULT=aliyun 导致 test_tts_selfheal 失败
config.TTS_PROVIDER_DEFAULT 改为 "aliyun" 后，test_tts_selfheal 测试 `patch.object
(player, "_check_kurisu")` 不再被调用（_check_provider_available 走 aliyun 分支不调
_check_kurisu），test_maybe_start_spawns_when_offline 也因 provider=aliyun 直接返回
False。修复：`_make_player` 强制 `player._get_tts_provider = lambda: "gpt_sovits"`，
maybe_start 测试 `patch("core.storage.load_config", return_value={"tts_provider":
"gpt_sovits"})`。**教训**：改默认配置常量时必须 grep 所有依赖该常量的代码（含测试），
评估波及面；test_tts_selfheal 套件 mock 的是 gpt_sovits 路径，必须显式强制 provider。

### 4. 【环境】测试环境分裂：miniaudio 装在 .venv，PySide6 DLL 加载失败；anaconda 有 PySide6 无 miniaudio
.venv 装了 miniaudio 1.71 但 PySide6 6.11.1 DLL 加载失败（`ImportError: DLL load failed
while importing QtCore`）；D:\anaconda 装了 PySide6 但没装 miniaudio。两个环境各缺一样东西。
解决：test_mp3_decoder.py 顶部 `sys.modules['miniaudio'] = MagicMock()` 注入 fake 模块，
让测试不依赖真实 miniaudio 安装。D:\anaconda 跑测试 208 passed。**教训**：测试环境依赖
分裂时，对纯 IO/解码库（如 miniaudio）注入 fake 模块到 sys.modules，避免环境耦合；
测试用例内部仍可 `patch("miniaudio.stream_any")` 覆盖具体方法。依据：
tests/test_mp3_decoder.py:1-14。

### 5. 【mock】patch 路径要匹配 import 方式：函数内 `from urllib.request import urlopen` 不能 patch 模块属性
mp3_decoder.py 中 _HttpStreamableSource.__init__ 用 `from urllib.request import urlopen`
局部导入，urlopen 不在 mp3_decoder 模块命名空间。`patch("core.mp3_decoder.urlopen")` 报
`AttributeError: <module 'core.mp3_decoder'> does not have the attribute 'urlopen'`。
正确：`patch("urllib.request.urlopen", return_value=fake_resp)`（patch 导入源模块）。
**教训**：unittest.mock.patch 路径必须匹配实际 import 语句——
- 模块级 `import urllib.request; urllib.request.urlopen(...)` → `patch("urllib.request.urlopen")`
- 模块级 `from urllib.request import urlopen; urlopen(...)` → `patch("core.mp3_decoder.urlopen")`（如果模块级 import）
- 函数内 `from urllib.request import urlopen` → 必须 `patch("urllib.request.urlopen")`，不能 patch 调用方模块
依据：tests/test_mp3_decoder.py:30/41/52/63/96/113。

## 2026-08-17 设置页 tab 合并（TTS 三合一）

### 1. 【UI 重构】QFormLayout 复用合并 tab：所有 addRow 加到同一 form，_section 视觉分组
设置页原 8 tab 体验割裂。合并方案：删除独立 `gpt_page`/`aliyun_page` QWidget 创建语句，
让 `gpt_form = voice_form`、`aliyun_form = voice_form` 直接复用 voice_form。所有 addRow
按视觉顺序追加到同一 form：voice 通用项 → GPT_SOVITS BACKEND section → ALIYUN BAILIAN TTS section。
`_section("XXX")` 返回的 QLabel（带左边框样式）充当视觉分组锚点，免新建 widget。**教训**：
Qt 多 tab 合并的本质是「QFormLayout 复用 + _section 视觉分组」，不需要为每个分区新建
QWidget/QScrollArea；先合并 form 后再 `tabs.removeTab` 旧 tab 即可。依据：
ui/settings_dialog.py:120-220（合并后单 tab 内三段 section）。

### 2. 【流程】删 tab 前先 grep 测试引用：settings_dialog 测试可能直接断言 tab 数
合并 tab 前担心 test_settings_*.py 里 `tabs.count()` 或 `tabs.tabText("GPT-SoVITS")` 断言
失败。先 `Select-String -Path tests\*.py "tabText|tabText|tabs.count|GPT-SoVITS.*tab|阿里云.*tab"`
确认无断言后再删。本次合并未破坏既有 settings 测试（test_settings_about/agent/companion）。
**教训**：UI 结构变更（tab 增删/控件重排）前必须 grep 测试代码对结构的断言，避免运行时
IndexError/AttributeError；pytest collection 阶段不报错不代表运行时不报错（8-17 教训 2 复用）。

### 3. 【用户偏好】同类型分区合并是 user_profile 已记录的 UI/UX 偏好
user_profile.md 明确记录「设置页 8 个 tab 合并方案 = TTS 三合一 + 模型二合一」是用户偏好。
合并 TTS 三合一（GPT-SoVITS + 阿里云 + 通用语音输出）属于直接落地该偏好。模型二合一
（chat 模型 + vision 模型合并为「模型」tab）是后续工作。**教训**：UI/UX 改动前先查
user_profile.md，已有的偏好直接落地不重新征求，未记录的偏好用 AskUserQuestion 确认。

### 4. 【git】定向 add 排除无关改动（8-13 教训 5 第六次复用）
本次工作区有 6 个无关改动（codex_client/backend_router/companion/controller + 3 个对应 test，
属于"codex 记忆注入 + companion on_finished 回调"另一项工作）+ 大量未追踪调试产物
（probe4.log/probe4_err.log/scripts/mic_probe.py/AMDS-RE/tait-crt-interface-skill/data/）。
git add 时严格定向 16 个 TTS 任务文件 + lessons.md，不动其他。**教训**：本项目工作区长期
有"半成品多任务并行"状态，每次 commit 都用定向 add，commit 纯度是回溯诊断的前提。
PRD_aliyun_tts.md 已过时（写明不做流式、仅 Qwen3-TTS-VC，与当前 CosyVoice 多引擎实现冲突），
本次不提交，待后续修订 PRD 时单独提交。

### 5. 【文档】PRD 与实现冲突时优先标记过时而非删除
PRD_aliyun_tts.md 1.3 非目标写「不做流式合成」「仅 Qwen3-TTS-VC」「不做 CosyVoice」，
与当前实现（CosyVoice v3.5-flash 默认 + 流式 OSS URL 播放 + 多引擎下拉）正面冲突。
处理选择：(a) 不入库待后续修订（本次采用）；(b) 一并入库作为历史归档；(c) 当场修订。
选 (a) 的理由：本次 commit 主题是「TTS 方案照搬」，修订 PRD 是独立工作（需要重新设计
CosyVoice 多引擎架构图、流式时延预算等），混入会让 commit 主题不纯。**教训**：过时 PRD
不应删除（承载历史决策上下文），但也不应与冲突的实现混在同一 commit；标记「已过时」
单独留作后续修订任务，是文档与代码解耦的标准做法。

## 2026-08-17 真机无声 bug 修复（engine 与 voice_id 命名空间互斥）

### 1. 【根因】CosyVoice 系列与 Qwen3-TTS-VC 音色命名空间互斥：418 错误是铁证
用户报告 amadeus（TypeScript/浏览器）能听到语音，amadeus-py（Python）听不到，同一个
阿里云 API key。控制变量实验（固定 api_key+voice_id，切换 engine）：cosyvoice-v3.5-flash
+ qwen-tts-vc- 前缀音色 → HTTP 400 InvalidParameter "Engine return error code: 418"；
qwen3-tts-vc + qwen-tts-vc- 前缀音色 → 成功 mp3 103724 bytes。**根因**：用户已克隆的是
Qwen3-TTS-VC 音色（qwen-tts-vc- 前缀），CosyVoice 系列引擎不识别该命名空间的音色 ID。
**教训**：阿里云 TTS 的 engine 与 voice_id 是绑定关系，不是自由组合——Qwen3-TTS-VC 引擎
只能用 Qwen3-TTS-VC 克隆的音色（multimodal-generation/generation endpoint），
CosyVoice 系列引擎只能用预置音色或 CosyVoice 自有的克隆音色（SpeechSynthesizer
endpoint）。移植外部项目时不能照搬默认值，要核实用户的音色来源与 engine 是否匹配。
依据：scripts/diagnose_aliyun_tts.py 三组控制变量实验。

### 2. 【反转发现】CosyVoice 预置音色也 418：账号未授权 CosyVoice 服务
实验 3 用 CosyVoice 预置音色 longxiaochun（不是 Qwen3 克隆音色）调 cosyvoice-v3.5-flash，
仍返回 418。这说明 418 不只是 engine 与 voice_id 不匹配，更深层原因是该阿里云账号未开通
CosyVoice 服务授权。amadeus 原项目默认 cosyvoice-v3.5-flash 是基于"账号已授权 CosyVoice"
的隐式假设。**教训**：移植外部项目的默认值时，要识别其隐式假设（amadeus 默认 cosyvoice
隐式假设用户已开通 CosyVoice 服务）；本地化默认值要按"用户最可能的状态"重设——本项目
用户已克隆 Qwen3-TTS-VC 音色，默认 engine 应为 qwen3-tts-vc。依据：实验 3 仍 418。

### 3. 【环境】测试环境分裂复发：miniaudio 装在 .venv，anaconda 缺失导致 frozen 模式无声
诊断脚本实验 2 合成成功后报 "No module named 'miniaudio'"——anaconda（桌宠运行环境）
没装 miniaudio。8-17 教训 4 已记录此环境分裂，本次复现。修复：`D:\anaconda\python.exe
-m pip install miniaudio`（需 dangerouslyDisableSandbox=true，沙箱拒写 site-packages，
8-14 教训 3 第三次复现）。**教训**：新依赖装到 anaconda 后必须验证 QtWebEngine 没被破坏
（8-15 严重事故 2 教训）——`import PySide6.QtWebEngineCore` 三行验证脚本即可。本次验证
miniaudio 1.71 + sounddevice 0.5.5 + QtWebEngine 三者共存正常，无 DLL 冲突。

### 4. 【frozen】PyInstaller hiddenimports 必须显式列动态 import 的库
core/mp3_decoder.py 用 `import miniaudio`（函数内/模块顶部均可），PyInstaller 静态分析
可能漏抓。Amadeus.spec hiddenimports 原列 ['pywebview.platforms.edgechromium', 'ddgs',
'trafilatura', 'mss']，新增 'miniaudio'。**教训**：frozen exe 不读 D:\anaconda\Lib\
site-packages，所有运行时依赖必须打包进 _MEIPASS。函数内动态 import 的库（miniaudio、
win32com.client 等）PyInstaller 静态分析漏抓概率高，必须在 spec hiddenimports 显式列出。
依据：Amadeus.spec:23-27 hiddenimports 新增 'miniaudio'。

### 5. 【方法论】控制变量法定位 bug：固定自变量 + 切换因变量 + 实测对照
本次 bug 排查用控制变量法：固定 api_key + voice_id（用户配置），切换 engine 实测 3 组
（cosyvoice+Qwen3音色 / qwen3-tts-vc+Qwen3音色 / cosyvoice+预置音色）。3 组结果对比立即
定位根因（engine 与 voice_id 不匹配 + 账号未授权 CosyVoice）。**教训**：用户报告"X 项目
能听到语音，Y 项目听不到，同一个 API"时，控制变量法是首选诊断手段——固定 API key/voice_id
等公共配置，切换客户端实现（engine/解码路径/播放层）实测，3 组实验即可定位。不要凭代码
逻辑猜根因（"我觉得 miniaudio 没问题"），必须用实测证据证伪或证实。参考：
scripts/diagnose_aliyun_tts.py 是控制变量实验的可执行模板。

## 2026-08-17 多段 === LLM 输出 + 气泡截断 bug 修复

### 1. 【根因】LLM 输出 75% 概率为多段「中文1 === 日语1 \n\n [emotion:xxx]中文2 === 日语2」
用户报告"一段日文一段中文"。查 dist/data/characters/kurisu/sessions.json 最近 4 条 LLM 回复，
3 条（75%）是多段交替格式：LLM 把"短句回应+详细展开"拆成两段，每段都带 === 中日双语，
段间用空行 + [emotion:xxx] 标签分隔（非 ===）。原版 _on_llm_delta 用 `split("===", 1)[1]`
只切第一个 ===，把第一段日语 + 第二段中文 + 第二段日语整体送 TTS，Qwen3-TTS-VC 用日语
phoneme 读中文 → 听起来"一段日文一段中文"。**教训**：LLM 输出格式不稳定是常态，不能假设
LLM 一定按 KURISU_OUTPUT_FORMAT 输出。移植外部项目时，要实测真实 LLM 输出格式，不能只看
prompt 约定。依据：dist/data/characters/kurisu/sessions.json 3/4 多段样本。

### 2. 【算法】[emotion:xxx] + === 双重切段状态机
原版只用 === 切段，多段 === 时把"日语1 + 中文2"整体送 TTS。修复方案：双重切段——
- 先按 [emotion:xxx] 切分：每个 emotion 标签视为新回复开始，重置 _stream_sep_count=0
- 再按 === 切分：每遇 === 切段，_stream_sep_count += 1
- 奇数段（1,3,5...）= 日语段 → 追加 TTS
- 偶数段（0,2,4...）= 中文段 → 跳过
**数学本质**：[emotion:xxx] 是 LLM 标记的新回复边界（段间分隔符），=== 是回复内部的中日
分界（段内分隔符）。两层分隔符层次不同，必须双重切段才能正确识别多段输出中的日语段。
**形象理解**：像漫画分镜，[emotion:xxx] 是新一格的开始，=== 是一格内部的中日字幕分界。
原版只看 === 把"日语格1 + 中文格2"当一个日语格读。依据：desktop_pet.py:1234-1291
_agent_delta + _append_tts_segment；core/voice_call.py:338-396 同步实现。

### 3. 【UI】_latest_line 105 字截断 + 高度上限 140px 双重截断长回复
用户报告"气泡没办法显示所有内容，分段后只显示前面几句，后面的不显示"。查 desktop_pet.py:
- _latest_line (line 1093-1097)：只取最后 3 行 + 105 字截断（`latest[:104] + "…"`）
- _set_bubble_text (line 867)：高度上限 140px（`h = min(..., 140)`）
长回复被双重截断。修复：_latest_line 返回完整 chinese（去掉 [emotion:] 标签，保留所有
中文段）；_set_bubble_text 高度上限放宽到 240px（屏幕高度的 1/4）。**教训**：UI 显示层
截断是历史遗留（早期回复短），LLM 输出变长后暴露。真正滚动需 QScrollArea 包 QLabel，
本次先做最小改动（不截断 + 高度放宽），如用户仍不满意再改 widget 类型。依据：
desktop_pet.py:1092-1101 _latest_line + 859-876 _set_bubble_text。

### 4. 【测试】多段 === + emotion 标签场景测试覆盖
新增 test_on_llm_delta_multi_separator_skips_chinese_segments 测试，模拟 LLM 多段输出
（段1无 emotion 标签，段2有 [emotion:neutral]）。断言：
- _stream_sep_count == 1（emotion 重置后最后一段日语）
- mock_append.call_count == 2（两段日语各追加 1 次，中文段不追加）
- 追加内容不含中文段（"中文长句2" / "中文短句1" 都不在 appended_texts 中）
**教训**：bug 修复必须配套回归测试，覆盖真实 LLM 输出格式（多段 === + emotion 标签交替）。
单元测试用 mock 模拟 LLM delta 序列，验证 TTS 只送日语段不送中文段。依据：
tests/test_voice_call.py:181-208。

### 5. 【frozen】exe 重新打包验证修复
修复后需重新打包 exe（frozen 模式不读源码，必须重新 PyInstaller）。版本号 0.3.0 → 0.3.1
（bug fix 按 SemVer 升 patch）。Amadeus.spec hiddenimports 已含 miniaudio（0.3.0 加的），
无需再改。**教训**：每次代码修复后，frozen exe 用户必须重新下载新版本才能生效。源码模式
（python desktop_pet.py）用户直接拉代码即可。打包前必须全量 pytest 通过（209 passed），
避免 frozen 模式运行时才发现 bug（frozen 模式调试成本高，无 stderr 实时输出）。

## 2026-08-17 双重切段引入无声 bug 修复（0.3.1 → 0.3.2）

### 1. 【回归】0.3.1 引入新 bug：双重切段把日语段错当中文段，TTS 全跳过 → 无声
用户报告"这一版没有语音声音了"。0.3.0 能听到语音，0.3.1 无声。查 dist/data/sessions.json
最近一次 LLM 回复（1098 字，5 个 === + 2 个 [emotion:]）：LLM 把 [emotion:xxx] 标签放在
=== 后的日语段里（如「日语1 \\n\\n [emotion:neutral]中文2 === 日语2」），违反 prompt
约定"情绪标签在最开头"。0.3.1 的双重切段逻辑用 [emotion:xxx] 重置 sep_count=0，把日语段1
（含 [emotion] 标签）错当新回复的中文段，后续 sep_count 全错位 → TTS 全跳过 → 无声。
**教训**：bug 修复引入新 bug 是常见陷阱（修复 A 暴露 B）。每次修复后必须实测 frozen exe
验证，不能只靠单元测试通过就认为修复成功——单元测试 mock 的 LLM 输出格式与真实输出
格式可能不同。依据：dist/data/characters/kurisu/sessions.json 真实 LLM 输出。

### 2. 【根因】LLM 不遵守 prompt 约定是常态，不能按约定设计切段逻辑
KURISU_OUTPUT_FORMAT 明确写"[emotion:xxx]在最开头"+"=== 独占一行"+"日语之后不输出
任何内容"。但实测 LLM 输出：
- [emotion:xxx] 出现在 === 后的日语段里（不是开头）
- 段间用空行 + [emotion:xxx] 分隔（不是 ===）
- 单次回复含多段 === 交替（75% 概率）
原版 _on_llm_delta 假设单段 === 格式（split('===', 1)[1]），0.3.1 假设 [emotion] 在 === 前
（双重切段重置）。两者都基于 prompt 约定设计，实际 LLM 输出都不遵守。
**教训**：移植外部项目或设计 LLM 输出解析逻辑时，必须实测真实 LLM 输出格式（查
sessions.json），不能按 prompt 约定设计。LLM 输出格式不稳定是常态，解析逻辑要鲁棒到
容忍各种格式变体。

### 3. 【算法】纯 === 切段 + 假名过滤：不依赖 [emotion] 标签位置
放弃用 [emotion] 重置 sep_count。新方案：纯 === 切段，对每段去 [emotion] 标签后按空白行
切块，每块用 has_japanese() 判断含假名则送 TTS，跳过纯中文段。
- 数学本质：日语必有假名（U+3040-309F 平假名 + U+30A0-30FF 片假名），CJK 汉字
  中日韩共用手写汉字（U+4E00-9FFF）无法区分但日语段必含假名。
- 形象理解：每段用 === 切开后扫假名，有假名的是日语段送 TTS。
- 边界场景：纯汉字日语词（如"元気"）无假名会被误判为中文跳过，但实际 LLM 输出的
  日语句子几乎都有假名结尾（助词/句尾），此边界场景罕见。
依据：desktop_pet.py:1277-1304 _append_tts_segment_by_japanese；
core/voice_call.py:364-396 同步实现。

### 4. 【测试】测试数据要真实，mock 不能用纯汉字"日语"词
0.3.1 测试用例 mock_append 期望"日本語短句1"被追加，但"日本語"3 字都是 CJK 汉字无假名，
被 has_japanese() 误判为中文跳过。改用真实日语"ええ、どうしたの？"（含平假名）。
**教训**：TTS 相关测试用例必须用真实日语样本（含平假名/片假名），不能用"日本語"这种
看似日语但纯汉字的词。CJK 汉字无法区分中日韩，has_japanese 判断依赖假名存在。
依据：tests/test_voice_call.py:182-207 test_on_llm_delta_multi_separator_skips_chinese_segments。

### 5. 【控制变量】frozen vs 源码模式差异排查：frozen exe 内 miniaudio DLL 检查
排查"0.3.1 无声"时先怀疑 frozen exe 没打包 miniaudio。用 PyInstaller CArchiveReader
检查（API 在 6.21 版本变了，archive_viewer 命令行更稳定）。实测 0.3.0 和 0.3.1 exe
大小几乎一样（123551454 vs 123552035 bytes），说明 miniaudio 已打包，排除环境因素。
**教训**：frozen 模式 bug 排查先排除环境因素（依赖打包/DLL 冲突），再查代码逻辑。
版本间 exe 大小对比是快速判断"是否打包层面变化"的指标——大小相近说明打包无变化，
bug 在代码逻辑层。本次 0.3.0 → 0.3.1 exe 大小差 581 bytes，说明仅代码层变化。
