# -*- coding: utf-8 -*-
# @Author: BugNotFound
# @Date: 2025-10-04
# @FilePath: /DeltaForceScript/main_gui.py
# @Description: 带 PyQt6 GUI 的主程序

import os
import sys
import re
import time
import random

import numpy as np
import ctypes
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
    ACTION_WAIT,
    MarketFlow, detect_page, press_esc, read_follow_count,
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
# 末尾几秒的高频轮询间隔（秒），保证不错过 3 秒刷新与 1 秒购买窗口
FAST_POLL_INTERVAL = 0.02
# 点击位置随机偏移范围（像素），避免每次点击坐标完全一致
CLICK_JITTER_PX = 5
# 防卡死：倒计时持续多少秒没有任何变化（含 OCR 完全读不到倒计时）即判定为界面卡死
# 正常倒计时每秒都在变，因此不会误触发；只有页面真的卡住（如被弹窗遮挡）才会恢复
SELF_HEAL_STALL_SECONDS = 5
# 停滞多少秒后输出一次告警日志，便于定位"识别不到倒计时"的问题
SELF_HEAL_WARN_SECONDS = 5
# "获得外观"结果页"跳过"按钮所在扫描区域（默认值取自 2K 屏的 2100,1140 - 2560,1320）
SKIP_BUTTON_RECT_RATIO = (2100 / 2560, 1140 / 1440, 2560 / 2560, 1320 / 1440)
# 脚本窗口的落点（相对主屏）。窗口是置顶的，会和游戏画面叠加，落点必须避开所有
# 已标定区域。2560x1440 上不存在"350x780 且零重叠、按钮又都在屏内"的位置，
# 取舍后选"贴屏幕右侧、纵向居中"：左上角 (2300, 375)、宽度 350。
# 注意：setGeometry 里的 780 只是建议值，Qt 会按各控件的 minimumSizeHint 把窗口
# 撑得更高。在 2560x1440 上实测实际高度 784px（对应 gui_monitor.py 里主布局
# setSpacing(3)/setContentsMargins(3,3,3,3) 与日志框 setMinimumHeight(26) 这两处压缩），
# 底边 y=375+784=1159，因此与 skip 区域的重叠是 y1140~1159 这 19px。
# 该处是文字外的留白，不影响「跳过」二字的识别；关键区域 browse_anchor（y245~375）完整避开。
# 若后续再往界面加控件，务必复测此高度，否则窗口底边会进一步压住「跳过」按钮。
# 代价是窗口右侧约 90px 溢出屏幕外，溢出的正好是最右的"👁 预览"按钮，
# "▶ 开始 / ⏸ 暂停 / ⏹ 停止"仍完整可见，不影响启停操作。
# 特别是不能放在左下角：那里有 follow_count（我的关注：N/150），
# 它是判断"我的关注"页的唯一依据，被压住会导致抢购阶段永远进不去
WINDOW_LEFT_RATIO = 2300 / 2560
WINDOW_TOP_RATIO = 375 / 1440
# 倒计时剩余多少秒时点击刷新（修改后需同步 gui_monitor.py 中复选框的文案）
REFRESH_TRIGGER_SECONDS = 5
# 连续多少轮"未检测到扣款"就提示一次。市场按随机抽选发放购买资格，连续未中签是正常现象，
# 因此这里只输出提示、不再触发任何界面恢复动作（旧逻辑会按 ESC，反而打乱关注页）
MAX_FAIL_COUNT = 10
# 购买+确认点完、按返回键回到关注列表后，等余额刷新再识别三角币的时间（秒）
POST_BUY_SETTLE_SECONDS = 1.0
# 退出「我的关注」页时，按 ESC 后等页面切换完成再重新识别的时间（秒）。
# 实测游戏里由关注页切回上一页要 1~2 秒，若不等就重新识别，会把切换中的画面
# 继续判成关注页，陷入"关注列表空 → ESC → 仍判为关注页 → 又进抢购"的死循环
FOLLOW_EXIT_WAIT_SECONDS = 1.5
# 一次退出关注页时最多补按几次 ESC（每次间隔 FOLLOW_EXIT_WAIT_SECONDS）
FOLLOW_EXIT_ATTEMPTS = 3
# 抢购阶段连续多少轮识别不出关注页内容，就判定为已不在关注页，尝试导航回去
SNIPE_UNKNOWN_LIMIT = 3
# 抢购阶段意外离开关注页后，最多连续尝试"导航回关注页"的轮数。
# 抢购成功后弹出的「获得外观」结果页 + 紧跟的 ESC，很容易把画面带到列表页或在售页，
# 以前这里直接退回准备阶段（等于把关注列表作废、重新筛选一遍）；现在先设法回关注页，
# 只有连续这么多轮都回不去，才认输退回准备阶段
SNIPE_RETURN_ROUNDS = 8
# 「关注页排序」最多尝试的轮数。设置排序要 OCR + 点几下，放进倒计时热路径会拖慢抢购，
# 所以进入关注页后最多试这么多轮；某一轮没搞定（比如下拉面板没展开出来）下一轮再试
FOLLOW_SORT_TRY_LIMIT = 3
# 准备阶段连续多少轮识别不出页面，就主动执行一次通用恢复（关弹窗 / 退回上一层）
# 原来准备阶段遇到未识别页面只是空等，直到 25 秒后被看门狗兜住，期间什么也不做
PREPARE_UNKNOWN_LIMIT = 10
# 连续多少次自动恢复都没有让主循环重新出现进展，就停下并提示用户。
# 恢复动作本身也可能无效（比如点到的坐标不对），若恢复完就重置卡死计时，
# 会变成"恢复 → 计时被重置 → 再恢复"的无限空转（关注页空列表场景曾空转 41 分钟）
RECOVER_ESCALATE_LIMIT = 5
# 连续多少次截屏失败就告警一次。dxcam 在"本轮没有新帧"时会返回 None（属正常现象），
# 但持续失败通常意味着游戏窗口被最小化或屏幕被锁定，此时必须让用户知道
CAPTURE_FAIL_WARN_LIMIT = 10
# 「仅关注页」模式下，关注列表抢空后原地等用户手动加关注时的轮询间隔（秒）。
# 这段等待里每轮都要做页面识别（最多 7 次 OCR），间隔太短只是白烧 GPU
FOLLOW_WAIT_POLL_SECONDS = 1.5
# 市场每日关闭时段（HH:MM）。关闭时段内市场不可交易，脚本会自行停止：
# 跨夜挂机时到点立刻停，避免对着关着的市场空点；时段内启动则直接停。
# 开市时间必须晚于关闭时间，否则视为不启用该功能（防止填错时间导致脚本永远起不来）
MARKET_CLOSE_TIME = "05:00"
MARKET_OPEN_TIME = "08:00"
# 市场时段输入框接受的格式："05:00" / "5:00" / "0500"
TIME_PATTERN = re.compile(r"^(\d{1,2}):?(\d{2})$")
# 准备阶段（筛选 / 加关注）多少秒没有任何进展即判定为卡住
# 准备阶段单步本身包含多次 OCR 与等待，耗时远大于抢购阶段，因此阈值放宽
PREPARE_STALL_SECONDS = 25
# 准备阶段的轮询间隔（秒）；各步骤内部已有点击等待，这里只需短暂让出
PREPARE_POLL_INTERVAL = 0.3
# 本地 OCR 模型根目录（相对脚本所在目录）
MODEL_ROOT = "models"
# 倒计时文本："N分M秒"
COUNTDOWN_PATTERN = re.compile(r'(\d+)\s*分\s*(\d+)\s*秒')

# ========== 倒计时像素差分 ==========
# 抢购热路径上每轮都要 OCR 一次倒计时（det+rec，GPU 约 40~90 ms，CPU 上 100~200 ms），
# 而倒计时数字每秒才变一次 —— 画面没变时再识别一遍是纯浪费。
# 改法：每轮只把倒计时区域缩成小图做像素差分（~0.3 ms），
#   1. 画面没变        -> 缓存值继续有效，跳过 OCR（决策零延迟）
#   2. 画面一秒一跳    -> 本地值 -1，不做 OCR（只有读数字时才需要 OCR）
#   3. 整块画面都变了  -> 页面切换/弹窗/刷新出新轮，强制 OCR 重新取权威值
#   4. 数值掉到 3 秒   -> OCR 校准一次，之后 2 秒、1 秒靠本地递减（购买决策不等 OCR）
#   5. 超过 3 秒没识别 -> 定期校准一次，防止递减漂移
#   6. 一秒内变好几次  -> 数字切换动画/意外抖动，连续几次后退回逐轮 OCR（行为同改前）
COUNTDOWN_SIG_SIZE = (128, 48)      # 差分用的小图尺寸：够分辨数字，又足够快
COUNTDOWN_PIXEL_DELTA = 18          # 单像素灰度差超过它才算"这个点变了"
COUNTDOWN_CHANGED_RATIO = 0.005     # 变化像素占比 ≥0.5% 才认为数字变了（抗区域大小差异）
COUNTDOWN_BIG_RATIO = 0.25          # 变化像素占比 ≥25% 视为整块画面更换，直接 OCR
COUNTDOWN_CHANGE_MIN_GAP = 0.35     # 倒计时不可能比这更快地跳一格
COUNTDOWN_CONFIRM_AT = 3            # 数值到 3 秒时 OCR 校准一次
COUNTDOWN_RESYNC_SECONDS = 3.0      # 这么久没 OCR 过就校准一次（只对剩余 10 秒内生效）
# 最后 3 秒（购买决策区间）校准更勤一些：万一画面卡住不动，也不会拿旧值连着点
COUNTDOWN_RESYNC_FAST_SECONDS = 1.5
COUNTDOWN_UNSTABLE_LIMIT = 3        # 连续这么多次"跳太快"就退回纯 OCR


class CountdownWatcher:
    """用像素差分代替"每轮 OCR"来跟踪倒计时

    update() 返回 (kind, total_seconds):
        ("value", n)     剩余 n 秒（OCR 权威值，或按跳秒本地递减得到）
        ("long", None)   倒计时按"天/小时"显示（市场还没开）
        ("unknown", None) 区域里没有可识别的倒计时（可能已不在关注页）
    """

    def __init__(self):
        self.auto = True          # False = 差分不可靠，退回逐轮 OCR
        self._sig = None          # 上一次 OCR 时的画面签名
        self._value = None        # 上一次 OCR 得到的剩余秒数
        self._last_change_at = 0.0
        self._last_ocr_at = 0.0
        self._fast_hits = 0

    def reset(self):
        """页面可能已切换：丢掉签名与缓存值，下一轮重新 OCR"""
        self._sig = None
        self._value = None
        self._last_change_at = 0.0
        self._fast_hits = 0
        # 重新给差分一次机会。以前 auto=False 是"一降级就永久逐轮 OCR"：实测
        # 11:28~11:30 连续降级 4 次后，整晚都在逐轮 OCR，像素差分优化实际没生效
        self.auto = True

    def value(self):
        """最近一次确认的剩余秒数（None 表示还不知道）"""
        return self._value

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
        if not self.auto:
            return self._read(read, sig)
        if sig is None:
            return ("unknown", None)
        if self._sig is None or self._value is None:
            return self._read(read, sig)

        ratio = self._changed_ratio(sig, self._sig)
        now = time.time()
        if ratio < COUNTDOWN_CHANGED_RATIO:
            # 画面没变 -> 显示的仍是缓存那个数；只在剩余时间不大时定期校准
            resync = (COUNTDOWN_RESYNC_FAST_SECONDS if self._value <= 3
                      else COUNTDOWN_RESYNC_SECONDS)
            if self._value <= 10 and now - self._last_ocr_at > resync:
                return self._read(read, sig)
            return ("value", self._value)
        if ratio >= COUNTDOWN_BIG_RATIO:
            # 整块画面换了（切页面/弹窗/刷新出新轮）：缓存值不可信
            self._last_change_at = now
            return self._read(read, sig)

        gap = now - self._last_change_at
        self._last_change_at = now
        if gap < COUNTDOWN_CHANGE_MIN_GAP:
            # 比"一秒一跳"快得多：数字切换动画或画面抖动，用 OCR 兜底
            self._fast_hits += 1
            if self._fast_hits >= COUNTDOWN_UNSTABLE_LIMIT:
                self.auto = False
                logger.warning("[倒计时] 差分判定抖动过多，已退回逐轮 OCR（不影响功能）")
            return self._read(read, sig)
        self._fast_hits = 0

        nxt = self._value - 1
        if nxt < 0 or nxt == COUNTDOWN_CONFIRM_AT:
            # 归零/刷新后跳回大数值/到 3 秒：用 OCR 拿权威值
            return self._read(read, sig)
        # 正常跳秒：本地递减，购买决策走这条路径，不需要等 OCR
        self._value = nxt
        self._sig = sig
        return ("value", nxt)

    def _read(self, read, sig):
        """做一次 OCR 并更新缓存；读不到就清空状态交由调用方兜底"""
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


def click_region_center(region: tuple, clicks=1, interval=0.1):
    """点击区域的中心位置

    Args:
        region: (left, top, right, bottom) 格式的区域坐标
        clicks: 点击次数
        interval: 多次点击之间的间隔（秒）
    """
    left, top, right, bottom = region
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2

    # 在 ±CLICK_JITTER_PX 像素范围内随机偏移，避免固定坐标被检测
    center_x += random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)
    center_y += random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX)

    pydirectinput.click(x=center_x, y=center_y, clicks=clicks, interval=interval, button=pydirectinput.LEFT)


def click_point(point: tuple):
    """在指定像素点附近随机抖动后点击

    Args:
        point: (x, y) 屏幕坐标
    """
    x, y = point
    pydirectinput.click(
        x=x + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX),
        y=y + random.randint(-CLICK_JITTER_PX, CLICK_JITTER_PX),
        button=pydirectinput.LEFT,
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
    # PaddleOCR 初始化时会把根 logger 的级别调高（实测此后 INFO 全部被丢弃，
    # logs\*.log 里只剩 WARNING/ERROR，界面上看到的每一步状态都写不进文件）。
    # 这里在 OCR 就绪后把级别恢复成 INFO，保证运行过程可追溯
    logging.getLogger().setLevel(logging.INFO)
    for _handler in logging.getLogger().handlers:
        _handler.setLevel(logging.INFO)
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
        self.follow_sort_tries = 0        # 本次进入关注页已尝试设置排序的轮数
        self.snipe_live = False          # 上一轮是否读到过倒计时（用于跳过重复的页面识别）
        self.countdown = CountdownWatcher()  # 倒计时像素差分：大多数轮次不再 OCR
        self.prepare_unknown_rounds = 0  # 准备阶段连续识别不出页面的轮数
        self.recover_count = 0           # 连续自动恢复的次数（真正有进展时清零）
        self.capture_fail_count = 0      # 连续截屏失败的次数

    # ------------------------------------------------------------ ops 接口
    def capture(self):
        """截取整屏画面

        dxcam 在"本轮没有新帧"时会返回 None（属正常现象），但持续失败通常意味着
        游戏窗口被最小化或屏幕被锁定。这里累计到阈值后告警一次，避免脚本静默空转。
        """
        frame = self.win_cap.capture()
        if frame is None or frame.size == 0:
            self.capture_fail_count += 1
            if self.capture_fail_count == CAPTURE_FAIL_WARN_LIMIT:
                logger.warning("[截屏] 连续 %d 次截屏失败", CAPTURE_FAIL_WARN_LIMIT)
                self.status_updated.emit(
                    f"⚠️ 连续 {CAPTURE_FAIL_WARN_LIMIT} 次截屏失败，"
                    "请确认游戏窗口未最小化、屏幕未被锁定")
            return None
        self.capture_fail_count = 0
        return frame

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
        self.snipe_live = False
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
        """裁剪图像区域"""
        left, top, right, bottom = region
        return frame[top:bottom, left:right]

    def ocr_region(self, region, frame=None):
        """对指定区域做 OCR 识别，返回识别到的第一段文本

        Args:
            region: (left, top, right, bottom) 区域坐标
            frame: 已捕获的整屏画面；为 None 时自行截屏（同一轮内复用可少截一次）
        """
        if frame is None:
            frame = self.capture()
        if frame is None or frame.size == 0:
            return ""
        roi = self.frame_cut(frame, region)
        res = self.ocr.predict(roi)
        if not res or not res[0]:
            return ""
        try:
            texts = res[0]['rec_texts']
        except (KeyError, TypeError, IndexError):
            return ""
        return texts[0] if texts else ""

    def ocr_region_items(self, region, frame=None):
        """对指定区域做 OCR，返回 [(文本, x1, y1, x2, y2), ...]（绝对坐标）

        Args:
            region: (left, top, right, bottom) 区域坐标
            frame: 已捕获的整屏画面；为 None 时自行截屏
        """
        if frame is None:
            frame = self.capture()
        if frame is None or frame.size == 0:
            return []
        left, top, right, bottom = region
        res = self.ocr.predict(frame[top:bottom, left:right])
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
                          int(left + min(xs)), int(top + min(ys)),
                          int(left + max(xs)), int(top + max(ys))))
        return items

    def ocr_region_boxes(self, region, frame=None):
        """对指定区域做 OCR，返回 [(文本, 文本框中心绝对坐标), ...]"""
        return [(text, ((x1 + x2) // 2, (y1 + y2) // 2))
                for text, x1, y1, x2, y2 in self.ocr_region_items(region, frame)]

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

    def _price_within_limit(self, cfg):
        """购买前检查商品价格是否在上限内

        价格为 OCR 识别结果，识别不到时按不购买处理（避免超价买入）。
        price 区域未配置时不做拦截，保持原有的直接购买行为，并只告警一次。

        Returns:
            True 表示可以购买
        """
        if cfg['follow_only']:
            # 仅关注页模式：关注列表是用户自己一个个挑出来的，不再套价格上限
            return True
        price_region = self.selector.get_region("price")
        if not price_region:
            if not self._price_region_warned:
                self._price_region_warned = True
                logger.warning("[价格上限] 未配置 price 区域，价格上限暂不生效")
                self.status_updated.emit("⚠️ 未配置价格区域，价格上限暂不生效")
            return True

        price = _to_int(extract_and_merge_digits(self.ocr_region(price_region)))
        if price is None:
            # 详情面板按钮里的价格在刷新后有一小段渲染空窗（实机日志里约一半轮次读不到），
            # 用列表第一张卡片的价格兜底：卡片价格刷新后先出现，且与详情面板是同一件商品
            fallback_region = self.selector.get_region("price_fallback")
            if fallback_region:
                price = _to_int(extract_and_merge_digits(self.ocr_region(fallback_region)))
                if price is not None and not self._price_fallback_warned:
                    self._price_fallback_warned = True
                    logger.info("[价格上限] 详情面板价格未读到，已改用列表第一张卡片价格兜底")
        if price is None:
            self.status_updated.emit("⚠️ 价格识别失败，本轮跳过购买")
            return False
        if price > cfg['price_limit']:
            self.status_updated.emit(f"⏭️ 价格 {price} 超过上限 {cfg['price_limit']}，跳过本轮")
            return False
        return True

    def _buy_and_check(self, cfg, buy_region, verify_region, money_region, last_money_check):
        """执行一轮购买+确认，按返回回到关注列表后对比三角币判断是否成功

        Returns:
            (是否成功, 最新的三角币字符串)
        """
        self.status_updated.emit("准备点击...")
        time.sleep(cfg['buy_click_delay'])
        self.status_updated.emit(f"连续点击购买 {cfg['buy_clicks']} 次...")
        # 最后一次点完直接进下一步，不再等待：购买点完就该立刻去点确认，
        # 在末尾多睡一个 buy_interval（默认 0.3 秒）纯属浪费
        for i in range(cfg['buy_clicks']):
            click_region_center(buy_region, interval=0)
            if i < cfg['buy_clicks'] - 1:
                time.sleep(cfg['buy_interval'])
        time.sleep(cfg['buy_to_verify_delay'])
        self.status_updated.emit(f"连续点击确认 {cfg['verify_clicks']} 次...")
        for i in range(cfg['verify_clicks']):
            click_region_center(verify_region, interval=0)
            if i < cfg['verify_clicks'] - 1:
                time.sleep(cfg['verify_interval'])

        # 购买+确认点完，交易就已提交，直接按返回键回到关注列表：
        # 不再等 1.5 秒结算、也不再点刷新。抢购窗口只有 1 秒，这两步纯是浪费
        self.status_updated.emit("↩️ 购买+确认已点完，按返回键回到关注列表")
        press_esc()

        # 回到关注列表后等余额刷新，再识别三角币判断是否扣款
        time.sleep(POST_BUY_SETTLE_SECONDS)
        now_money = extract_and_merge_digits(self.ocr_region(money_region))
        self.status_updated.emit(f"当前三角币: {now_money}")

        # 识别失败时按失败处理，避免 OCR 返回空串被误判为"余额减少"
        now_money_int = _to_int(now_money)
        last_money_int = _to_int(last_money_check)
        success = (now_money_int is not None and last_money_int is not None
                   and now_money_int < last_money_int)
        if success:
            self.status_updated.emit(f"✅ 购买成功！扣除三角币，当前: {now_money}")
        return success, now_money

    def run(self):
        """运行脚本：准备阶段（筛选 + 加关注）→ 抢购阶段（我的关注页）"""
        try:
            self.status_updated.emit("初始化中...")

            time_region = self.selector.get_region("time")
            buy_region = self.selector.get_region("buy")
            verify_region = self.selector.get_region("verify")
            refresh_region = self.selector.get_region("refresh")
            money_region = self.selector.get_region("money")

            # 缺少区域配置时直接报错，避免后续裁剪/点击抛出不明确的异常
            required = {
                "time": time_region, "buy": buy_region, "verify": verify_region,
                "refresh": refresh_region, "money": money_region,
            }
            missing = [name for name, region in required.items() if not region]
            if missing:
                raise ValueError(f"区域配置缺失: {', '.join(missing)}，请检查 regions_2k.json")

            # 起点两种都支持：停在「典藏外观」列表页走完整准备阶段；
            # 若游戏已经停在「我的关注」页，则跳过准备阶段直接抢购
            if self._get_config().get('follow_only'):
                self.status_updated.emit("监控中...（仅关注页模式：请停在「我的关注」页）")
            else:
                self.status_updated.emit("监控中...（请停在「典藏外观」或「我的关注」页）")

            while self.is_running:
                # 暂停时等待
                while self.is_paused and self.is_running:
                    time.sleep(0.2)

                # 每轮重新读取配置，使 GUI 中修改的参数即时生效
                cfg = self._get_config()

                # === 仅关注页模式 ===
                # 勾选后跳过整个准备阶段（不筛选商品、不自动加关注），直接在
                # 「我的关注」页抢购。运行中随时勾选/取消都会在下一轮生效
                if cfg['follow_only'] and self.phase == PHASE_PREPARE:
                    self.phase = PHASE_SNIPE
                    self.last_page = None
                    self.reset_snipe_context()
                    self.mark_progress()
                    self.status_updated.emit(
                        "🎯 仅关注页模式：跳过准备阶段，直接在「我的关注」页抢购")

                # === 市场关闭时段检查 ===
                # 放在所有点击动作之前：市场关着时空点毫无意义，还容易触发卡死恢复
                if _market_closed(datetime.now(),
                                  cfg['market_close_time'], cfg['market_open_time']):
                    self.status_updated.emit(
                        f"🛑 已进入市场关闭时段（{cfg['market_close_time']}~"
                        f"{cfg['market_open_time']}），脚本已停止")
                    self.task_completed.emit()
                    self.is_running = False
                    break

                # === 防卡死检查 ===
                # 必须放在截图/OCR 之前：页面卡死或被弹窗遮挡时 OCR 根本读不到特征文本，
                # 若放在后面的 early-continue 之后，这种最需要恢复的场景永远触发不了
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

                # 截图并识别当前页面（本轮复用同一张截图，避免重复截屏）
                frame = self.capture()
                if frame is None or frame.size == 0:
                    time.sleep(PREPARE_POLL_INTERVAL)
                    continue
                # 抢购阶段的热路径：上一轮刚读到过倒计时，说明当时确实在关注页上，
                # 这一轮就不必再做页面识别（它也靠 OCR 关注页标识，属于重复劳动）。
                # 末尾几秒每秒要轮询十几次，省掉的这次 OCR 直接变成点击延迟；
                # 一旦读不到倒计时，_snipe_step 会把 snipe_live 置回 False，
                # 下一轮就恢复完整的页面识别
                if self.phase == PHASE_SNIPE and self.snipe_live:
                    page = PAGE_FOLLOW
                else:
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
                        if self.follow_sort_tries < FOLLOW_SORT_TRY_LIMIT:
                            # 进入关注页先确保排序是「按稀有度升序」。它要 OCR + 点几下，
                            # 放进倒计时的热路径里会拖慢抢购，所以最多试 FOLLOW_SORT_TRY_LIMIT 轮；
                            # 某一轮没搞定（下拉面板没展开出来等）下一轮接着试
                            self.follow_sort_tries += 1
                            if self.flow.ensure_follow_sort(frame):
                                self.follow_sort_tries = FOLLOW_SORT_TRY_LIMIT
                        if self._snipe_step(frame, cfg) == "empty":
                            if cfg['follow_only']:
                                # 仅关注页模式：列表抢完就停在原地等用户手动加关注，
                                # 不退出关注页、也不回准备阶段。这里必须 mark_progress，
                                # 否则 5 秒后看门狗会来"恢复"（点刷新），连续几次还会
                                # 判成"恢复无效"直接把脚本停掉 —— 而这是有意为之的等待
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
                            # 又读到了关注页的内容（用户加完关注回来了），
                            # 允许下次抢空时再提醒一次
                            self.follow_wait_notified = False
                    elif page == PAGE_UNKNOWN:
                        # 可能是购买后弹出的"获得外观"结果页
                        if self._try_skip_result_page(frame):
                            self.unknown_rounds = 0
                            self.mark_progress()
                        elif cfg['follow_only']:
                            # 仅关注页模式：认不出画面又不是结果页（多半是您手动翻到了
                            # 别的页面）。这里刻意什么都不点，免得干扰您手动操作，
                            # 只等您自己切回「我的关注」页
                            self.mark_progress()
                            if not self.follow_wait_notified:
                                self.follow_wait_notified = True
                                self.status_updated.emit(
                                    "⏸ 仅关注页模式：当前不在「我的关注」页，请手动切回")
                            time.sleep(FOLLOW_WAIT_POLL_SECONDS)
                        else:
                            # 连续多轮都认不出画面：多半是购买后的结果页没点掉、或页面
                            # 状态异常。不再直接退回准备阶段，先设法导航回关注页
                            self.unknown_rounds += 1
                            if self.unknown_rounds >= SNIPE_UNKNOWN_LIMIT:
                                self.unknown_rounds = 0
                                self._nav_back_to_follow(PAGE_UNKNOWN, frame)
                    elif cfg['follow_only']:
                        # 仅关注页模式：不在「我的关注」页时什么都不点（绝不导航，
                        # 免得干扰您正在手动挑商品），只等您自己切回关注页
                        self.mark_progress()
                        if not self.follow_wait_notified:
                            self.follow_wait_notified = True
                            self.status_updated.emit(
                                f"⏸ 仅关注页模式：当前不在「我的关注」页"
                                f"（{PAGE_NAMES.get(page, page)}），请手动切回")
                        time.sleep(FOLLOW_WAIT_POLL_SECONDS)
                    else:
                        # 意外离开了关注页：先设法导航回关注页继续抢购，而不是直接
                        # 退回准备阶段。抢购成功后的结果页 + ESC 很容易落到列表页或
                        # 在售页，这时回准备阶段会把已经关注好的商品全部作废重来
                        self._nav_back_to_follow(page, frame)
                    continue

                # -------------------- 准备阶段 --------------------
                if page == PAGE_UNKNOWN:
                    # 先看是不是购买后弹出的"获得外观"结果页；连续多轮都认不出画面
                    # 时主动执行一次通用恢复（关弹窗 / 退回上一层），否则会一直在这里
                    # 空转，直到 PREPARE_STALL_SECONDS 之后才被看门狗兜住
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
                        # 关注列表是空的：进抢购只会空转（见 _leave_follow_page），
                        # 说明上一次的"退出关注页"还没生效，这里先确保真的退出去
                        self.status_updated.emit("📭 关注列表为空，退出关注页重新筛选")
                        self._leave_follow_page()

                time.sleep(PREPARE_POLL_INTERVAL)
        except Exception as e:
            self.status_updated.emit(f"错误: {str(e)}")
            logger.error("脚本运行错误", exc_info=True)

    def _snipe_step(self, frame, cfg) -> str:
        """执行一轮「我的关注」页抢购逻辑

        逻辑与旧版单体循环一致，只是把等待/分支改为返回状态，由 run() 负责阶段切换。

        Returns:
            'continue' 继续抢购
            'empty'    关注列表已无商品，需要回到准备阶段
        """
        time_region = self.selector.get_region("time")
        buy_region = self.selector.get_region("buy")
        verify_region = self.selector.get_region("verify")
        refresh_region = self.selector.get_region("refresh")
        money_region = self.selector.get_region("money")

        # 抢购窗口只有 1 秒，末尾每秒要轮询十几次，这里每多一次 OCR 就多几十~几百
        # 毫秒的识别延迟（CPU 上尤其明显），直接变成"比别人慢 0.x 秒"。
        # 因此先做像素差分：画面没变就直接用缓存值；只有数字跳格时才 OCR，
        # 其余判断全部推迟到"读不到倒计时"时再做（细节见 CountdownWatcher）
        self.snipe_live = False
        kind, total_seconds = self.countdown.update(
            frame, time_region, lambda: self.ocr_region(time_region, frame))
        if kind == "long":
            click_region_center(refresh_region)
            self.mark_progress()
            time.sleep(cfg['ocr_interval'])
            return "continue"
        if kind == "unknown":
            # 读不到倒计时，才需要花时间判断页面状态
            # 关注列表清空 → 本轮抢购结束（列表空了自然也就没有倒计时可读）
            joined, _total = read_follow_count(self, self.selector, frame)
            if joined == 0:
                # 仅关注页模式下会一直停在这一页等用户手动加关注，这里不再每轮刷
                # "已无商品"（否则会把 run() 的一次性提醒顶掉），提醒统一由 run() 负责
                if not cfg['follow_only']:
                    self.status_updated.emit("📭 关注列表已无商品")
                return "empty"
            # 优先判断是否为"获得外观"结果页，是就点"跳过"走人；
            # 该页面会完全挡住交易行，不点掉会一直卡住
            if self._try_skip_result_page(frame):
                self.mark_progress()
                self.last_total = None
                time.sleep(FAST_POLL_INTERVAL)
                return "continue"
            time.sleep(cfg['ocr_interval'])
            return "continue"

        # 读到倒计时说明确实在关注页上：给 run() 一个信号，下一轮可以跳过页面识别
        self.snipe_live = True
        minutes, seconds = divmod(total_seconds, 60)
        # 更新时间显示
        self.timer_updated.emit(str(minutes), str(seconds))

        # 剩余时间变大说明刷新出了新一轮，重置本轮标志（否则同一轮会重复点击）
        if self.last_total is not None and total_seconds > self.last_total:
            self.refreshed = False
            self.buy_done = False

        # 倒计时数值发生变化即视为"有进展"，重置卡死计时
        if total_seconds != self.last_total:
            self.mark_progress()
        self.last_total = total_seconds

        # 剩余指定秒数时点击刷新（每轮仅触发一次）。
        # 用 <= 兜底：OCR 误读或本地递减都可能整格跳过"5 秒"，严格相等会整轮不刷新
        if (total_seconds <= REFRESH_TRIGGER_SECONDS
                and cfg['click_refresh_enabled'] and not self.refreshed):
            self.status_updated.emit(f"🔄 剩余{total_seconds}秒，点击刷新...")
            click_region_center(refresh_region)
            self.refreshed = True
            self.mark_progress()
            time.sleep(FAST_POLL_INTERVAL)
            return "continue"

        # 剩余 1 秒及以内执行购买；用 <= 兜底，避免 OCR 漏掉那一秒导致整轮不点击
        if total_seconds <= 1 and not self.buy_done:
            self.buy_done = True
            # 价格超过上限（或价格读不出来）时本轮不购买，直接刷新等下一轮
            if not self._price_within_limit(cfg):
                click_region_center(refresh_region)
                self.mark_progress()
                return "continue"
            success, money_now = self._buy_and_check(
                cfg, buy_region, verify_region, money_region, self.last_money_check,
            )
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
                    f"⚠️ 未检测到扣款（连续失败: {self.consecutive_fail_count}/{MAX_FAIL_COUNT}）"
                )
                # 连续失败达到阈值 → 只提示，不做任何界面操作。
                # 市场改成"随机抽选购买资格"后，连点几十轮都没中签是正常现象；
                # 以前这里当成界面卡死、按 ESC 恢复，反而把关注页/上一页打乱
                # （实测日志 19:10:40：连续 10 次未扣款后立刻触发 [防卡死] 恢复）
                if self.consecutive_fail_count >= MAX_FAIL_COUNT:
                    logger.warning(
                        f"[抢购] 连续 {MAX_FAIL_COUNT} 轮未中签"
                        "（随机抽选机制下属正常现象），继续等待下一轮，不改动页面")
                    self.status_updated.emit(
                        f"ℹ️ 已连续 {MAX_FAIL_COUNT} 轮未中签，继续等待"
                        "（随机抽选机制下属正常现象）")
                    self.consecutive_fail_count = 0

            # 购买流程本身也算"有进展"，重置卡死计时避免误判
            self.mark_progress()
            self.status_updated.emit("继续监控中...")
            return "continue"

        # 距离触发点还早时按配置间隔识别，末尾几秒改为高频轮询以抓住触发窗口
        time.sleep(cfg['ocr_interval'] if total_seconds > 5 else FAST_POLL_INTERVAL)
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
