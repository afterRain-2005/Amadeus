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

a = Analysis(
    ['main.py'],
    pathex=[str(Path('.').resolve()), str(_libs)],
    binaries=[],
    datas=[
        ('resources', 'resources'),          # Live2D 模型/图标/纹理/语音样本
        ('live2d', 'live2d'),                # live2d_page.html + pixi/cubism 运行时
    ],
    hiddenimports=[
        'pywebview.platforms.edgechromium',
        'ddgs', 'trafilatura', 'mss',
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
