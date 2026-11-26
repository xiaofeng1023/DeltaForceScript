# -*- coding: utf-8 -*-
# @Author: BugNotFound
# @Date: 2025-10-04
# @FilePath: /DeltaForceScript/main_gui.py
# @Description: 带 PyQt6 GUI 的主程序

import os
import sys
import re
import json
import time
import random

import numpy as np
import ctypes
from ctypes import wintypes
import logging
# 设置 PaddleOCR 模型下载国内镜像（加速下载）
os.environ['PADDLEX_MODEL_URL_PREFIX'] = 'https://paddlepaddle.bj.bcebos.com'

from datetime import datetime

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(
            os.path.join(_log_dir, f"{datetime.now().strftime('%Y-%m-%d')}.log"),
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

from window_capture import WindowCapture
from region_selector import RegionSelector
from gui_monitor import MonitorWindow
from page_flow import (
    ACTION_WAIT, FOLLOW_COUNT_PATTERN,
    MarketFlow, detect_page, get_region, press_esc, read_follow_count,
    PAGE_BROWSE, PAGE_FILTER_DIALOG, PAGE_SALE, PAGE_GRADE_PANEL,
    PAGE_FOLLOW, PAGE_LOBBY, PAGE_MARKET, PAGE_UNKNOWN,
    PHASE_PREPARE, PHASE_SNIPE,
)

import cv2
from paddleocr import PaddleOCR
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
import pydirectinput

# pydirectinput 每次鼠标调用都会强制 sleep(PAUSE)（默认 0.1 秒），且 click() 内部还会
# 先调一次 moveTo()，等于每次点击白等 0.2 秒。抢购窗口只有 1 秒，一轮购买（5 次购买 +
# 8 次确认）光这里就要浪费 2.6 秒，因此直接关掉。出于反作弊考虑保留了原有的人类化
# 抖动（CLICK_JITTER_PX），点击节奏完全由配置里的"购买点击延迟 / 点击间隔 / 点击次数"
# 控制，不在这里额外加固定停顿
pydirectinput.PAUSE = 0

# ========== 全局常量 ==========
# 末尾几秒（t <= 5s）的极速帧轮询间隔（秒），配合 timeBeginPeriod(1) 实现 ~3-4ms 级响应
FAST_POLL_INTERVAL = 0.004
# 中段临近期（5s < t <= 12s）轮询间隔（秒），确保精准卡住 5s 刷新点而不滞后
MEDIUM_POLL_INTERVAL = 0.08
# 准备阶段无页面跳转时的快速步进间隔（秒）
PREPARE_FAST_INTERVAL = 0.04
# 点击位置随机偏移范围（像素），避免每次点击坐标完全一致
CLICK_JITTER_PX = 4
# 原生点击按下保持时长（秒）：约半帧~1帧，防止 Unreal Engine Slate UI 同帧吞掉 0ms 点击
CLICK_HOLD_SECONDS = 0.008
# 视觉闭环检测：确认弹窗区域像素变化占比超过该阈值判定为「弹窗已弹出」
VERIFY_POPUP_OPEN_RATIO = 0.14
# 视觉闭环检测：确认弹窗区域相对初始背景差异回落到该阈值以内判定为「弹窗已关闭」
VERIFY_POPUP_CLOSED_RATIO = 0.08
# 防卡死：倒计时持续多少秒没有任何变化（含 OCR 完全读不到倒计时）即判定为界面卡死
# 正常倒计时每秒都在变，因此不会误触发；只有页面真的卡住（如被弹窗遮挡）才会恢复
SELF_HEAL_STALL_SECONDS = 5
# 停滞多少秒后输出一次告警日志，便于定位"识别不到倒计时"的问题
SELF_HEAL_WARN_SECONDS = 5
# "获得外观"结果页"跳过"按钮所在扫描区域（默认值取自 2K 屏的 2100,1140 - 2560,1320）
SKIP_BUTTON_RECT_RATIO = (2100 / 2560, 1140 / 1440, 2560 / 2560, 1320 / 1440)
# 脚本窗口的落点（相对主屏）
WINDOW_LEFT_RATIO = 2300 / 2560
WINDOW_TOP_RATIO = 375 / 1440
# 倒计时剩余多少秒时点击刷新（修改后需同步 gui_monitor.py 中复选框的文案）
REFRESH_TRIGGER_SECONDS = 5
# 连续多少轮"未检测到扣款"就提示一次
MAX_FAIL_COUNT = 10
# 购买+确认点完后，首次读取余额前的短暂稳定等待与二次复核等待（秒）
POST_BUY_INITIAL_WAIT = 0.18
POST_BUY_SETTLE_SECONDS = 0.38
# 退出「我的关注」页时，按 ESC 后等页面切换完成再重新识别的时间（秒）
FOLLOW_EXIT_WAIT_SECONDS = 1.2
# 一次退出关注页时最多补按几次 ESC（每次间隔 FOLLOW_EXIT_WAIT_SECONDS）
FOLLOW_EXIT_ATTEMPTS = 3
# 抢购阶段连续多少轮识别不出关注页内容，就判定为已不在关注页，尝试导航回去
SNIPE_UNKNOWN_LIMIT = 3
# 抢购阶段意外离开关注页后，最多连续尝试"导航回关注页"的轮数
SNIPE_RETURN_ROUNDS = 8
# 「关注页排序」最多尝试的轮数
FOLLOW_SORT_TRY_LIMIT = 3
# 准备阶段连续多少轮识别不出页面，就主动执行一次通用恢复（关弹窗 / 退回上一层）
PREPARE_UNKNOWN_LIMIT = 10
# 连续多少次自动恢复都没有让主循环重新出现进展，就停下并提示用户
RECOVER_ESCALATE_LIMIT = 5
# 超过多少秒完全抓不到任何桌面帧才告警（区分正常的静止画面无新帧与窗口最小化/锁屏）
CAPTURE_STALE_WARN_SECONDS = 4.0
# 「仅关注页」模式下，关注列表抢空后原地等用户手动加关注时的轮询间隔（秒）
FOLLOW_WAIT_POLL_SECONDS = 1.5
# 市场每日关闭时段（HH:MM）
MARKET_CLOSE_TIME = "05:00"
MARKET_OPEN_TIME = "08:00"
# 市场时段输入框接受的格式："05:00" / "5:00" / "0500"
TIME_PATTERN = re.compile(r"^(\d{1,2}):?(\d{2})$")
# 准备阶段（筛选 / 加关注）多少秒没有任何进展即判定为卡住
PREPARE_STALL_SECONDS = 25
# 准备阶段的轮询间隔（秒）
PREPARE_POLL_INTERVAL = 0.20
# 本地 OCR 模型根目录（相对脚本所在目录）
MODEL_ROOT = "models"
# 用户界面参数持久化文件名
USER_CONFIG_FILE = "user_config.json"
# 倒计时文本："N分M秒"
COUNTDOWN_PATTERN = re.compile(r'(\d+)\s*分\s*(\d+)\s*秒')

# ========== 高精度时钟与原生 Win32 输入层 ==========
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    ]


class _WindowsTimerResolution:
    """将 Windows 系统定时器精度从默认 15.625ms 提升至 1ms"""

    def __init__(self, period_ms: int = 1):
        self.period_ms = period_ms
        self._active = False

    def __enter__(self):
        try:
            ctypes.windll.winmm.timeBeginPeriod(self.period_ms)
            self._active = True
        except Exception:
            self._active = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._active:
            try:
                ctypes.windll.winmm.timeEndPeriod(self.period_ms)
            except Exception:
                pass
            self._active = False


def precise_sleep(seconds: float):
    """亚毫秒级高精度等待（配合 timeBeginPeriod(1) 消除 Windows 默认 15.6ms 睡眠抖动）"""
    if seconds <= 0:
        return
    deadline = time.perf_counter() + seconds
    if seconds > 0.003:
        time.sleep(seconds - 0.002)
    while time.perf_counter() < deadline:
        time.sleep(0)


def _send_mouse_event(flags: int, dx: int = 0, dy: int = 0) -> int:
    inp = _INPUT(
        type=INPUT_MOUSE,
        union=_INPUTUNION(mi=_MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0)),
    )
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def fast_move(x: int, y: int):
    """原生 Win32 SendInput 鼠标绝对定位（零多余查询开销）"""
    user32 = ctypes.windll.user32
    sw = max(2, user32.GetSystemMetrics(0))
    sh = max(2, user32.GetSystemMetrics(1))
    nx = int(round(x * 65535 / (sw - 1)))
    ny = int(round(y * 65535 / (sh - 1)))
    if _send_mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, nx, ny) == 0:
        pydirectinput.moveTo(int(x), int(y))


def fast_click(x: int, y: int, hold: float = CLICK_HOLD_SECONDS, move: bool = True):
    """原生 Win32 极速左键点击，带微秒/帧级按压保持以防 UE4/UE5 同帧漏检"""
    if move:
        fast_move(x, y)
    down_ok = _send_mouse_event(MOUSEEVENTF_LEFTDOWN)
    if hold > 0:
        precise_sleep(hold)
    up_ok = _send_mouse_event(MOUSEEVENTF_LEFTUP)
    if down_ok == 0 or up_ok == 0:
        pydirectinput.click(x=int(x), y=int(y), button=pydirectinput.LEFT)


# ========== 倒计时像素差分与亚秒级跳秒相位锁定 ==========
COUNTDOWN_SIG_SIZE = (128, 48)      # 差分用的小图尺寸：够分辨数字，又足够快
COUNTDOWN_PIXEL_DELTA = 18          # 单像素灰度差超过它才算"这个点变了"
COUNTDOWN_CHANGED_RATIO = 0.005     # 变化像素占比 ≥0.5% 才认为数字变了（抗区域大小差异）
COUNTDOWN_BIG_RATIO = 0.25          # 变化像素占比 ≥25% 视为整块画面更换，直接 OCR
COUNTDOWN_CHANGE_MIN_GAP = 0.35     # 倒计时不可能比这更快地跳一格
COUNTDOWN_CONFIRM_AT = 3            # 数值到 3 秒时 OCR 校准一次
COUNTDOWN_RESYNC_SECONDS = 3.0      # 这么久没 OCR 过就校准一次（只对剩余 10 秒内生效）
COUNTDOWN_RESYNC_FAST_SECONDS = 1.4
COUNTDOWN_UNSTABLE_LIMIT = 3        # 连续这么多次"跳太快"就退回纯 OCR


class CountdownWatcher:
    """用像素差分 + 跳秒相位锁定来跟踪倒计时

    update() 返回 (kind, total_seconds):
        ("value", n)     剩余 n 秒（OCR 权威值，或按跳秒本地递减得到；含归零瞬间 n=0）
        ("long", None)   倒计时按"天/小时"显示（市场还没开）
        ("unknown", None) 区域里没有可识别的倒计时（可能已不在关注页）
    """

    def __init__(self):
        self.auto = True
        self._sig = None
        self._value = None
        self._last_change_at = 0.0
        self._last_ocr_at = 0.0
        self._fast_hits = 0
        self.last_tick_perf = None      # 最近一次整数秒跳变发生的精确 perf_counter 时间戳
        self._refresh_sync_after = 0.0  # 点击刷新后的强制权威 OCR 同步时刻

    def reset(self):
        """页面可能已切换：丢掉签名与缓存值，下一轮重新 OCR"""
        self._sig = None
        self._value = None
        self._last_change_at = 0.0
        self._fast_hits = 0
        self.last_tick_perf = None
        self._refresh_sync_after = 0.0
        self.auto = True

    def mark_refreshed(self, settle_seconds: float = 0.22):
        """通知观察器刚刚点击了刷新按钮：等列表回包稳定后强制走一次权威 OCR

        解决刷新后榜首切换为另一件剩余秒数不同的商品时，像素差分误把新商品秒数
        当作旧商品 -1 秒递减的问题。
        """
        self._refresh_sync_after = time.perf_counter() + settle_seconds
        self._fast_hits = 0

    def value(self):
        """最近一次确认的剩余秒数（None 表示还不知道）"""
        return self._value

    def tick_age(self):
        """距离最近一次跳秒过去了多少秒；未知返回 None"""
        if self.last_tick_perf is None:
            return None
        return time.perf_counter() - self.last_tick_perf

    def resync_due(self) -> bool:
        """当前时刻是否需要即使无新帧也触发校准"""
        now_perf = time.perf_counter()
        if self._refresh_sync_after > 0 and now_perf >= self._refresh_sync_after:
            return True
        if self._value is not None and self._value <= 10:
            resync = (COUNTDOWN_RESYNC_FAST_SECONDS if self._value <= 3
                      else COUNTDOWN_RESYNC_SECONDS)
            if time.time() - self._last_ocr_at > resync:
                return True
        return False

    @staticmethod
    def signature(frame, region):
        """倒计时区域的灰度缩略图；区域截不到时返回 None"""
        if frame is None or frame.size == 0:
            return None
        left, top, right, bottom = region
        height, width = frame.shape[:2]
        left, right = max(0, int(left)), min(width, int(right))
        top, bottom = max(0, int(top)), min(height, int(bottom))
        if right <= left or bottom <= top:
            return None
        gray = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, COUNTDOWN_SIG_SIZE, interpolation=cv2.INTER_AREA)

    @staticmethod
    def _changed_ratio(sig, base):
        """明显变化的像素占比（按比例判断，避免区域大小影响阈值）"""
        delta = cv2.absdiff(sig, base)
        _, mask = cv2.threshold(delta, COUNTDOWN_PIXEL_DELTA, 255, cv2.THRESH_BINARY)
        return float(np.count_nonzero(mask)) / mask.size

    def update(self, frame, region, read):
        """根据当前画面返回倒计时状态；read 是一个"做一次 OCR 并返回文本"的回调"""
        sig = self.signature(frame, region)
        now_perf = time.perf_counter()

        # 刷新后等待回包期：稳定前暂维持旧值避免误判，到点后立即强制权威 OCR 同步新商品
        if self._refresh_sync_after > 0:
            if now_perf < self._refresh_sync_after:
                if self._value is not None:
                    return ("value", self._value)
            else:
                self._refresh_sync_after = 0.0
                return self._read(read, sig, record_tick=False)

        if not self.auto:
            return self._read(read, sig)
        if sig is None:
            return ("unknown", None)
        if self._sig is None or self._value is None:
            return self._read(read, sig)

        ratio = self._changed_ratio(sig, self._sig)
        now = time.time()
        if ratio < COUNTDOWN_CHANGED_RATIO:
            resync = (COUNTDOWN_RESYNC_FAST_SECONDS if self._value <= 3
                      else COUNTDOWN_RESYNC_SECONDS)
            if self._value <= 10 and now - self._last_ocr_at > resync:
                return self._read(read, sig)
            return ("value", self._value)
        if ratio >= COUNTDOWN_BIG_RATIO:
            # 整块画面换了（切页面/弹窗/倒计时归零消失）：
            # 若上一刻正是 1 秒且已持续 >0.25s，倒计时文字消失往往意味着刚刚归零解锁！
            prev_val = self._value
            prev_change = self._last_change_at
            self._last_change_at = now
            res = self._read(read, sig, record_tick=True)
            if res[0] == "unknown" and prev_val == 1 and (now - prev_change) >= 0.25:
                self.last_tick_perf = now_perf
                return ("value", 0)
            return res

        gap = now - self._last_change_at
        self._last_change_at = now
        if gap < COUNTDOWN_CHANGE_MIN_GAP:
            self._fast_hits += 1
            if self._fast_hits >= COUNTDOWN_UNSTABLE_LIMIT:
                self.auto = False
                logger.warning("[倒计时] 差分判定抖动过多，已退回逐轮 OCR（不影响功能）")
            return self._read(read, sig)
        self._fast_hits = 0

        nxt = self._value - 1
        self.last_tick_perf = now_perf
        if nxt < 0 or nxt == COUNTDOWN_CONFIRM_AT:
            return self._read(read, sig, record_tick=True)
        # 正常跳秒：本地递减（含 1->0 归零瞬间），零 OCR 延迟立即决策
        self._value = nxt
        self._sig = sig
        return ("value", nxt)

    def _read(self, read, sig, record_tick: bool = False):
        """做一次 OCR 并更新缓存；读不到就清空状态交由调用方兜底"""
        prev_val = self._value
        text = read() or ""
        self._last_ocr_at = time.time()
        if "天" in text or "小时" in text:
            self._value = None
            self._sig = sig
            return ("long", None)
        match = COUNTDOWN_PATTERN.search(text)
        if not match:
            self.reset()
            return ("unknown", None)
        total = int(match.group(1)) * 60 + int(match.group(2))
        if record_tick or (prev_val is not None and total == prev_val - 1):
            self.last_tick_perf = time.perf_counter()
        self._value = total
        self._sig = sig
        return ("value", total)


# 页面标识 -> 中文名，仅用于状态栏与日志展示
PAGE_NAMES = {
    PAGE_BROWSE: "典藏外观列表",
    PAGE_FILTER_DIALOG: "筛选弹窗",
    PAGE_SALE: "在售列表",
    PAGE_GRADE_PANEL: "成色筛选",
    PAGE_FOLLOW: "我的关注",
    PAGE_LOBBY: "游戏大厅",
    PAGE_MARKET: "交易行",
    PAGE_UNKNOWN: "未识别页面",
}


def is_admin():
    """检查是否以管理员权限运行"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin():
    """以管理员权限重新启动程序"""
    if not is_admin():
        logger.info("正在请求管理员权限...")
        # 获取当前脚本路径
        script = os.path.abspath(sys.argv[0])
        # 脚本路径与参数分别加引号，避免路径含空格时解析错误
        params = " ".join([f'"{script}"'] + [f'"{arg}"' for arg in sys.argv[1:]])
        # 指定工作目录，否则提权后 cwd 会变成 system32，导致相对路径配置失效
        work_dir = os.path.dirname(script)

        # 使用 ShellExecute 以管理员权限运行
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, work_dir, 1
        )

        if ret > 32:  # 成功
            sys.exit(0)
        logger.warning("未获得管理员权限，继续以普通权限运行")
        return False
    return True


def _screen_size():
    """获取主屏幕分辨率"""
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _rect_from_ratio(ratio: tuple):
    """按主屏分辨率把相对比例换算成实际像素区域"""
    screen_w, screen_h = _screen_size()
    left, top, right, bottom = ratio
    return (int(screen_w * left), int(screen_h * top),
            int(screen_w * right), int(screen_h * bottom))


def _to_int(text: str):
    """把 OCR 识别到的数字字符串转成整数，无法识别时返回 None"""
    return int(text) if text and text.isdigit() else None


def _parse_hhmm(text):
    """把 "05:00" / "5:00" / "0500" 解析成当日分钟数，格式不对返回 None"""
    match = TIME_PATTERN.match(str(text).strip()) if text else None
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _market_closed(now, close_text, open_text):
    """判断当前时刻是否落在市场关闭时段 [关闭时间, 开市时间) 内

    时间填错（解析不出来）、或开市时间不晚于关闭时间时返回 False（即不启用），
    避免一次手滑把时间填反后脚本永远启动不了。
    """
    close_minute = _parse_hhmm(close_text)
    open_minute = _parse_hhmm(open_text)
    if close_minute is None or open_minute is None or close_minute >= open_minute:
        return False
    now_minute = now.hour * 60 + now.minute
    return close_minute <= now_minute < open_minute


def click_region_center(region: tuple, clicks=1, interval=0.05, hold=CLICK_HOLD_SECONDS):
    """点击区域的中心位置（原生 Win32 极速实现）"""
    left, top, right, bottom = region
    center_x = (left + right) // 2 + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
    center_y = (top + bottom) // 2 + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
    for i in range(clicks):
        fast_click(center_x, center_y, hold=hold, move=(i == 0))
        if i < clicks - 1 and interval > 0:
            precise_sleep(interval)


def click_point(point: tuple, hold=CLICK_HOLD_SECONDS):
    """在指定像素点附近随机抖动后点击"""
    x, y = point
    fast_click(
        x + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX),
        y + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX),
        hold=hold,
    )


def extract_and_merge_digits(s: str) -> str:
    """识别字符串中的所有数字并合并为一个新字符串"""
    return ''.join(re.findall(r'\d', s))


def _find_local_model_dir(base_dir: str, keyword: str):
    """在 models/ 下查找本地 OCR 推理模型目录

    同时兼容 modelscope 缓存目录结构（如 models/PaddlePaddle--PP-OCRv5_server_det/snapshots/master）
    和直接把推理文件放在 models/PP-OCRv5_server_det_infer 下的结构。

    Args:
        base_dir: 项目根目录
        keyword: 模型类型关键字，"det"（检测）或 "rec"（识别）

    Returns:
        模型目录的绝对路径；未找到时返回 None，由 PaddleOCR 自行下载
    """
    model_root = os.path.join(base_dir, MODEL_ROOT)
    if not os.path.isdir(model_root):
        return None
    for root, _, files in os.walk(model_root):
        if "inference.json" in files and "inference.pdiparams" in files:
            if keyword in os.path.normpath(root).lower():
                return root
    return None


def create_ocr(base_dir: str):
    """创建 OCR 实例

    优先加载 models/ 下的本地模型（启动更快且可离线运行），未找到时由 PaddleOCR 自动下载；
    设备优先使用 GPU，初始化失败时回退到 CPU。
    """
    det_dir = _find_local_model_dir(base_dir, "det")
    rec_dir = _find_local_model_dir(base_dir, "rec")

    kwargs = dict(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )
    # 检测与识别模型必须成对指定，否则 PaddleOCR 会去解析默认模型名
    if det_dir and rec_dir:
        kwargs["text_detection_model_dir"] = det_dir
        kwargs["text_recognition_model_dir"] = rec_dir
        logger.info(f"使用本地 OCR 模型: det={det_dir}, rec={rec_dir}")
    else:
        logger.info("未找到完整的本地 OCR 模型，将使用 PaddleOCR 自动下载的模型")

    try:
        ocr = PaddleOCR(device='gpu:0', **kwargs)
        logger.info("OCR 初始化完成（GPU）")
    except Exception as e:
        logger.warning(f"GPU 初始化失败，回退到 CPU: {e}")
        ocr = PaddleOCR(device='cpu', **kwargs)
    # PaddleOCR 初始化时会把根 logger 的级别调高，恢复为 INFO 保证运行过程可追溯
    logging.getLogger().setLevel(logging.INFO)
    for _handler in logging.getLogger().handlers:
        _handler.setLevel(logging.INFO)

    # 启动时预热一次推理图（编译 CUDA Kernel / CPU 算子缓存），避免首次倒计时识别出现百毫秒冷启动卡顿
    try:
        warmup = np.zeros((48, 160, 3), dtype=np.uint8)
        cv2.putText(warmup, "0:05", (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        ocr.predict(warmup)
        logger.info("OCR 推理引擎预热完成")
    except Exception as e:
        logger.debug(f"OCR 预热跳过: {e}")

    return ocr


class ScriptThread(QThread):
    """脚本运行线程"""
    
    status_updated = pyqtSignal(str)
    timer_updated = pyqtSignal(str, str)
    task_completed = pyqtSignal()
    
    def __init__(self, selector: RegionSelector, win_cap: WindowCapture, ocr, config_provider):
        """初始化脚本线程

        Args:
            selector: 区域选择器
            win_cap: 窗口捕获器
            ocr: PaddleOCR 实例
            config_provider: 返回当前配置字典的可调用对象（每次循环调用，使 GUI 改动实时生效）
        """
        super().__init__()
        self.selector = selector
        self.win_cap = win_cap
        self.ocr = ocr
        self._get_config = config_provider
        self._price_region_warned = False    # "未配置价格区域"的告警是否已输出过
        self._price_fallback_warned = False  # 详情面板价格读不到、改用卡片价格兜底的提示是否已输出过
        self.is_running = True
        self.is_paused = False

        # ===== 页面流程状态 =====
        self.flow = MarketFlow(self, selector, self.status_updated.emit)
        self.phase = PHASE_PREPARE    # 当前阶段：准备（筛选+加关注）/ 抢购
        self.last_page = None         # 上一轮识别到的页面，用于页面切换提示
        self.bought_count = 0         # 已抢到的商品数量
        # 仅关注页模式：是否已经提醒过"关注列表抢完，请手动添加关注"。
        # 没有这个标志的话，列表空着期间每轮都会走一遍提醒分支，日志会被刷屏
        self.follow_wait_notified = False

        # ===== 进度 / 防卡死 =====
        self.last_progress_time = time.time()
        self.stall_warned = False

        # ===== 抢购上下文（进入关注页时由 reset_snipe_context 重置）=====
        self.refreshed = False           # 本轮是否已点击过刷新
        self.buy_done = False            # 本轮是否已执行过购买
        self.last_total = None           # 上一次识别到的剩余总秒数
        self.consecutive_fail_count = 0  # 连续未检测到扣款的次数
        self.last_money_check = ""       # 上一次的三角币数量
        self.unknown_rounds = 0          # 抢购阶段连续识别不出页面的轮数
        self.return_follow_rounds = 0    # 抢购阶段连续尝试导航回关注页的轮数
        self.follow_sort_tries = 0       # 本次进入关注页已尝试设置排序的轮数
        self.snipe_checked_once = False  # 本次进入关注页是否已至少跑过一轮倒计时读取
        self.snipe_live = False          # 上一轮是否读到过倒计时（用于跳过重复的页面识别）
        self.prechecked_price = None     # 提前预审的价格缓存: (是否允许购买, 价格数值)
        self.buy_hovered = False         # 本轮是否已提前将光标预悬停至购买按钮
        self.hover_buy_pt = None         # 预悬停的购买按钮坐标
        self.countdown = CountdownWatcher()  # 倒计时像素差分与相位锁定
        self.prepare_unknown_rounds = 0  # 准备阶段连续识别不出页面的轮数
        self.recover_count = 0           # 连续自动恢复的次数（真正有进展时清零）
        self._capture_stale_warned = False

    # ------------------------------------------------------------ ops 接口
    def capture_fresh(self):
        """抓取全新桌面帧（无新帧时返回 (cached_frame, False)，供热路径零拷贝跳帧）"""
        frame, is_new = self.win_cap.capture_fresh()
        if is_new:
            self._capture_stale_warned = False
            return frame, True
        age = self.win_cap.last_frame_age()
        if age > CAPTURE_STALE_WARN_SECONDS and not self._capture_stale_warned:
            self._capture_stale_warned = True
            logger.warning("[截屏] 已超过 %.1f 秒未捕获到新画面，尝试重连捕获器", age)
            self.win_cap.reconnect()
            frame, is_new = self.win_cap.capture_fresh()
            if is_new:
                self._capture_stale_warned = False
                return frame, True
            self.status_updated.emit(
                "⚠️ 长时间未捕获到游戏画面，请确认游戏窗口未最小化、屏幕未被锁定")
        return frame, False

    def capture(self, max_cache_age: float = 1.2):
        """截取整屏画面（桌面静止无新帧时自动复用近期缓存帧，避免瞬时返回 None）"""
        frame = self.win_cap.capture(max_cache_age=max_cache_age)
        if frame is not None and frame.size > 0:
            self._capture_stale_warned = False
            return frame
        age = self.win_cap.last_frame_age()
        if age > CAPTURE_STALE_WARN_SECONDS and not self._capture_stale_warned:
            self._capture_stale_warned = True
            logger.warning("[截屏] 连续 %.1f 秒截屏为空，尝试重连 DXGI", age)
            self.win_cap.reconnect()
            frame = self.win_cap.capture(max_cache_age=max_cache_age)
            if frame is not None and frame.size > 0:
                self._capture_stale_warned = False
                return frame
            self.status_updated.emit(
                "⚠️ 截屏持续为空，请确认游戏窗口未最小化、屏幕未被锁定")
        return None

    def click_region(self, region):
        """点击区域中心"""
        click_region_center(region)

    def click_point_at(self, point):
        """点击指定像素点"""
        click_point(point)

    def mark_progress(self):
        """标记"有进展"：重置防卡死计时，并清空"连续恢复无效"计数"""
        self._reset_stall_timer()
        self.recover_count = 0

    def _reset_stall_timer(self):
        """只重置防卡死计时，不清空恢复计数（供恢复动作自身调用）"""
        self.last_progress_time = time.time()
        self.stall_warned = False

    def _click_keyword(self, region_name, keyword, frame):
        """在指定区域内 OCR 查找关键字并点击其文字中心，找到返回 True"""
        region = self.selector.get_region(region_name)
        if not region:
            return False
        for text, x1, y1, x2, y2 in self.ocr_region_items(region, frame):
            if keyword in text:
                click_point(((x1 + x2) // 2, (y1 + y2) // 2))
                return True
        return False

    def reset_snipe_context(self):
        """重置一轮抢购的上下文（每次进入关注页时调用）"""
        self.refreshed = False
        self.buy_done = False
        self.last_total = None
        self.consecutive_fail_count = 0
        self.unknown_rounds = 0
        self.return_follow_rounds = 0
        self.follow_sort_tries = 0
        self.snipe_checked_once = False
        self.snipe_live = False
        self.prechecked_price = None
        self.buy_hovered = False
        self.hover_buy_pt = None
        self.countdown.reset()
        self.follow_wait_notified = False
        self.stall_warned = False
        self.last_progress_time = time.time()
        money = extract_and_merge_digits(self.ocr_region(self.selector.get_region("money")))
        if money:
            self.last_money_check = money
        self.status_updated.emit(f"💰 当前三角币: {self.last_money_check or '未知'}")

    def _still_on_follow_page(self):
        """重新截屏确认当前是否仍停在「我的关注」页"""
        frame = self.capture()
        if frame is None or frame.size == 0:
            return False
        return detect_page(self, self.selector, frame) == PAGE_FOLLOW

    def _leave_follow_page(self):
        """退出「我的关注」页，回到能重新筛选商品的页面

        关注列表被抢空后必须真的离开这一页：留在空列表页上继续抢购没有意义。
        关键是不能按完 ESC 立刻重新识别 —— 游戏切页要 1~2 秒，切换中的画面
        仍会被判成关注页，于是脚本"退出 → 又进抢购 → 又退出"地空转（每轮都
        mark_progress，防卡死看门狗也永远不会触发）。这里按 ESC 后等页面稳定
        再确认，没退出去就补按，最多 FOLLOW_EXIT_ATTEMPTS 次。
        """
        for attempt in range(FOLLOW_EXIT_ATTEMPTS):
            press_esc()
            time.sleep(FOLLOW_EXIT_WAIT_SECONDS)
            if not self._still_on_follow_page():
                return
            logger.warning(f"[退出关注页] 第 {attempt + 1} 次 ESC 后仍在关注页")
        self.status_updated.emit(
            "⚠️ 多次按 ESC 仍停在「我的关注」页，请手动切回「典藏外观」列表页")

    def _leave_snipe(self):
        """退出抢购阶段，回到准备阶段重新扫描商品列表"""
        self.phase = PHASE_PREPARE
        self.last_page = None
        self.prepare_unknown_rounds = 0
        self.return_follow_rounds = 0
        # 只重置列表上下文，赛季/品阶筛选结果保留（游戏端仍然生效）
        self.flow.reset_prepare_context()
        self.countdown.reset()
        self.mark_progress()

    def _nav_back_to_follow(self, page, frame):
        """抢购阶段意外离开关注页：先设法回到关注页继续抢，回不去才退回准备阶段

        抢购成功后弹出的「获得外观」结果页、以及紧跟的 ESC，很容易把画面带到
        列表页或在售页。以前这里直接退回准备阶段重新筛选，等于把已经关注的商品
        全部作废、白跑一遍准备流程；现在按当前页面选一个"回关注页"的动作：
            典藏外观列表页 -> 点右侧「我的关注」入口，一步到位
            游戏大厅       -> 点底部「交易行」入口（返航链第一步）
            交易行首页     -> 点「典藏外观」标签（返航链第二步）
            在售页/成色侧栏/筛选弹窗/未识别 -> 按返回键退一层，下一轮再判断
        连续 SNIPE_RETURN_ROUNDS 轮都没能回到关注页时才认输。

        注意这里不碰看门狗（不调 _recover_ui）：导航失败本身是正常流程的一部分，
        不能算进"连续恢复无效即停机"的计数里。
        """
        # 回到关注页等于重新进了这一页，排序可能会被游戏重置为「默认排序」，
        # 所以下一轮允许再检查一次（见 run() 里的 follow_sort_tries）
        self.follow_sort_tries = 0
        self.snipe_checked_once = False
        if page == PAGE_BROWSE:
            self.status_updated.emit("↩️ 抢购阶段离开关注页，点「我的关注」入口返回")
            self.flow.enter_follow_page(frame)
        elif page == PAGE_LOBBY:
            self.status_updated.emit("🏛 抢购阶段退回了游戏大厅，点「交易行」入口返回")
            self.flow.enter_market()
        elif page == PAGE_MARKET:
            self.status_updated.emit("🖼 抢购阶段退到了交易行，点「典藏外观」返回列表页")
            self.flow.enter_appearance()
        else:
            # 在售页 / 成色侧栏 / 筛选弹窗 / 未识别页面：离列表页只有一步。
            # 与 _recover_ui 一致，优先点标定好的返回按钮，没标定就按 ESC
            back_region = self.selector.get_region("back")
            if back_region:
                click_region_center(back_region)
            else:
                press_esc()
            time.sleep(ACTION_WAIT)

        self.mark_progress()
        self.return_follow_rounds += 1
        if self.return_follow_rounds >= SNIPE_RETURN_ROUNDS:
            logger.warning(
                f"[抢购] 连续 {SNIPE_RETURN_ROUNDS} 轮未能回到关注页，退回准备阶段")
            self.status_updated.emit(
                f"⚠️ 连续 {SNIPE_RETURN_ROUNDS} 轮未能回到关注页，退回准备阶段重新筛选")
            self._leave_snipe()
    
    def frame_cut(self, frame, region):
        """裁剪图像区域（自动做边界保护）"""
        h, w = frame.shape[:2]
        left, top, right, bottom = region
        cl, ct = max(0, int(left)), max(0, int(top))
        cr, cb = min(w, int(right)), min(h, int(bottom))
        return frame[ct:cb, cl:cr]

    @staticmethod
    def _prepare_ocr_roi(frame, region):
        """裁剪小区域并补充边缘留白，防止紧贴边界的数字（如 230、0分1秒）被 DBNet 截断

        Returns:
            (padded_roi, origin_x, origin_y)；若区域无效返回 (None, 0, 0)
        """
        h, w = frame.shape[:2]
        left, top, right, bottom = (int(v) for v in region)
        cl, ct = max(0, left), max(0, top)
        cr, cb = min(w, right), min(h, bottom)
        if cr <= cl or cb <= ct:
            return None, 0, 0
        roi = frame[ct:cb, cl:cr]
        rh, rw = roi.shape[:2]
        if rh < 90 or rw < 300:
            pad = 10
            roi = cv2.copyMakeBorder(roi, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
            return roi, cl - pad, ct - pad
        return roi, cl, ct

    def ocr_region(self, region, frame=None):
        """对指定区域做 OCR 识别，返回识别到的第一段文本"""
        if not region:
            return ""
        if frame is None:
            frame = self.capture()
        if frame is None or frame.size == 0:
            return ""
        roi, _, _ = self._prepare_ocr_roi(frame, region)
        if roi is None or roi.size == 0:
            return ""
        res = self.ocr.predict(roi)
        if not res or not res[0]:
            return ""
        try:
            texts = res[0]['rec_texts']
        except (KeyError, TypeError, IndexError):
            return ""
        return "".join(texts) if len(texts) > 1 else (texts[0] if texts else "")

    def ocr_region_items(self, region, frame=None):
        """对指定区域做 OCR，返回 [(文本, x1, y1, x2, y2), ...]（绝对坐标）"""
        if not region:
            return []
        if frame is None:
            frame = self.capture()
        if frame is None or frame.size == 0:
            return []
        roi, ox, oy = self._prepare_ocr_roi(frame, region)
        if roi is None or roi.size == 0:
            return []
        res = self.ocr.predict(roi)
        if not res or not res[0]:
            return []
        try:
            texts = res[0]['rec_texts']
            polys = res[0]['rec_polys']
        except (KeyError, TypeError, IndexError):
            return []
        items = []
        for content, poly in zip(texts, polys):
            xs = [float(p[0]) for p in poly]
            ys = [float(p[1]) for p in poly]
            items.append((content,
                          int(ox + min(xs)), int(oy + min(ys)),
                          int(ox + max(xs)), int(oy + max(ys))))
        return items

    def ocr_region_boxes(self, region, frame=None):
        """对指定区域做 OCR，返回 [(文本, 文本框中心绝对坐标), ...]"""
        return [(text, ((x1 + x2) // 2, (y1 + y2) // 2))
                for text, x1, y1, x2, y2 in self.ocr_region_items(region, frame)]

    def ocr_regions_batch(self, named_regions, frame=None):
        """将多个命名区域纵向拼接为单张画布，1 次 OCR 推理识别全部区域并映射回原屏绝对坐标

        对超宽区域（>740px）自动做水平重叠切分纵向排布，保证画布最大边不超过检测器阈值，
        避免整图被缩小导致小字号文本漏检。

        Args:
            named_regions: [(name, (left, top, right, bottom)), ...]
            frame: 已捕获的整屏画面

        Returns:
            {name: [(text, x1, y1, x2, y2), ...]}
        """
        if frame is None:
            frame = self.capture()
        results = {name: [] for name, _ in named_regions}
        if frame is None or frame.size == 0 or not named_regions:
            return results

        fh, fw = frame.shape[:2]
        max_strip_w = 740
        pad = 10
        sep = 22
        strips = []  # (name, padded_crop, y_start, y_end, origin_x, origin_y)
        cur_y = 0
        canvas_w = 0

        for name, region in named_regions:
            if not region:
                continue
            l, t, r, b = (int(v) for v in region)
            cl, ct = max(0, l), max(0, t)
            cr, cb = min(fw, r), min(fh, b)
            if cr <= cl or cb <= ct:
                continue
            w = cr - cl
            # 超宽条切分成左右重叠两段，防止画布过宽触发 DBNet 降采样
            segments = []
            if w <= max_strip_w:
                segments.append((cl, ct, cr, cb))
            else:
                step = max_strip_w - 40
                sx = cl
                while sx < cr:
                    ex = min(cr, sx + max_strip_w)
                    segments.append((sx, ct, ex, cb))
                    if ex >= cr:
                        break
                    sx += step

            for sl, st, sr, sb in segments:
                crop = frame[st:sb, sl:sr]
                padded = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
                ph, pw = padded.shape[:2]
                y0, y1 = cur_y, cur_y + ph
                strips.append((name, padded, y0, y1, sl - pad, st - pad))
                canvas_w = max(canvas_w, pw)
                cur_y = y1 + sep

        if not strips:
            return results

        canvas_h = strips[-1][3]
        canvas = np.full((canvas_h, canvas_w, 3), 24, dtype=np.uint8)
        for _name, padded, y0, y1, _ox, _oy in strips:
            canvas[y0:y1, :padded.shape[1]] = padded

        res = self.ocr.predict(canvas)
        if not res or not res[0]:
            return results
        try:
            texts = res[0]['rec_texts']
            polys = res[0]['rec_polys']
        except (KeyError, TypeError, IndexError):
            return results

        for content, poly in zip(texts, polys):
            xs = [float(p[0]) for p in poly]
            ys = [float(p[1]) for p in poly]
            cy = sum(ys) / len(ys)
            for name, padded, y0, y1, ox, oy in strips:
                if y0 <= cy <= y1 and min(xs) < padded.shape[1]:
                    results[name].append((
                        content,
                        int(ox + min(xs)),
                        int(oy + (min(ys) - y0)),
                        int(ox + max(xs)),
                        int(oy + (max(ys) - y0)),
                    ))
                    break
        return results

    def _try_skip_result_page(self, frame):
        """识别"获得枪械外观"结果页并点击"跳过"

        购买成功后游戏会弹出该结果页（含"应用外观"/"跳过"两个按钮），
        它会完全挡住交易行列表，不点掉就会一直卡住。
        点击点直接用 OCR 识别到的"跳过"文字中心，因此不依赖精确标定。

        Returns:
            True 表示检测到该页面并已点击"跳过"
        """
        region = self.selector.get_region("skip") or _rect_from_ratio(SKIP_BUTTON_RECT_RATIO)
        for content, center in self.ocr_region_boxes(region, frame):
            if "跳过" in content:
                logger.info(f"[跳过] 检测到获得外观结果页，点击跳过 {center}")
                self.status_updated.emit("⏭️ 检测到获得外观结果页，点击跳过")
                click_point(center)
                time.sleep(0.8)
                return True
        return False

    def _recover_ui(self, page, frame):
        """防卡死恢复：按当前所处页面选择恢复动作

        恢复动作本身也可能无效（比如返回按钮没标定、弹窗不在预期位置），所以这里
        只重置"防卡死计时"、不清空"连续恢复"计数：连续 RECOVER_ESCALATE_LIMIT 次
        都没能让主循环重新出现进展时，直接停下并提示用户，避免无限空转。

        Args:
            page: 当前识别到的页面
            frame: 当前画面，用于结果页判定
        """
        # 暂停/停止后绝不再操作游戏：用户暂停往往就是为了手动接管画面，
        # 此时再点刷新或按 ESC 会把用户的页面打乱（实测日志 19:10:59 已暂停，
        # 19:11:03 仍在执行 [防卡死] 恢复）
        if self.is_paused or not self.is_running:
            return
        self.recover_count += 1
        logger.warning(
            f"[防卡死] 页面 {page} 无进展，执行第 {self.recover_count} 次恢复...")
        self.status_updated.emit(
            f"[防卡死] 页面无进展，尝试恢复（{PAGE_NAMES.get(page, page)}，"
            f"第 {self.recover_count}/{RECOVER_ESCALATE_LIMIT} 次）...")

        # 恢复了一次又一次都没有进展：不再空转，停下来交给用户处理
        if self.recover_count >= RECOVER_ESCALATE_LIMIT:
            logger.error(
                f"[防卡死] 连续 {RECOVER_ESCALATE_LIMIT} 次自动恢复均无效，脚本停止")
            self.is_running = False
            self.task_completed.emit()
            self.status_updated.emit(
                f"🛑 连续 {RECOVER_ESCALATE_LIMIT} 次自动恢复均无效，脚本已停止，"
                "请手动检查游戏画面后重新点「开始」")
            return

        # "获得外观"结果页会完全挡住交易行，优先点掉
        if frame is not None and self._try_skip_result_page(frame):
            self._reset_stall_timer()
            return

        if page == PAGE_FOLLOW:
            # 关注页：点刷新重排关注列表
            refresh_region = self.selector.get_region("refresh")
            if refresh_region:
                click_region_center(refresh_region)
        elif page == PAGE_BROWSE:
            # 已在目标页（商品列表）：重置列表上下文，从头重新扫描
            self.flow.reset_prepare_context()
            self.status_updated.emit("🔁 重置商品列表，从头重新筛选")
        elif page == PAGE_LOBBY:
            # 大厅不是"卡死"，而是被连续 ESC 一路退回了起点之前：点回交易行即可。
            # 特别注意不能按 ESC —— 大厅里 ESC 会弹出暂停菜单，越按越乱
            self.status_updated.emit("🏛 已退回游戏大厅，点「交易行」入口返回")
            self.flow.enter_market()
        elif page == PAGE_MARKET:
            self.status_updated.emit("🖼 已退回交易行，点「典藏外观」返回列表页")
            self.flow.enter_appearance()
        else:
            # 弹窗 / 成色侧栏 / 在售页 / 未识别页面：优先点标定好的返回按钮。
            # 没标定时按 ESC 退回上一层 —— 原来这里用的是固定坐标盲点屏幕中部，
            # 而 back 区域既没标定、也不在标定清单里，实测点了 400 多次都无效
            back_region = self.selector.get_region("back")
            if back_region:
                click_region_center(back_region)
            else:
                press_esc()

        time.sleep(0.8)
        logger.info("[防卡死] 恢复完成，继续监控")
        self.status_updated.emit("[防卡死] 恢复完成，继续监控...")
        self._reset_stall_timer()

    def _evaluate_price_limit(self, cfg, frame=None, silent_pass=False):
        """读取当前商品价格并校验是否不超过上限

        支持在 t=2~4s 空闲窗口提前预审并缓存结果，使 t<=1s 抢购触发点实现 0 次 OCR、0ms 阻塞；
        同时显式透传 frame，避免再次调用 grab() 遇到瞬时无新帧返回 None。

        Returns:
            (allowed: bool, price: Optional[int])；识别失败时返回 (False, None)
        """
        if cfg['follow_only']:
            return True, None
        price_region = self.selector.get_region("price")
        if not price_region:
            if not self._price_region_warned:
                self._price_region_warned = True
                logger.warning("[价格上限] 未配置 price 区域，价格上限暂不生效")
                self.status_updated.emit("⚠️ 未配置价格区域，价格上限暂不生效")
            return True, None

        if frame is None:
            frame = self.capture()
        fallback_region = self.selector.get_region("price_fallback")

        # 若同时配置了主价格区和兜底卡片价格区，单次批量 OCR 同时识别，省掉失败再重试的第二轮推理
        if fallback_region and frame is not None and frame.size > 0:
            batch = self.ocr_regions_batch(
                [("primary", price_region), ("fallback", fallback_region)], frame
            )
            p_text = "".join(t for t, *_ in batch.get("primary", ()))
            price = _to_int(extract_and_merge_digits(p_text))
            if price is None:
                fb_text = "".join(t for t, *_ in batch.get("fallback", ()))
                price = _to_int(extract_and_merge_digits(fb_text))
                if price is not None and not self._price_fallback_warned:
                    self._price_fallback_warned = True
                    logger.info("[价格上限] 详情面板价格未读到，已改用列表第一张卡片价格兜底")
        else:
            price = _to_int(extract_and_merge_digits(self.ocr_region(price_region, frame)))

        if price is None:
            if not silent_pass:
                self.status_updated.emit("⚠️ 价格识别失败，本轮跳过购买")
            return False, None
        if price > cfg['price_limit']:
            if not silent_pass:
                self.status_updated.emit(f"⏭️ 价格 {price} 超过上限 {cfg['price_limit']}，跳过本轮")
            return False, price
        return True, price

    def _price_within_limit(self, cfg, frame=None):
        """购买前检查商品价格是否在上限内（优先读取预审缓存）"""
        if cfg['follow_only']:
            return True
        if self.prechecked_price is not None:
            allowed, price = self.prechecked_price
            if not allowed:
                self.status_updated.emit(
                    f"⏭️ 预审价格 {price} 超过上限 {cfg['price_limit']}，跳过本轮")
            return allowed
        allowed, _ = self._evaluate_price_limit(cfg, frame=frame, silent_pass=False)
        return allowed

    @staticmethod
    def _verify_thumb_from_roi(roi):
        """提取确认按钮区域的轻量灰度特征图（耗时 <0.05ms），用于实时感知弹窗弹出与闭合"""
        if roi is None or roi.size == 0:
            return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (96, 24), interpolation=cv2.INTER_AREA)

    def _verify_diff_ratio(self, verify_region, base_thumb, allow_cached=False) -> float:
        """抓取 verify 区域并计算相对基准图的显著变化像素比例"""
        if base_thumb is None:
            return 0.0
        roi, _ = self.win_cap.grab_region(verify_region, allow_cached=allow_cached)
        if roi is None:
            return 0.0
        cur = self._verify_thumb_from_roi(roi)
        if cur is None:
            return 0.0
        delta = cv2.absdiff(cur, base_thumb)
        _, mask = cv2.threshold(delta, 20, 255, cv2.THRESH_BINARY)
        return float(np.count_nonzero(mask)) / mask.size

    def _prehover_buy_button(self, buy_region):
        """在开抢前提前将鼠标移至「购买」按钮上方悬停，预先激活 UE4/UE5 控件 Hover 态"""
        left, top, right, bottom = buy_region
        hx = (left + right) // 2 + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
        hy = (top + bottom) // 2 + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
        fast_move(hx, hy)
        self.hover_buy_pt = (hx, hy)
        self.buy_hovered = True

    def _buy_and_check(self, cfg, buy_region, verify_region, money_region,
                       last_money_check, base_frame=None):
        """视觉闭环极速抢购执行器：高频探测购买 → 毫秒级识别弹窗切入确认 → 智能无损善后

        核心优化：
          1. 摒弃固定点 2 下购买就盲点确认的开环模式：持续高频点击「购买」覆盖整个倒计时归零窗口，
             同时以 <0.3ms 的局部区域抓帧监测 verify 区域像素突变；**一旦确认弹窗出现，立即中断购买连击、
             毫秒级切入「确认」连击**（既不会早停漏掉 0 秒解锁，也不会弹窗已出还在傻等 0.3s）。
          2. 结合 CountdownWatcher 锁定的亚秒跳秒相位（tick_age），在预计归零解锁区间 [0.80s, 1.25s]
             自动加密点击频率至 ~25ms/次，抢占服务器解锁后的第一帧。
          3. 确认连击期间检测到弹窗已关闭后补 1 下保险即提前收工，省掉空点消耗的 1 秒+。
          4. 智能善后：先单次批量识别 skip / follow_count / money；若确认弹窗已自动关闭且画面已在
             「我的关注」页，**绝不乱按 ESC**（彻底修复以前每次抢完按 ESC 把自己踢出关注页浪费 5~8 秒的问题）。

        Returns:
            (是否成功, 最新的三角币字符串)
        """
        t_start = time.perf_counter()
        tick_age_start = self.countdown.tick_age()

        # 1) 采集点击前的确认按钮区域基准图（用于检测确认弹窗何时弹出、何时关闭）
        base_thumb = None
        if base_frame is not None and base_frame.size > 0:
            base_thumb = self._verify_thumb_from_roi(self.frame_cut(base_frame, verify_region))
        if base_thumb is None:
            roi0, _ = self.win_cap.grab_region(verify_region, allow_cached=True)
            base_thumb = self._verify_thumb_from_roi(roi0)

        if cfg['buy_click_delay'] > 0:
            precise_sleep(cfg['buy_click_delay'])

        # 2) 阶段一：高频点击「购买」，实时侦测确认弹窗
        if self.buy_hovered and self.hover_buy_pt is not None:
            base_bx, base_by = self.hover_buy_pt
        else:
            bl, bt, br, bb = buy_region
            base_bx = (bl + br) // 2 + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
            base_by = (bt + bb) // 2 + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)

        max_buy_clicks = max(1, int(cfg['buy_clicks']))
        cfg_buy_interval = float(cfg['buy_interval'])
        popup_opened = False
        popup_ms = None
        actual_buy_clicks = 0

        for i in range(max_buy_clicks):
            # 微幅抖动保持在购买按钮内部，不丢失 UE4 Hover 焦点
            bx = base_bx + random.randint(-2, 2)
            by = base_by + random.randint(-2, 2)
            fast_click(bx, by, hold=CLICK_HOLD_SECONDS, move=(i > 0 or not self.buy_hovered))
            actual_buy_clicks += 1

            # 立即探测一次弹窗是否已经弹出
            if self._verify_diff_ratio(verify_region, base_thumb) >= VERIFY_POPUP_OPEN_RATIO:
                popup_opened = True
                popup_ms = int((time.perf_counter() - t_start) * 1000)
                break

            if i >= max_buy_clicks - 1:
                break

            # 在预计的倒计时归零点附近（距跳秒 0.80s~1.25s）自动把探测间隔收紧到 <=25ms
            cur_tick_age = self.countdown.tick_age()
            if cur_tick_age is not None and 0.78 <= cur_tick_age <= 1.28:
                step_wait = min(cfg_buy_interval, 0.025)
            else:
                step_wait = cfg_buy_interval

            # 分片等待：等待期间每 ~8ms 探一次弹窗，弹窗一出一毫秒都不多等！
            wait_deadline = time.perf_counter() + step_wait
            while time.perf_counter() < wait_deadline:
                remain = wait_deadline - time.perf_counter()
                precise_sleep(min(0.008, max(0.0, remain)))
                if self._verify_diff_ratio(verify_region, base_thumb) >= VERIFY_POPUP_OPEN_RATIO:
                    popup_opened = True
                    popup_ms = int((time.perf_counter() - t_start) * 1000)
                    break
            if popup_opened:
                break

        if cfg['buy_to_verify_delay'] > 0:
            precise_sleep(cfg['buy_to_verify_delay'])

        # 3) 阶段二：极速连点「确认」，检测到弹窗闭合后补 1 下立即收工
        vl, vt, vr, vb = verify_region
        base_vx = (vl + vr) // 2 + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
        base_vy = (vt + vb) // 2 + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
        cfg_verify_clicks = max(1, int(cfg['verify_clicks']))
        # 若全程未检测到弹窗变化，仍保留最多 8 次兜底确认点击（防 verify 坐标微偏或半透明弹窗）
        max_verify = cfg_verify_clicks if popup_opened else min(cfg_verify_clicks, 8)
        verify_interval = float(cfg['verify_interval'])
        actual_verify_clicks = 0
        popup_closed_early = False

        for i in range(max_verify):
            vx = base_vx + random.randint(-2, 2)
            vy = base_vy + random.randint(-2, 2)
            fast_click(vx, vy, hold=0.007, move=True)
            actual_verify_clicks += 1

            if i >= max_verify - 1:
                break
            if verify_interval > 0:
                precise_sleep(verify_interval)

            # 弹窗曾打开过、且已点满至少 2 次确认后，若 verify 区域已恢复回列表背景则说明弹窗已提交关闭
            if popup_opened and i >= 1:
                diff_now = self._verify_diff_ratio(verify_region, base_thumb)
                if 0.0 < diff_now <= VERIFY_POPUP_CLOSED_RATIO:
                    # 补最后 1 下安全点击后退出循环
                    fast_click(base_vx, base_vy, hold=0.007, move=False)
                    actual_verify_clicks += 1
                    popup_closed_early = True
                    break

        burst_ms = int((time.perf_counter() - t_start) * 1000)
        tick_info = f"跳秒+{int(tick_age_start * 1000)}ms" if tick_age_start is not None else "首帧直触发"
        popup_info = f"{popup_ms}ms检出弹窗" if popup_opened else "未检出弹窗变化(兜底点击)"
        close_info = ", 弹窗闭合即停" if popup_closed_early else ""
        self.status_updated.emit(
            f"⚡ 抢购连击完成({burst_ms}ms | {tick_info} | 买×{actual_buy_clicks}[{popup_info}] "
            f"→ 确认×{actual_verify_clicks}{close_info})"
        )

        # 4) 阶段三：智能无损善后（已在关注页则绝不乱按 ESC，避免自踢出关注页！）
        precise_sleep(POST_BUY_INITIAL_WAIT)
        post_frame = self.capture(max_cache_age=0.02)
        if post_frame is None:
            post_frame = self.capture()

        skip_region = self.selector.get_region("skip") or _rect_from_ratio(SKIP_BUTTON_RECT_RATIO)
        follow_cnt_region = get_region(self.selector, "follow_count")
        now_money = ""

        if post_frame is not None and post_frame.size > 0:
            batch = self.ocr_regions_batch(
                [("skip", skip_region), ("follow_count", follow_cnt_region), ("money", money_region)],
                post_frame,
            )
            skip_items = batch.get("skip", ())
            skip_hit = next((((x1 + x2) // 2, (y1 + y2) // 2)
                             for txt, x1, y1, x2, y2 in skip_items if "跳过" in txt), None)
            follow_txt = "".join(t for t, *_ in batch.get("follow_count", ()))
            money_txt = "".join(t for t, *_ in batch.get("money", ()))
            now_money = extract_and_merge_digits(money_txt)

            if skip_hit is not None:
                logger.info(f"[抢购善后] 检测到获得外观结果页，点击跳过 {skip_hit}")
                self.status_updated.emit("⏭️ 检测到获得外观结果页，点击跳过")
                click_point(skip_hit)
                precise_sleep(0.45)
                now_money = extract_and_merge_digits(self.ocr_region(money_region))
            else:
                on_follow = bool(FOLLOW_COUNT_PATTERN.search(follow_txt))
                modal_still_open = (
                    base_thumb is not None
                    and self._verify_diff_ratio(verify_region, base_thumb, allow_cached=True)
                    >= VERIFY_POPUP_OPEN_RATIO
                )
                if not on_follow or modal_still_open:
                    # 只有弹窗仍卡在画面上或遮挡了关注页标识时，才按一次轻量 ESC 关闭弹窗
                    logger.info("[抢购善后] 弹窗仍打开或未见关注标识，按 ESC 关闭遮挡层")
                    press_esc(wait=0.30)
                    after_esc = self.capture(max_cache_age=0.05) or self.capture()
                    if after_esc is not None and after_esc.size > 0:
                        self._try_skip_result_page(after_esc)
                    now_money = extract_and_merge_digits(self.ocr_region(money_region))
        else:
            press_esc(wait=0.30)
            now_money = extract_and_merge_digits(self.ocr_region(money_region))

        # 5) 核对三角币余额：若首次读取已扣款立即判定成功；否则短等一次服务器结算刷新再复核
        now_money_int = _to_int(now_money)
        last_money_int = _to_int(last_money_check)
        success = (now_money_int is not None and last_money_int is not None
                   and now_money_int < last_money_int)
        if not success:
            precise_sleep(POST_BUY_SETTLE_SECONDS)
            retry_frame = self.capture(max_cache_age=0.02) or self.capture()
            retry_money = extract_and_merge_digits(self.ocr_region(money_region, retry_frame))
            if retry_money:
                now_money = retry_money
                now_money_int = _to_int(now_money)
                success = (now_money_int is not None and last_money_int is not None
                           and now_money_int < last_money_int)

        self.status_updated.emit(f"当前三角币: {now_money or '未读到'}")
        if success:
            self.status_updated.emit(f"✅ 购买成功！扣除三角币，当前: {now_money}")
        return success, now_money

    def run(self):
        """运行脚本：准备阶段（筛选 + 加关注）→ 抢购阶段（我的关注页）"""
        with _WindowsTimerResolution(1):
            self._run_loop()

    def _run_loop(self):
        try:
            self.status_updated.emit("初始化中...")

            time_region = self.selector.get_region("time")
            buy_region = self.selector.get_region("buy")
            verify_region = self.selector.get_region("verify")
            refresh_region = self.selector.get_region("refresh")
            money_region = self.selector.get_region("money")

            required = {
                "time": time_region, "buy": buy_region, "verify": verify_region,
                "refresh": refresh_region, "money": money_region,
            }
            missing = [name for name, region in required.items() if not region]
            if missing:
                raise ValueError(f"区域配置缺失: {', '.join(missing)}，请检查 regions_2k.json")

            if self._get_config().get('follow_only'):
                self.status_updated.emit("监控中...（仅关注页模式：请停在「我的关注」页）")
            else:
                self.status_updated.emit("监控中...（请停在「典藏外观」或「我的关注」页）")

            while self.is_running:
                while self.is_paused and self.is_running:
                    time.sleep(0.2)

                cfg = self._get_config()

                # === 仅关注页模式 ===
                if cfg['follow_only'] and self.phase == PHASE_PREPARE:
                    self.phase = PHASE_SNIPE
                    self.last_page = None
                    self.reset_snipe_context()
                    self.mark_progress()
                    self.status_updated.emit(
                        "🎯 仅关注页模式：跳过准备阶段，直接在「我的关注」页抢购")

                # === 市场关闭时段检查 ===
                if _market_closed(datetime.now(),
                                  cfg['market_close_time'], cfg['market_open_time']):
                    self.status_updated.emit(
                        f"🛑 已进入市场关闭时段（{cfg['market_close_time']}~"
                        f"{cfg['market_open_time']}），脚本已停止")
                    self.task_completed.emit()
                    self.is_running = False
                    break

                # === 防卡死检查 ===
                stall_threshold = (SELF_HEAL_STALL_SECONDS if self.phase == PHASE_SNIPE
                                   else PREPARE_STALL_SECONDS)
                stall_seconds = time.time() - self.last_progress_time
                if stall_seconds > SELF_HEAL_WARN_SECONDS and not self.stall_warned:
                    self.stall_warned = True
                    self.status_updated.emit(
                        f"⚠️ {int(stall_seconds)} 秒未检测到有效进展，页面可能被弹窗遮挡或已卡死"
                    )
                if stall_seconds > stall_threshold:
                    frame = self.capture()
                    if frame is not None and frame.size:
                        self._recover_ui(detect_page(self, self.selector, frame), frame)
                    else:
                        self.mark_progress()
                    continue

                # === 抢购热路径：区分新帧与静止画面，彻底消除原版无新帧时误睡 300ms 的致命黑洞 ===
                if self.phase == PHASE_SNIPE and self.snipe_live:
                    frame, is_new = self.capture_fresh()
                    if not is_new and not self.countdown.resync_due():
                        cd_val = self.countdown.value()
                        if cd_val is not None and cd_val <= 5:
                            precise_sleep(FAST_POLL_INTERVAL)
                        elif cd_val is not None and cd_val <= 12:
                            precise_sleep(MEDIUM_POLL_INTERVAL)
                        else:
                            precise_sleep(min(cfg['ocr_interval'], 0.12))
                        continue
                    if frame is None or frame.size == 0:
                        precise_sleep(FAST_POLL_INTERVAL)
                        continue
                    page = PAGE_FOLLOW
                else:
                    frame = self.capture()
                    if frame is None or frame.size == 0:
                        precise_sleep(0.05 if self.phase == PHASE_SNIPE else PREPARE_POLL_INTERVAL)
                        continue
                    page = detect_page(self, self.selector, frame)

                if page != self.last_page:
                    logger.info(f"[页面] {self.last_page} -> {page}")
                    self.status_updated.emit(f"🖥️ 当前页面：{PAGE_NAMES.get(page, page)}")
                    self.last_page = page
                    self.mark_progress()

                # -------------------- 抢购阶段 --------------------
                if self.phase == PHASE_SNIPE:
                    if page == PAGE_FOLLOW:
                        self.unknown_rounds = 0
                        self.return_follow_rounds = 0
                        # 排序检查避让临期抢购：首次进入先读一轮倒计时；若当前商品只剩 <=7 秒即将开抢，
                        # 绝对优先抢购，推迟排序切换以免错过眼前这件商品！
                        cur_cd = self.countdown.value()
                        if (self.snipe_checked_once
                                and self.follow_sort_tries < FOLLOW_SORT_TRY_LIMIT
                                and (cur_cd is None or cur_cd > 7)):
                            self.follow_sort_tries += 1
                            if self.flow.ensure_follow_sort(frame):
                                self.follow_sort_tries = FOLLOW_SORT_TRY_LIMIT
                                # 切换排序可能改变榜首商品，通知倒计时观察器与价格预审重同步
                                self.countdown.mark_refreshed(0.20)
                                self.prechecked_price = None
                                self.buy_hovered = False

                        self.snipe_checked_once = True
                        if self._snipe_step(frame, cfg) == "empty":
                            if cfg['follow_only']:
                                self.mark_progress()
                                if not self.follow_wait_notified:
                                    self.follow_wait_notified = True
                                    logger.info(
                                        "[仅关注页模式] 关注列表已抢完，等待手动添加关注")
                                    self.status_updated.emit(
                                        "📭 关注列表已抢完，请手动添加关注；"
                                        "加完回到「我的关注」页，脚本会自动继续抢购")
                                time.sleep(FOLLOW_WAIT_POLL_SECONDS)
                            else:
                                self.status_updated.emit("📭 关注列表已清空，退出关注页重新筛选")
                                self._leave_follow_page()
                                self._leave_snipe()
                        else:
                            self.follow_wait_notified = False
                    elif page == PAGE_UNKNOWN:
                        if self._try_skip_result_page(frame):
                            self.unknown_rounds = 0
                            self.mark_progress()
                        elif cfg['follow_only']:
                            self.mark_progress()
                            if not self.follow_wait_notified:
                                self.follow_wait_notified = True
                                self.status_updated.emit(
                                    "⏸ 仅关注页模式：当前不在「我的关注」页，请手动切回")
                            time.sleep(FOLLOW_WAIT_POLL_SECONDS)
                        else:
                            self.unknown_rounds += 1
                            if self.unknown_rounds >= SNIPE_UNKNOWN_LIMIT:
                                self.unknown_rounds = 0
                                self._nav_back_to_follow(PAGE_UNKNOWN, frame)
                    elif cfg['follow_only']:
                        self.mark_progress()
                        if not self.follow_wait_notified:
                            self.follow_wait_notified = True
                            self.status_updated.emit(
                                f"⏸ 仅关注页模式：当前不在「我的关注」页"
                                f"（{PAGE_NAMES.get(page, page)}），请手动切回")
                        time.sleep(FOLLOW_WAIT_POLL_SECONDS)
                    else:
                        self._nav_back_to_follow(page, frame)
                    continue

                # -------------------- 准备阶段 --------------------
                action = "running"
                if page == PAGE_UNKNOWN:
                    self._try_skip_result_page(frame)
                    self.prepare_unknown_rounds += 1
                    if self.prepare_unknown_rounds >= PREPARE_UNKNOWN_LIMIT:
                        self.prepare_unknown_rounds = 0
                        self.status_updated.emit(
                            f"⚠️ 连续 {PREPARE_UNKNOWN_LIMIT} 轮识别不出页面，尝试恢复")
                        self._recover_ui(page, frame)
                else:
                    self.prepare_unknown_rounds = 0
                    action = self.flow.prepare_step(page, frame, cfg)
                    if action == "to_snipe":
                        self.status_updated.emit("⚔️ 进入抢购阶段")
                        self.phase = PHASE_SNIPE
                        self.last_page = None
                        self.reset_snipe_context()
                        self.mark_progress()
                        continue
                    if action == "leave_follow":
                        self.status_updated.emit("📭 关注列表为空，退出关注页重新筛选")
                        self._leave_follow_page()

                precise_sleep(PREPARE_FAST_INTERVAL if action == "fast" else PREPARE_POLL_INTERVAL)
        except Exception as e:
            self.status_updated.emit(f"错误: {str(e)}")
            logger.error("脚本运行错误", exc_info=True)

    def _snipe_step(self, frame, cfg) -> str:
        """执行一轮「我的关注」页抢购逻辑

        Returns:
            'continue' 继续抢购
            'empty'    关注列表已无商品，需要回到准备阶段
        """
        time_region = self.selector.get_region("time")
        buy_region = self.selector.get_region("buy")
        verify_region = self.selector.get_region("verify")
        refresh_region = self.selector.get_region("refresh")
        money_region = self.selector.get_region("money")

        self.snipe_live = False
        kind, total_seconds = self.countdown.update(
            frame, time_region, lambda: self.ocr_region(time_region, frame))
        if kind == "long":
            click_region_center(refresh_region)
            self.countdown.mark_refreshed(0.25)
            self.prechecked_price = None
            self.buy_hovered = False
            self.mark_progress()
            precise_sleep(cfg['ocr_interval'])
            return "continue"
        if kind == "unknown":
            joined, _total = read_follow_count(self, self.selector, frame)
            if joined == 0:
                if not cfg['follow_only']:
                    self.status_updated.emit("📭 关注列表已无商品")
                return "empty"
            if self._try_skip_result_page(frame):
                self.mark_progress()
                self.last_total = None
                precise_sleep(FAST_POLL_INTERVAL)
                return "continue"
            precise_sleep(min(cfg['ocr_interval'], 0.25))
            return "continue"

        self.snipe_live = True
        minutes, seconds = divmod(total_seconds, 60)
        self.timer_updated.emit(str(minutes), str(seconds))

        # 剩余时间变大说明刷新出了新一轮商品，重置本轮所有抢购与预检状态
        if self.last_total is not None and total_seconds > self.last_total:
            self.refreshed = False
            self.buy_done = False
            self.prechecked_price = None
            self.buy_hovered = False

        if total_seconds != self.last_total:
            self.mark_progress()
        self.last_total = total_seconds

        # 1) 剩余 <= 5 秒时点击一次刷新，并标记刷新回包后强制 OCR 校准倒计时与重审价格
        if (total_seconds <= REFRESH_TRIGGER_SECONDS
                and cfg['click_refresh_enabled'] and not self.refreshed):
            self.status_updated.emit(f"🔄 剩余{total_seconds}秒，点击刷新...")
            click_region_center(refresh_region)
            self.refreshed = True
            self.prechecked_price = None
            self.buy_hovered = False
            self.countdown.mark_refreshed(0.22)
            self.mark_progress()
            precise_sleep(0.05)
            return "continue"

        # 2) 刷新稳定后的空闲窗口（2s <= t <= 4s）：提前完成榜首商品价格预审 + 鼠标预悬停至购买钮！
        #    这样进入 t <= 1s 抢购临界点时直接零 OCR、零移动延迟瞬间开火！
        if 1 < total_seconds <= 4 and not self.countdown.resync_due():
            if not cfg['follow_only'] and self.prechecked_price is None:
                allowed, p_val = self._evaluate_price_limit(cfg, frame=frame, silent_pass=True)
                if p_val is not None or (allowed and not self.selector.get_region("price")):
                    self.prechecked_price = (allowed, p_val)
                    if allowed and p_val is not None:
                        logger.info(f"[预审] 榜首商品价格 {p_val} <= 上限 {cfg['price_limit']}，已就绪")
                    elif not allowed:
                        logger.info(f"[预审] 榜首商品价格 {p_val} > 上限 {cfg['price_limit']}，本轮将跳过")
            if total_seconds <= 3 and not self.buy_hovered:
                if self.prechecked_price is None or self.prechecked_price[0]:
                    self._prehover_buy_button(buy_region)

        # 3) 剩余 1 秒及以内（含 0 秒瞬间）执行视觉闭环极速抢购
        if total_seconds <= 1 and not self.buy_done:
            self.buy_done = True
            if not self._price_within_limit(cfg, frame=frame):
                click_region_center(refresh_region)
                self.countdown.mark_refreshed(0.25)
                self.prechecked_price = None
                self.buy_hovered = False
                self.mark_progress()
                return "continue"

            success, money_now = self._buy_and_check(
                cfg, buy_region, verify_region, money_region,
                self.last_money_check, base_frame=frame,
            )
            # 本轮出手完毕，清空预检与悬停状态供下一件商品使用
            self.prechecked_price = None
            self.buy_hovered = False
            self.countdown.reset()

            if success:
                self.consecutive_fail_count = 0
                self.last_money_check = money_now
                self.bought_count += 1
                self.status_updated.emit(f"🎉 抢购成功，累计 {self.bought_count} 件")
                stop_after = int(cfg.get('stop_after_count') or 0)
                if stop_after > 0 and self.bought_count >= stop_after:
                    self.status_updated.emit(
                        f"✅ 已抢到 {self.bought_count} 件，达到设定数量，脚本已停止")
                    self.task_completed.emit()
                    self.is_running = False
                    return "continue"
            else:
                self.consecutive_fail_count += 1
                self.status_updated.emit(
                    f"⚠️ 未检测到扣款（连续未中签: {self.consecutive_fail_count}/{MAX_FAIL_COUNT}）"
                )
                if self.consecutive_fail_count >= MAX_FAIL_COUNT:
                    logger.warning(
                        f"[抢购] 连续 {MAX_FAIL_COUNT} 轮未中签"
                        "（随机抽选机制下属正常现象），继续等待下一轮，不改动页面")
                    self.status_updated.emit(
                        f"ℹ️ 已连续 {MAX_FAIL_COUNT} 轮未中签，继续等待"
                        "（随机抽选机制下属正常现象）")
                    self.consecutive_fail_count = 0

            self.mark_progress()
            self.status_updated.emit("继续监控中...")
            return "continue"

        # 4) 分级动态轮询：t<=5s 4ms 帧级追踪，5<t<=12s 80ms 准点卡刷新，t>12s 按用户间隔
        if total_seconds <= 5:
            precise_sleep(FAST_POLL_INTERVAL)
        elif total_seconds <= 12:
            precise_sleep(MEDIUM_POLL_INTERVAL)
        else:
            precise_sleep(cfg['ocr_interval'])
        return "continue"
    
    def pause(self):
        self.is_paused = True
        # 暂停期间不会产生任何"进展"，卡死计时必须一起停：否则暂停半小时后一按继续，
        # 看门狗会立刻按"长时间无进展"执行恢复（实测日志 18:55:46、21:50:14 都是
        # 暂停结束的同一秒触发恢复）
        self._reset_stall_timer()
    
    def resume(self):
        self.is_paused = False
        # 同上：恢复后重新开始计时，给脚本完整一轮阈值的时间去重新识别页面
        self._reset_stall_timer()
    
    def stop(self):
        self.is_running = False


def main():
    """主函数"""
    base_dir = os.path.dirname(os.path.abspath(__file__))

    app = QApplication(sys.argv)
    selector = RegionSelector()
    # 使用绝对路径，避免提权后工作目录变化导致找不到配置文件
    selector.load_regions_from_file(os.path.join(base_dir, "regions_2k.json"))
    win_cap = WindowCapture(max_buffer_len=2)

    # 初始化 OCR（优先使用 models/ 下的本地模型，否则自动下载）
    try:
        ocr = create_ocr(base_dir)
    except Exception as e:
        logger.error(f"OCR 初始化失败，程序退出: {e}", exc_info=True)
        return

    window = MonitorWindow()
    user_cfg_path = os.path.join(base_dir, USER_CONFIG_FILE)
    if os.path.isfile(user_cfg_path):
        try:
            with open(user_cfg_path, "r", encoding="utf-8") as f:
                window.load_config(json.load(f))
            logger.info(f"已加载用户配置: {user_cfg_path}")
        except Exception as e:
            logger.warning(f"加载 {USER_CONFIG_FILE} 失败，使用默认配置: {e}")

    def save_user_config():
        try:
            with open(user_cfg_path, "w", encoding="utf-8") as f:
                json.dump(window.get_config(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"保存用户配置失败: {e}")

    window.controller.config_changed.connect(save_user_config)
    window.show()
    # 移到屏幕右侧纵向居中。置顶窗口会和游戏画面叠加，必须避开所有已标定区域，
    # 尤其是左下角的 follow_count（详见 WINDOW_LEFT_RATIO 处的说明）
    screen = app.primaryScreen().geometry()
    x = screen.x() + int(screen.width() * WINDOW_LEFT_RATIO)
    y = screen.y() + int(screen.height() * WINDOW_TOP_RATIO)
    window.move(x, y)
    window.add_log("程序已启动")
    window.add_log("点击 [开始] 按钮启动监控")
    script_thread = None
    
    def on_start():
        nonlocal script_thread
        if script_thread and script_thread.isRunning():
            window.add_log("脚本已在运行中")
            return
        window.add_log("正在启动监控线程...")

        # 获取当前配置
        config = window.get_config()
        window.add_log(f"配置: 购买延迟={config['buy_click_delay']}秒")

        # 传入配置回调而非快照，运行中修改参数即可生效
        script_thread = ScriptThread(selector, win_cap, ocr, window.get_config)

        script_thread.status_updated.connect(lambda s: window.update_status(s))
        script_thread.status_updated.connect(lambda s: window.add_log(s))
        script_thread.timer_updated.connect(lambda m, s: window.update_timer(m, s))
        script_thread.task_completed.connect(lambda: window.on_complete())

        script_thread.start()
    
    def on_pause():
        if script_thread:
            script_thread.pause()
    
    def on_resume():
        if script_thread:
            script_thread.resume()
    
    def on_stop():
        if script_thread and script_thread.isRunning():
            script_thread.stop()
            script_thread.wait()
    
    def on_preview():
        frame = win_cap.capture()
        if frame is None or frame.size == 0:
            window.add_log("截图失败，无法预览")
            return
        preview = frame.copy()
        colors = {
            # ---- 抢购阶段 ----
            "time": (0, 255, 255),
            "buy": (0, 255, 0),
            "verify": (0, 165, 255),
            "refresh": (255, 0, 0),
            "money": (255, 0, 255),
            # ---- 页面识别特征区域 ----
            "follow_count": (0, 128, 255),
            "grade_panel_anchor": (0, 200, 200),
            "dialog_anchor": (200, 200, 0),
            "browse_anchor": (128, 0, 255),
            "sale_tab": (255, 128, 0),
            # ---- 列表页 / 筛选弹窗 ----
            "filter_btn": (0, 0, 255),
            "goods_list": (50, 205, 50),
            "detail_buy": (0, 255, 128),
            "my_follow_entry": (255, 255, 0),
            "season_combo": (255, 0, 128),
            "season_options": (128, 128, 255),
            "tier_row": (0, 200, 100),
            "dialog_confirm": (64, 224, 208),
            # ---- 在售列表页 ----
            "goods_grid": (255, 64, 64),
            "grade_options": (160, 32, 240),
            "grade_ok": (34, 139, 34),
            "follow_star": (0, 215, 255),
            # ---- 返航链（大厅 -> 交易行 -> 典藏外观）----
            "lobby_anchor": (255, 191, 0),
            "lobby_market_btn": (0, 128, 128),
            "market_tab_anchor": (128, 0, 128),
            "appearance_tab": (0, 100, 0),
        }
        for name, region in selector.get_all_regions().items():
            left, top, right, bottom = region
            color = colors.get(name, (255, 255, 255))
            cv2.rectangle(preview, (left, top), (right, bottom), color, 2)
            cv2.putText(preview, name, (left, top - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        window.show_preview(QPixmap.fromImage(qimg))

    window.controller.start_requested.connect(on_start)
    window.controller.pause_requested.connect(on_pause)
    window.controller.resume_requested.connect(on_resume)
    window.controller.stop_requested.connect(on_stop)
    window.controller.preview_requested.connect(on_preview)
    
    def cleanup():
        if script_thread and script_thread.isRunning():
            script_thread.stop()
            script_thread.wait()
        win_cap.stop()
    
    app.aboutToQuit.connect(cleanup)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    # 检查并请求管理员权限
    if not is_admin():
        logger.info("检测到程序未以管理员权限运行")
        run_as_admin()
    else:
        logger.info("Delta Force 自动购买脚本 - PyQt6 GUI版本 (管理员模式)")
        main()
