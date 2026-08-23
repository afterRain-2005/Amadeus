# Paper2Galgame Skill

把 PDF / DOCX / PPT / PPTX / 纯文本学习资料，一键转化为互动视觉小说（galgame）故事的 WorkBuddy 技能。基于官方 [Paper2Galgame](https://paper2gal.com) CLI 构建。

## 功能

- **文档转游戏**：把枯燥的学习资料改编成可玩的互动视觉小说
- **角色管理**：创建/更新角色（头像、表情立绘、最多 3 张 CG、参考配音），支持本地素材上传
- **社区角色**：搜索并借用社区角色
- **背景管理**：上传/管理场景背景
- **存档系统**：查看、导入已保存的游戏进度
- **安全认证**：浏览器 OAuth + PKCE 授权，令牌仅存本地 `~/.paper2gal/config.json`

跨平台 Node 启动器：优先使用 `P2G_CLI_PATH`，其次本地开发检出、全局 `paper2gal` 命令，最后通过 `npx` 运行最新发布的 `@paper2gal/cli` 包。Windows 上无需 Bash/WSL。

## 安装（WorkBuddy）

### 方式一：本地安装

把本仓库的 `paper2galgame` 目录（或解压 zip 包）放到以下任一位置：

- **个人级（跨项目）**：`~/.workbuddy/skills/paper2galgame/`
- **项目级（团队共享）**：`{项目目录}/.workbuddy/skills/paper2galgame/`

重启 WorkBuddy 即生效，对话中提到 Paper2Gal / 转视觉小说 等关键词会自动触发。

### 方式二：通过 GitHub 导入

WorkBuddy 技能管理 → 通过 URL 导入 → 填入本仓库地址。

## 使用示例

```
把这份 PDF 讲义改编成一部可玩的互动视觉小说
用 Paper2Gal 把这份 PPT 变成 galgame 故事
为这段课程内容设计角色和剧情分支
```

## 要求

- Node.js 18 或更高
- `npx`（或全局安装 `paper2gal`，或设置 `P2G_CLI_PATH` 指向 CLI JavaScript 文件）
- Paper2Gal CLI 0.2.1+（本地角色图片上传需要）
- Paper2Gal 账号（认证类操作需要）

## 项目结构

```
paper2galgame/
├── SKILL.md            # 技能定义与使用说明
├── scripts/
│   ├── paper2gal.cjs   # 跨平台 Node 启动器
│   └── paper2gal.sh    # Bash 入口（macOS/Linux）
├── assets/
│   └── icon.png        # 技能图标
└── references/         # 参考文档
```

## License

MIT
