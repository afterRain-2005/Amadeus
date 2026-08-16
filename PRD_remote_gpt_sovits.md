# PRD：GPT-SoVITS 远程化（SSH 隧道）

## 1. 背景与目标

### 1.1 现状
- 桌宠 `maybe_start_gpt_sovits()` 在本地拉 GPT-SoVITS 子进程，要求本机有 GPU（≥6GB VRAM）
- `core/gpt_sovits_client.py` 的 `KurisuTTS` 硬编码 `http://127.0.0.1:9880`，但构造函数已支持 `base_url` 参数
- 笔记本 GPU 弱时本地启动失败，回退 SAPI（机械音，体验差）

### 1.2 目标
- 支持通过 SSH 隧道连接远程 GPU 服务器上的 GPT-SoVITS
- 保留本地启动模式作为兜底
- 设置页面可视化配置：读取本机 `~/.ssh/config`、选择 Host、测试连接
- 用户场景：笔记本算力不足时，用 202.114.107.184（user=yangjing, port=9922）的 GPU 跑 TTS

### 1.3 非目标
- 不做远程 HTTP 直连（端口暴露公网不安全）
- 不做多服务器负载均衡
- 不做服务器端 GPT-SoVITS 自动部署（手动一次性部署）

## 2. 架构设计

### 2.1 三种 TTS 模式

| 模式 | 启动方式 | 适用场景 |
|---|---|---|
| `local` | `maybe_start_gpt_sovits()` 拉本地子进程 | 本机有 GPU |
| `ssh` | `ssh -L 9880:localhost:9880 <host> -N -f` 建隧道 | 远程 GPU 服务器 |
| `auto` | 优先 SSH，失败回退本地，再失败回退 SAPI | 默认 |

### 2.2 SSH 隧道工作原理

```
[本地桌宠] --http--> 127.0.0.1:9880 --ssh隧道--> [远程服务器] localhost:9880 --http--> GPT-SoVITS API
```

- 本地 `KurisuTTS` 仍然连 `127.0.0.1:9880`（**代码零改动**）
- SSH 隧道把本地 9880 转发到远程 9880
- 隧道用 `-N -f` 后台运行，关闭桌宠时 kill

### 2.3 模块划分

```
core/
├── tts_client.py          # 不改
├── gpt_sovits_client.py   # 不改（base_url 仍为 127.0.0.1:9880）
├── ssh_tunnel.py          # 新增：SSH 隧道管理器
└── ssh_config_parser.py   # 新增：解析 ~/.ssh/config

ui/
└── settings_dialog.py     # 新增「GPT-SoVITS」tab

desktop_pet.py             # maybe_start_gpt_sovits 增加分支：ssh 模式建隧道
```

## 3. 服务器端部署步骤（一次性手动）

### 3.1 连接服务器
```bash
ssh 202.114.107.184
# 已在 ~/.ssh/config 配置：user=yangjing, port=9922
```

### 3.2 检查 GPU
```bash
nvidia-smi
# 确认有 GPU 且显存 ≥6GB，CUDA 版本 ≥11.8
```

### 3.3 克隆 GPT-SoVITS
```bash
cd ~
git clone https://github.com/RVC-Boss/GPT-SoVITS.git
cd GPT-SoVITS
```

### 3.4 创建 conda 环境
```bash
conda create -n gpt_sovits python=3.10 -y
conda activate gpt_sovits
```

### 3.5 安装 PyTorch（CUDA 版本按 nvidia-smi 显示选择）
```bash
# CUDA 11.8
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
# 或 CUDA 12.1
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### 3.6 安装 GPT-SoVITS 依赖
```bash
pip install -r requirements.txt
pip install pyopenjtalk-plus  # 日语支持（与本地一致）
```

### 3.7 下载预训练模型
```bash
# GPT-SoVITS 需要下载基础模型到 GPT_SoVITS/pretrained_models/
# 参考 https://github.com/RVC-Boss/GPT-SoVITS#pretrained-models
# 通常用脚本：
python download_models.py  # 如果仓库有提供
# 或手动从 huggingface 下载
```

### 3.8 上传红莉栖音色样本
```bash
# 在本地执行，把音色样本传到服务器
scp -P 9922 resources/voice_sample_clip_v2.wav yangjing@202.114.107.184:~/GPT-SoVITS/
```

### 3.9 配置 tts_infer.yaml
```bash
# 编辑 GPT-SoVITS/GPT_SoVITS/configs/tts_infer.yaml
# 设置：
#   device: cuda
#   is_half: true
#   custom:
#     path: /home/yangjing/GPT-SoVITS/voice_sample_clip_v2.wav
#     text: （与本地一致的 prompt 文本）
#     language: ja
```

### 3.10 启动 API server（前台测试）
```bash
cd ~/GPT-SoVITS
conda activate gpt_sovits
python api_v2.py --host 127.0.0.1 --port 9880
# 看到 "API started" 表示成功，Ctrl+C 先停掉
```

### 3.11 配置后台常驻（可选，用 tmux）
```bash
tmux new -s gpt_sovits
cd ~/GPT-SoVITS
conda activate gpt_sovits
python api_v2.py --host 127.0.0.1 --port 9880
# 按 Ctrl+B 然后 D 脱离 tmux，服务继续跑
# 重新进入：tmux attach -t gpt_sovits
```

## 4. 客户端实现

### 4.1 SSH 配置解析器（`core/ssh_config_parser.py`）
- 解析 `~/.ssh/config`，返回 `list[SSHHost]`
- `SSHHost` 字段：`host`, `hostname`, `user`, `port`
- 处理重复 Host（用户 config 中 115.156.97.117 出现 3 次）

### 4.2 SSH 隧道管理器（`core/ssh_tunnel.py`）
```python
class SSHTunnel:
    def __init__(self, host: str, local_port: int = 9880, remote_port: int = 9880): ...
    def start(self) -> bool:
        # ssh -L 9880:localhost:9880 <host> -N -f -o ConnectTimeout=5
        # 用 subprocess.Popen，保存句柄
    def test(self) -> bool:
        # ssh <host> -o ConnectTimeout=5 echo ok
    def stop(self):
        # terminate Popen 句柄
    def is_alive(self) -> bool:
        # poll() is None 表示还活着
```

### 4.3 设置页面（`ui/settings_dialog.py` 新增 tab）
- Tab 名称：「GPT-SoVITS」
- 内容：
  - 模式选择：`QComboBox`（本地启动 / SSH 隧道 / 自动）
  - SSH Host 选择：`QComboBox`（从 `~/.ssh/config` 读取）
  - 测试连接按钮：`QPushButton`（显示「✓ 连接成功」或「✗ 失败原因」）
  - 端口配置：`QSpinBox`（默认 9880）
  - 状态显示：当前隧道状态、GPT-SoVITS API 可用性

### 4.4 desktop_pet.py 启动逻辑
```python
def maybe_start_gpt_sovits(spawn=subprocess.Popen) -> bool:
    mode = load_config().get("gpt_sovits_mode", "auto")
    if mode == "ssh" or (mode == "auto" and not _has_local_gpu()):
        return start_ssh_tunnel()
    # 原有本地启动逻辑
    ...
```

## 5. UI 设计

- 复用现有 `settings_dialog.py` 风格（暂用 CRT_QSS，后续 UI 统一整改时一并调整）
- 新增 tab 在「语音输入」之后
- 字段布局参考现有「Chat模型」tab 的 form 布局

## 6. 测试方案

### 6.1 单元测试
- `ssh_config_parser`：解析用户实际的 `~/.ssh/config`，验证去重逻辑
- `ssh_tunnel.test()`：对 202.114.107.184 实测

### 6.2 集成测试
- 配置 SSH 隧道模式 → 发消息 → 验证语音输出
- 隧道中断 → 验证自动重连或回退 SAPI
- 模式切换：本地 ↔ SSH ↔ 自动

### 6.3 边界场景
- 服务器 GPT-SoVITS 未启动 → 友好错误提示
- SSH 密钥未配置 → 提示用户配 `ssh-keygen` + `ssh-copy-id`
- 网络中断 → 隧道断开检测 + 重连

## 7. 风险与限制

| 风险 | 缓解措施 |
|---|---|
| SSH 隧道延迟 | 实测 RTT <300ms，对 TTS 首句延迟影响 <10% |
| 服务器 GPU 占满 | 错误返回时提示用户检查服务器状态 |
| 教育网断网 | 自动模式回退本地 SAPI |
| tmux 会话丢失 | 文档提供重启命令，不在客户端自动管理 |

## 8. 实施步骤

1. 写 `core/ssh_config_parser.py` + 测试
2. 写 `core/ssh_tunnel.py` + 对 202.114.107.184 实测
3. `ui/settings_dialog.py` 新增 GPT-SoVITS tab
4. `desktop_pet.py` 集成模式选择 + 启动分支
5. 端到端测试（用户在服务器部署后）

## 9. 用户验证清单

- [ ] 服务器端 GPT-SoVITS 部署成功（`curl http://localhost:9880/docs` 返回文档）
- [ ] 设置页面能读取 `~/.ssh/config` 并列出 Host
- [ ] 测试连接按钮显示成功
- [ ] 发消息后听到红莉栖语音（非 SAPI 机械音）
- [ ] 关闭桌宠后 SSH 隧道进程被清理
