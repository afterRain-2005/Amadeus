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
    excludes=[],
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
