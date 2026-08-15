# Amadeus 打包说明

## 环境要求
- Windows 10/11
- Python 3.11+
- 依赖：`python -m pip install -r requirements.txt`
- 打包工具：`python -m pip install pyinstaller`

## 打包命令
在项目根目录执行：

```
python -m PyInstaller Amadeus.spec --noconfirm
```

产物：`dist/Amadeus-<version>/Amadeus-<version>.exe`（onefile，无控制台窗口）。

## 版本号
版本号定义在 `core/version.py` 的 `__version__`，打包产物名自动带版本号。
发布新版只需改 `__version__` 后重新打包。

## 验证打包产物
运行 exe：
- 桌宠窗口直接出现（无登录窗）。
- 设置页"关于"显示版本号与 `__version__` 一致。
- GPT-SoVITS TTS 依赖外部 API 服务（`http://127.0.0.1:9880`），不打入 exe；
  未启动时自动降级 SAPI。
