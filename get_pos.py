"""
取坐标小工具
运行后实时显示鼠标坐标，按 Ctrl+C 退出
"""
import time

import win32api

print("=" * 50)
print("  取坐标小工具")
print("  将鼠标移动到目标位置，坐标会实时更新")
print("  按 Ctrl+C 退出")
print("=" * 50)

try:
    while True:
        x, y = win32api.GetCursorPos()
        print(f"\r  当前坐标: x={x}, y={y}    ", end="", flush=True)
        time.sleep(0.05)
except KeyboardInterrupt:
    print("\n\n已退出。")