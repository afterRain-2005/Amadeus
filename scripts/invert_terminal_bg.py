"""把 resources/terminal_back.png 的白色/黑色互换（颜色反转），覆盖原图。

用法：python scripts/invert_terminal_bg.py
"""

import sys
from pathlib import Path

from PySide6.QtGui import QImage

TARGET = Path(__file__).resolve().parent.parent / "resources" / "terminal_back.png"


def invert_image(path: Path) -> None:
    """按像素反转 RGB（保留 alpha），写回原路径。"""
    img = QImage(str(path))
    if img.isNull():
        raise SystemExit(f"无法加载图片: {path}")
    size = img.size()
    for y in range(size.height()):
        for x in range(size.width()):
            c = img.pixelColor(x, y)
            c.setRed(255 - c.red())
            c.setGreen(255 - c.green())
            c.setBlue(255 - c.blue())
            img.setPixelColor(x, y, c)
    if not img.save(str(path)):
        raise SystemExit(f"cannot write: {path}")
    print(f"OK: inverted and overwritten -> {path}")


if __name__ == "__main__":
    invert_image(TARGET)