# PRD:QQ / 微信消息接入与通知

版本:v0.1(草案)
日期:2026-08-22
状态:待评审

---

## 1. 背景与目标

Amadeus 目前只能被动响应用户的语音/文字输入,感知不到 QQ、微信里发生的事。
用户在挂机、专注工作时希望红莉栖能"替我看着手机/聊天软件",有重要消息时主动提醒。

**目标(P1)**:
- 接入 QQ 与微信的实时消息流
- 新消息到达时,通过桌宠主动通知(气泡 + 可选 TTS 播报)

**非目标(本期不做)**:
- 代替用户回复消息(只读,不写)
- 群消息全量播报(只做过滤后的重要消息)
- 移动端 / 多设备同步

## 2. 技术方案选型

QQ 和微信均无官方开放 API,只能走第三方协议端。选型原则:**协议端独立进程运行,Amadeus 只做客户端消费消息**,互不侵入。

### 2.1 QQ:NapCat + OneBot 11(推荐,低风险)

- **NapCat**(无头 QQNT,基于 NTQQ)对外暴露 **OneBot 11** 标准接口,支持正向/反向 WebSocket。
- Amadeus 作为 WS 客户端连接 NapCat,订阅 `message.private` / `message.group` 事件,生态成熟、文档全。
- 备选:Lagrange.Core(.NET)、LLOneOneBot。均走 OneBot 11,客户端代码不变。

### 2.2 微信:wcferry(WeChat Ferry,Windows 限定)

- **wcferry**(wcf.ritual.link)通过注入 DLL 配合**指定版本的 PC 微信**(3.9.x)工作,Python SDK 现成,提供消息回调与联系人查询。
- 限制:必须锁定特定微信客户端版本、微信需保持登录;微信更新后需等 wcferry 适配。
- 备选:wechatpadpro(个人协议,风险高,封号概率大,**不推荐**)。

> 风险提示:两者都属非官方方案,存在账号风控可能。PRD 默认只读不发送,降低风险;文档中需向用户明示。

### 2.3 统一抽象

两个来源统一收敛到 `IMMessage` 数据模型,上层(过滤、通知、记忆)不感知来源:

```python
@dataclass
class IMMessage:
    platform: str        # "qq" | "wechat"
    msg_type: str        # "private" | "group"
    peer_id: str         # 发送者/群号
    sender_name: str
    content: str         # 已剥离 CQ 码的纯文本
    is_at_me: bool
    timestamp: float
    raw: dict            # 原始事件,调试用
```

## 3. 功能需求

### 3.1 连接管理
- **F1 设置页**:新增"消息接入"tab:各平台开关、NapCat WS 地址(默认 `ws://127.0.0.1:3001`)、微信 wcferry 启动按钮、连接状态指示(未连接/已连接/错误)。
- **F2 自动重连**:断线指数退避重连;连接状态变化时托盘气泡提示。
- **F3 登录引导**:QQ 侧由用户自行启动 NapCat 并扫码;微信侧检测微信进程/版本,wcferry 注入失败给出明确错误(版本不符/未登录)。

### 3.2 消息接收与过滤
- **F4 实时接收**:私聊全收;群聊默认只收 @我 + 关键词,规则可在设置页配置(免打扰名单、关键词白名单)。
- **F5 会话摘要**:非重要群消息不通知,但按 5 分钟窗口聚合存入本地(供用户问"刚才群里聊了什么")。
- **F6 去重与回补**:启动/重连后通过 OneBot `get_msg`/消息序列号去重,不重播旧消息(可配置回看最近 N 条)。

### 3.3 通知(P1 核心)
- **F7 桌宠气泡**:桌宠头顶气泡显示"【QQ·私聊】张三:今晚吃饭吗?",多条聚合为"x 条新消息"。
- **F8 TTS 播报**:可开关;私聊默认播报,群@我播报摘要;遵循现有 Companion 的"免打扰时段"。
- **F9 托盘通知**:Qt 托盘 `showMessage()` 作为兜底通道(可开关)。
- **F10 重要性分级**(P2,LLM 辅助):调用现有 LLM 路由对消息做 高/中/低 分级,低级只记不报,高级强提醒(气泡+语音+托盘)。

### 3.4 与现有系统集成
- **F11 记忆写入**:重要消息经用户确认后写入现有 memory 系统(P2)。
- **F12 对话上下文**:用户可问"最近有什么消息",桌宠从消息缓冲区回答(P2)。

## 4. 架构与模块设计

```
core/im/
  ├── models.py          # IMMessage 等数据模型
  ├── onebot_client.py   # QQ:OneBot 11 WS 客户端(连接、事件解析、重连)
  ├── wcf_client.py      # 微信:wcferry 封装(注入、回调、联系人)
  ├── filter.py          # 过滤/聚合/去重
  └── notify.py          # 气泡 + TTS + 托盘 分发
desktop_pet.py           # 启动 IMManager,接线到气泡/TTS/托盘
ui/settings_dialog.py    # 新增"消息接入"tab
config.py / data/config.json  # im.qq.enabled / im.qq.ws_url / im.wechat.* / im.notify.*
```

- `IMManager` 在独立 QThread/asyncio loop 中运行,消息经 Qt signal 投递到主线程,避免阻塞 UI。
- 通知分发复用 `core/tts_client.py` 与托盘实例。

## 5. 数据与隐私
- 消息仅存本地(`data/im_buffer.jsonl`,滚动保留 7 天),不上传。
- API Key / WS 地址等敏感配置沿用 `data/config.json`(已 gitignore)。
- 设置页明示:"使用非官方协议,存在账号风控风险,建议使用小号验证"。

## 6. 里程碑
| 阶段 | 内容 | 预估 |
|---|---|---|
| M1 | QQ 接入:OneBot 客户端 + 气泡/托盘通知 + 设置页 | 3~4 天 |
| M2 | 微信接入:wcferry + 统一过滤/去重 | 2~3 天 |
| M3 | TTS 播报 + 免打扰 + 聚合缓冲(F5/F8) | 1~2 天 |
| M4(P2) | LLM 重要性分级、记忆写入、对话查询 | 另行排期 |

## 7. 验收标准
1. NapCat 运行且 QQ 登录后,Amadeus 10s 内建立 WS 连接并在私聊消息到达 2s 内弹气泡。
2. 断开 NapCat 后自动重连成功,且不重播断线期间之前的旧消息。
3. 微信(PC 3.9.x 已登录)注入成功,私聊消息通知正常;注入失败时设置页显示可读错误。
4. 关闭某平台开关后完全断开、无残留线程。
5. 免打扰时段内无 TTS 播报。

## 8. 待确认问题
1. 微信走 wcferry 需锁定微信版本,是否接受?(否则只能砍掉微信,仅做 QQ)
2. 群消息过滤的默认策略:@我 only,还是白名单群?
3. TTS 播报私聊内容是否涉及隐私(外放场景),默认开还是关?
