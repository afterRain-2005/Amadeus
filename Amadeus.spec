# -*- mode: python ; coding: utf-8 -*-
# Amadeus PyInstaller spec —— 版本号取自 core/version.py，产物名 Amadeus-<version>.exe
# 打包说明见 docs/build.md
import sys
from pathlib import Path

sys.path.insert(0, str(Path('.').resolve()))
from core.version import __version__  # noqa: E402

# .libs 是 pip install --target 的本地依赖（ddgs/trafilatura/mss），加入搜索路径以便 PyInstaller 收集
_libs = Path('.').resolve() / '.libs'
if _libs.is_dir():
    sys.path.insert(0, str(_libs))

# deepseek-harness Python SDK 路径 + 运行时路径
_harness_sdk = Path('.').resolve() / 'deepseek-harness-master' / 'python' / 'sdk' / 'src'
_harness_runtime = Path('.').resolve() / 'deepseek-harness-master' / 'python' / 'sdk-runtime' / 'src'

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve()), str(_libs), str(_harness_sdk), str(_harness_runtime)],
    binaries=[],
    datas=[
        ('resources', 'resources'),          # Live2D 模型/图标/纹理/语音样本
        ('live2d', 'live2d'),                # live2d_page.html + pixi/cubism 运行时
        # DeepSeek Harness node 闭包 + 默认 cordis.yml + 元数据（frozen 下由 core/harness_bridge.py 的 _runtime_data_dir 定位）
        (str(_harness_runtime / 'deepseek_harness_runtime'), 'deepseek_harness_runtime'),
    ],
    hiddenimports=[
        'pywebview.platforms.edgechromium',
        'ddgs', 'trafilatura', 'mss',
        'miniaudio',  # 阿里云 TTS 流式 MP3 解码（core/mp3_decoder.py 函数内动态 import，PyInstaller 静态分析漏抓）
        'markdown', 'markdown.extensions.fenced_code', 'markdown.extensions.tables', 'markdown.extensions.nl2br',  # 终端 markdown 渲染（extensions 动态 import）
        'deepseek_harness', 'deepseek_harness.client', 'deepseek_harness.api', 'deepseek_harness.models', 'deepseek_harness.errors',  # DeepSeek Harness SDK
        'deepseek_harness_runtime',  # DeepSeek Harness 运行时（node 模式）
        'core.cordis_builder',  # 设置页保存时动态生成 cordis（ui/settings_dialog.py 函数内 import，PyInstaller 静态分析可能漏抓）
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # anaconda base 混装 PyQt5/PyQt6，PyInstaller 不允许多 Qt 绑定，显式排除；
    # .libs 里 numpy 的函数级 matplotlib 懒导入会被静态分析追踪，连带 anaconda
    # 科学栈（scipy/pandas/botocore 等），运行时根本不会走到，全部排除瘦身。
    excludes=[
        'PyQt5', 'PyQt6', 'qtpy',
        'matplotlib', 'scipy', 'pandas', 'botocore', 'boto3', 'IPython',
        'pytest', 'tkinter',
        'webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f'Amadeus-{__version__}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
