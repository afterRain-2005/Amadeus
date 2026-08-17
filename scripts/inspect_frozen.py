"""检查 frozen exe 内是否打包了 miniaudio / sounddevice。"""
import sys
from PyInstaller.utils.cliutils.archive_viewer import get_archive

exe = sys.argv[1] if len(sys.argv) > 1 else "dist/Amadeus-0.3.1.exe"
try:
    a = get_archive(exe)
    tocs = a.toc
    miniaudio = [(n, d) for n, d in tocs if "miniaudio" in n.lower()]
    print("miniaudio in archive:")
    for n, d in miniaudio:
        print(f"  {n}  {d}")
    print("---")
    sd = [(n, d) for n, d in tocs if "sounddevice" in n.lower()]
    print("sounddevice (first 5):")
    for n, d in sd[:5]:
        print(f"  {n}  {d}")
    print("---")
    print(f"total entries: {len(tocs)}")
except Exception as e:
    print(f"ERR: {e}")
