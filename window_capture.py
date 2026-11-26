# -*- coding: utf-8 -*-
# @Author: BugNotFound
# @Date: 2025-10-02 14:45:20
# @LastEditTime: 2025-10-07 14:59:39
# @FilePath: /DeltaForceScript/window_capture.py
# @Description: 窗口截图工具 - 包含Windows Graphics Capture API支持

import logging
import time
from typing import Optional, Tuple

import dxcam
import win32gui
import cv2
import numpy as np

logger = logging.getLogger(__name__)


def enum_windows_with_title():
    """枚举所有窗口并显示标题"""
    def enum_callback(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            window_title = win32gui.GetWindowText(hwnd)
            if window_title:
                results.append((hwnd, window_title))
        return True

    windows = []
    win32gui.EnumWindows(enum_callback, windows)
    return windows


class WindowCapture():
    def __init__(self, device_idx: int = 0, output_idx: int = 0, max_buffer_len: int = 8):
        """初始化窗口捕获

        Args:
            device_idx: 设备索引
            output_idx: 输出屏幕索引（多屏幕时指定）
        """
        logger.debug("DXGI 设备: %s", dxcam.device_info())
        logger.debug("DXGI 输出: %s", dxcam.output_info())
        self.device_idx = device_idx
        self.output_idx = output_idx
        self.max_buffer_len = max_buffer_len
        self._last_frame: Optional[np.ndarray] = None
        self._last_frame_ts: float = 0.0
        self.camera = None
        self._create_camera()

    def _create_camera(self):
        if self.camera is not None:
            try:
                if self.camera.is_capturing:
                    self.camera.stop()
            except Exception:
                pass
        self.camera = dxcam.create(
            device_idx=self.device_idx,
            output_idx=self.output_idx,
            output_color="BGR",
            max_buffer_len=self.max_buffer_len,
        )
        if self.camera is None:
            raise RuntimeError(
                f"窗口捕获初始化失败（device_idx={self.device_idx}, output_idx={self.output_idx}），请检查屏幕索引是否正确"
            )

    def capture_fresh(self) -> Tuple[Optional[np.ndarray], bool]:
        """尝试抓取一帧全新画面（不回退缓存）

        DXGI Desktop Duplication 在桌面像素未发生变化时返回 None（代表当前画面与上一帧
        100% 一致，并非错误）。抢购热路径利用此特性可在 <0.1ms 内跳过无变化帧。

        Returns:
            (frame, is_new): 若有新帧返回 (frame, True)；无新帧返回 (self._last_frame, False)
        """
        try:
            frame = self.camera.grab()
        except Exception as e:
            logger.warning("DXGI 抓帧异常，尝试重建捕获实例: %s", e)
            try:
                self._create_camera()
            except Exception:
                pass
            return None, False

        if frame is not None and frame.size > 0:
            self._last_frame = frame
            self._last_frame_ts = time.perf_counter()
            return frame, True
        return self._last_frame, False

    def capture(self, max_cache_age: float = 1.2) -> Optional[np.ndarray]:
        """抓取画面；若当前瞬间无新帧（桌面静止），复用近期缓存帧

        解决同一毫秒内连续调用 capture()（如主循环截屏后紧接着 OCR 局部区域未传 frame）
        因 DXGI 无新帧返回 None 导致的识别失败问题。
        """
        frame, is_new = self.capture_fresh()
        if is_new:
            return frame
        if frame is not None and (time.perf_counter() - self._last_frame_ts) <= max_cache_age:
            return frame
        return None

    def grab_region(self, region: Tuple[int, int, int, int],
                    allow_cached: bool = False,
                    max_cache_age: float = 0.5) -> Tuple[Optional[np.ndarray], bool]:
        """极速抓取指定矩形区域 (left, top, right, bottom)

        dxcam.grab(region=...) 只在 CPU 侧转换目标子区域像素，比整屏 2560x1440 拷贝快数十倍，
        适合抢购点击连发期间以百赫兹频率监测确认弹窗或倒计时变化。

        Returns:
            (roi, is_new): 有新帧时返回 (roi, True)；无新帧时若 allow_cached 则从缓存裁剪返回 (roi, False)
        """
        left, top, right, bottom = (int(v) for v in region)
        try:
            roi = self.camera.grab(region=(left, top, right, bottom))
        except Exception:
            roi = None
        if roi is not None and roi.size > 0:
            if self._last_frame is not None:
                # 同步更新缓存帧对应区域，避免后续复用整帧缓存时该块陈旧
                h, w = self._last_frame.shape[:2]
                cl, ct = max(0, left), max(0, top)
                cr, cb = min(w, right), min(h, bottom)
                if cr - cl == roi.shape[1] and cb - ct == roi.shape[0]:
                    self._last_frame[ct:cb, cl:cr] = roi
                    self._last_frame_ts = time.perf_counter()
            return roi, True
        if allow_cached and self._last_frame is not None:
            if (time.perf_counter() - self._last_frame_ts) <= max_cache_age:
                h, w = self._last_frame.shape[:2]
                cl, ct = max(0, left), max(0, top)
                cr, cb = min(w, right), min(h, bottom)
                if cr > cl and cb > ct:
                    return self._last_frame[ct:cb, cl:cr], False
        return None, False

    def last_frame_age(self) -> float:
        """距离最近一次成功抓到新帧的秒数；从未抓到过返回 inf"""
        if self._last_frame is None:
            return float("inf")
        return time.perf_counter() - self._last_frame_ts

    def reconnect(self):
        """主动重建 DXGI 捕获器（应对全屏切换/锁屏恢复后的 ACCESS_LOST）"""
        self._create_camera()

    def stop(self):
        if self.camera and self.camera.is_capturing:
            self.camera.stop()
    
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    wc = WindowCapture()
    from region_selector import RegionSelector
    selector = RegionSelector()
    selector.load_regions_from_file("regions_2k.json")
    frame = wc.capture()
    region = selector.get_region("verify")
    frame = frame[region[1]:region[3], region[0]:region[2]]
    # 打印中心色块颜色
    center_color = frame[frame.shape[0] // 2, frame.shape[1] // 2]
    print("Center color (BGR):", center_color)
    cv2.imwrite("screenshot.png", frame)
    wc.stop()
