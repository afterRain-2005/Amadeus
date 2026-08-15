# conftest.py — 测试全局配置：把项目本地依赖目录 .libs 加入 sys.path
# （pip install --target=.libs 的包在测试进程中也可导入）
import sys
from pathlib import Path

_libs = Path(__file__).parent / ".libs"
if _libs.is_dir():
    sys.path.insert(0, str(_libs))
