# -*- coding: utf-8 -*-
"""市场多页面流程状态机：页面识别 + 准备阶段（筛选 / 加关注）

依赖方向：main_gui.py -> page_flow.py
本模块不 import main_gui（否则会循环导入）。它通过构造参数接收的 ``ops``
对象来完成截图 / OCR / 点击，``ops`` 由 ScriptThread 自己传入。

页面与流程（起始点：用户手动停留在「典藏外观」列表页，或直接停在「我的关注」页）：
    列表页 --点「筛选」--> 筛选弹窗 --选赛季+勾品阶+确认--> 列表页(已筛选)
    列表页 --逐个点商品--> --点「购买」--> 在售页 --点「筛选」--> 成色侧栏
    --勾成色+确定--> 在售页(已筛选) --逐卡片读价格--> 价格达标的点★关注
    在售页 --ESC--> 列表页 --下一个商品...--> 列表页全部处理完
    --点「我的关注」--> 关注页（进入抢购阶段，逻辑在 main_gui 中）

起点直接是关注页时，上面整条准备链都不走，直接进抢购阶段。

防卡死恢复是"退回上一层"（连按 ESC），连着几次就会一路退回游戏大厅；因此还有一条
"返航链"，用来从大厅回到列表页（否则脚本会停在大厅里认不出页面、一直空转）：
    游戏大厅 --点底部图标栏的「交易行」入口--> 交易行 --点「典藏外观」标签--> 列表页

默认坐标基于 2560x1440，与 regions_2k.json 基准一致；用户可用
region_selector.py 重新标定来覆盖。
"""

import ctypes
import logging
import re
import time

import numpy as np
import pydirectinput

logger = logging.getLogger(__name__)

# ===================== 页面标识 =====================
PAGE_BROWSE = "browse"            # 典藏外观列表页（左侧商品列表 + 右侧详情）
PAGE_FILTER_DIALOG = "dialog"     # 筛选弹窗（赛季 / 查看 / 品阶 / 枪种）
PAGE_SALE = "sale"                # 在售列表页
PAGE_GRADE_PANEL = "grade"        # 在售页的成色筛选侧栏
PAGE_FOLLOW = "follow"            # 我的关注页
PAGE_LOBBY = "lobby"              # 游戏大厅（防卡死连按 ESC 会一路退到这里）
PAGE_MARKET = "market"            # 交易行首页（顶部有 曼德尔砖 / 典藏外观 / 典藏饰品 三个标签）
PAGE_UNKNOWN = "unknown"          # 未识别

# ===================== 流程阶段 =====================
PHASE_PREPARE = "prepare"         # 准备阶段：筛选 + 逐个商品加关注
PHASE_SNIPE = "snipe"             # 抢购阶段：在关注页抢购

# ===================== 默认区域坐标（2560x1440 基准）=====================
DEFAULT_REGIONS = {
    # ---- 页面识别特征区域 ----
    "follow_count": (110, 1240, 320, 1285),        # "我的关注：N/150"
    "grade_panel_anchor": (1900, 1320, 2400, 1385),  # "重置" / "确定"
    "dialog_anchor": (500, 615, 1900, 780),        # "品阶" / "枪种"
    "browse_anchor": (2260, 245, 2400, 375),       # "我的关注" / "典藏工坊"
    "sale_tab": (200, 75, 320, 130),               # 顶部"在售"页签
    # ---- 通用 ----
    "filter_btn": (180, 232, 440, 305),            # "筛选"按钮（列表页/在售页同位）
    # ---- 典藏外观列表页 ----
    "goods_list": (110, 320, 550, 1290),           # 左侧商品列表
    "detail_buy": (2150, 1145, 2350, 1210),        # 右侧"购买"按钮
    "my_follow_entry": (2250, 245, 2400, 300),     # 右侧"我的关注"入口
    # ---- 我的关注页的排序下拉框 ----
    # 实测自 2560×1440 截图：下拉框在工具栏右侧，当前值"默认排序"的文字占
    # x1149~1239 / y248~277；展开后的选项列表向下延伸（首项"默认排序"y≈321、
    # 第 2 项"按稀有度升序"y≈387，行距约 64px）
    # 判定逻辑不依赖坐标精度：只有 OCR 到排序名（含"排序/升序/降序"的文字）才会点，
    # 且点的是**文字自身的位置**而不是区域中心；框错了最多"没生效"，不会误点别处
    "follow_sort": (1000, 222, 1790, 302),        # 排序下拉框本体（显示「默认排序」）
    "follow_sort_options": (1000, 305, 1790, 745),  # 点开后向下展开的选项区
    # ---- 筛选弹窗 ----
    "season_combo": (735, 410, 1155, 480),         # 赛季下拉框
    # 下拉展开后的选项区域。实测（2560x1440）面板约 x750~1103 / y468~885，
    # 右边界收到 1100 是为了排除右侧那一列复选框文字（「未拥有」「史诗品阶」
    # 「冲锋枪」等，x>=1118），否则会污染 OCR 结果
    "season_options": (750, 490, 1100, 890),
    "tier_row": (760, 615, 1900, 682),             # 品阶四个复选框所在行
    "dialog_confirm": (1200, 960, 1360, 1045),     # "确认"按钮
    # ---- 在售列表页 ----
    "goods_grid": (115, 300, 1869, 1125),          # 商品卡片网格整体区域
    "grade_options": (1830, 430, 2300, 650),       # 成色侧栏的复选项
    "grade_ok": (2260, 1320, 2420, 1385),          # 成色侧栏"确定"按钮
    "follow_star": (2318, 292, 2368, 342),         # 右侧详情面板的★关注星标
    # ---- 返航链：游戏大厅 -> 交易行 -> 典藏外观（实测坐标取自 2560x1440 截图）----
    "lobby_anchor": (180, 48, 1350, 84),           # 大厅顶部"开始游戏 / 特战干员 / 改枪台"
    "lobby_market_btn": (625, 1365, 726, 1436),    # 大厅底部图标栏里的"交易行"入口
    "market_tab_anchor": (200, 84, 660, 120),      # 交易行顶部"曼德尔砖 / 典藏外观 / 典藏饰品"
    "appearance_tab": (370, 82, 490, 122),         # 交易行的"典藏外观"标签（点它进列表页）
}

# ===================== 其它常量 =====================
GRID_COLS = 2                     # 商品卡片网格列数
GRID_ROWS = 3                     # 商品卡片网格可见行数
# 价格在卡片内的相对位置（右下角）
PRICE_RECT_RATIO = (0.86, 0.78, 1.00, 0.99)
# 滚动后卡片与固定网格会错位（滚轮步长不是卡片行高的整数倍），价格会偏离上面
# 标定的位置导致读不到。这时用更大的纵向范围重读一次：覆盖卡片 30%~100% 的高度
PRICE_RECT_RATIO_WIDE = (0.86, 0.30, 1.00, 1.00)
# 价格锚点在卡片内的相对纵向位置（取 PRICE_RECT_RATIO 纵向范围的中心）。
# 价格相对卡片的位置是固定的，因此可用"价格实际所在的 y"反推卡片中心，
# 保证读到的价格和点下去的卡片是同一张（错位时也不会张冠李戴）
PRICE_ANCHOR_RATIO_Y = (PRICE_RECT_RATIO[1] + PRICE_RECT_RATIO[3]) / 2

ACTION_WAIT = 0.7                 # 每次点击后等待界面响应的时间（秒）
SEASON_RETRY_LIMIT = 6            # 赛季下拉的选择重试次数
SEASON_SCROLL_LIMIT = 15          # 赛季下拉列表最多向下滚动的次数（目标赛季常在列表最底部）
SEASON_SCROLL_CLICKS = 2          # 每次向下滚动赛季下拉列表的滚轮格数（步长小于可见行数，避免漏掉选项）
TIER_RETRY_LIMIT = 3              # 品阶勾选找不到时的重试次数
GRADE_RETRY_LIMIT = 6             # 成色勾选找不到时的重试次数
BUY_RETRY_LIMIT = 3               # 点"购买"后仍未跳转的容忍次数
LIST_SCROLL_LIMIT = 10            # 左侧列表最多滚动次数
# 判定"这次真的滚动了"所需的最小纵向位移（像素）。OCR 给出的 y 坐标有 ±1~2px 抖动，
# 而真正的滚动会整屏移动约一个行高（实测 152px），用这个下限把两者分开：
# 小于它就当作"没滚动"（列表已到底），否则会出现"754 → 753 差 1 像素"被判成滚动成功、
# 序号归零后把同一屏商品又从头加一遍关注的情况（白跑一整轮准备阶段）
SCROLL_MIN_DELTA_PX = 12
SALE_SCROLL_LIMIT = 10            # 在售页商品网格最多向下滚动的次数
SALE_SCROLL_CLICKS = 3            # 每次向下滚动在售页商品网格的滚轮格数（与左侧列表步长一致）
STAR_GOLD_RATIO = 0.03            # 星标区域"金色像素占比"阈值，超过视为已关注

# 「我的关注」页排序下拉框里要选中的那一项。文字需与游戏里**逐字一致**（按 OCR 匹配后点击）；
# 想让别的排序生效，改这里即可。进入关注页时脚本会先读下拉框当前值，已经是它就跳过
FOLLOW_SORT_TARGET = "按稀有度升序"
# 下拉框当前值必然是这几个排序名之一（默认显示「默认排序」，选过之后显示「按稀有度升序」/
# 「按稀有度降序」等）。用它们当"这个框是不是排序框"的判据：一个都读不到就只记日志不点击 ——
# 估算坐标万一落在别的按钮上（比如「筛选」），也不会误点。
# 注意别只写「排序」：选中后显示的是「按稀有度升序」，里面没有"排序"二字
FOLLOW_SORT_MARKERS = ("排序", "升序", "降序")
# 点开排序下拉后的等待与重试次数。面板有展开动画，dxcam 的帧缓冲也可能慢一拍：
# 实测"点完只等 0.7 秒截一次屏"经常拍到面板还没出现的那一帧（读到的是面板后面的商品名），
# 于是误判成"选项区没找到"、把刚点开的下拉又点回去 —— 看起来就是"点开了但没选中"。
# 这里分几次重新截屏 OCR，每次之间等 FOLLOW_SORT_OPEN_WAIT 秒
FOLLOW_SORT_OPEN_ATTEMPTS = 4
FOLLOW_SORT_OPEN_WAIT = 0.45

# 复选框位于文字左侧约 31 像素处（实测方框 36x36、中心正好落在这个点击点上）
CHECKBOX_DX = 31
# 复选框像素判定：以 CHECKBOX_DX 处的中心取 2*CHECKBOX_HALF 的方框。
# 未勾选是纯深色方框（实测亮像素占比 0.000），已勾选有明显白色对勾（实测 0.27），
# 所以用"亮像素占比"就能区分；注意不能套用金色判据，勾选标记是白色的
CHECKBOX_HALF = 18
CHECKBOX_BRIGHT_GRAY = 110        # 多亮算"亮像素"
CHECKBOX_CHECKED_RATIO = 0.05     # 亮像素占比超过该值即视为已勾选
# 商品名文字在商品项顶部，价格标签在缩略图上；点击点由"典藏"标签向上偏移得到
ITEM_TAG_OFFSET_Y = 50
# 列表滚动时鼠标停留的位置
LIST_SCROLL_POINT = (330, 820)

FOLLOW_COUNT_PATTERN = re.compile(r"我的关注\s*[:：]\s*(\d+)\s*/\s*(\d+)")

MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


def get_region(selector, name: str):
    """取区域坐标：优先用标定文件里的值，没标定则用内置默认值"""
    region = None
    try:
        region = selector.get_region(name)
    except Exception:
        region = None
    if region:
        return tuple(region)
    return DEFAULT_REGIONS[name]


def press_esc():
    """按 ESC 返回上一层"""
    pydirectinput.press("esc")
    time.sleep(ACTION_WAIT)


def scroll_wheel(clicks: int):
    """滚动鼠标滚轮（clicks 为负表示向下）"""
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0,
                                     int(clicks * WHEEL_DELTA), 0)


def read_follow_count(ops, selector, frame):
    """读取"我的关注：N/150"里的 N 和总数

    Returns:
        (已关注数量, 关注上限)；未识别到返回 (None, None)
    """
    region = get_region(selector, "follow_count")
    joined = "".join(text for text, _ in ops.ocr_region_boxes(region, frame))
    match = FOLLOW_COUNT_PATTERN.search(joined)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def detect_page(ops, selector, frame) -> str:
    """根据特征文本判断当前处于哪个页面

    注意判定顺序：截图6/8/9 布局几乎一致，「我的关注：N/150」是区分关注页
    唯一可靠的依据，因此关注页最先判定。
    """
    def joined(name):
        region = get_region(selector, name)
        return "".join(text for text, _ in ops.ocr_region_boxes(region, frame))

    # 我的关注页
    if FOLLOW_COUNT_PATTERN.search(joined("follow_count")):
        return PAGE_FOLLOW
    # 成色筛选侧栏
    anchor = joined("grade_panel_anchor")
    if "重置" in anchor or "确定" in anchor:
        return PAGE_GRADE_PANEL
    # 筛选弹窗
    anchor = joined("dialog_anchor")
    if "品阶" in anchor or "枪种" in anchor:
        return PAGE_FILTER_DIALOG
    # 典藏外观列表页
    if "典藏工坊" in joined("browse_anchor"):
        return PAGE_BROWSE
    # 在售列表页
    if "在售" in joined("sale_tab"):
        return PAGE_SALE
    # 兜底判据：赛季下拉框展开时会遮住「品阶」字样，此时用弹窗底部的「确认」按钮兜底。
    # 放在最后判定，避免误判：在售页/列表页的特征区域优先级更高
    if "确认" in joined("dialog_confirm"):
        return PAGE_FILTER_DIALOG
    # 返航链的两个页面放在最后判定：
    # - 交易行首页和"典藏外观列表页"共用顶部那一排标签，必须靠上面的 browse_anchor
    #   （「典藏工坊」）先把列表页认出来，所以交易行只能排在列表页后面
    # - 大厅的特征文字最"独"（「开始游戏」只在进游戏那一刻出现在顶部），排最后最安全
    tab_bar = joined("market_tab_anchor")
    if "典藏外观" in tab_bar or "曼德尔砖" in tab_bar:
        return PAGE_MARKET
    lobby_anchor = joined("lobby_anchor")
    if any(word in lobby_anchor for word in ("开始游戏", "特战干员", "改枪台")):
        return PAGE_LOBBY
    return PAGE_UNKNOWN


class MarketFlow:
    """准备阶段状态机：筛选 -> 逐个商品加关注 -> 进入关注页"""

    def __init__(self, ops, selector, notify):
        """
        Args:
            ops: 提供 capture / ocr_region / ocr_region_items / click_region /
                 click_point_at / mark_progress 的对象（ScriptThread 自身）
            selector: RegionSelector，用于读取用户标定的区域
            notify: 向界面推送状态文本的回调
        """
        self.ops = ops
        self.selector = selector
        self._notify = notify
        self.browse_filter_done = False   # 赛季+品阶筛选是否已提交（全程只需一次）
        self.reset_prepare_context()

    # ---------------------------------------------------------------- 状态重置
    def reset_prepare_context(self):
        """重置一轮"处理商品列表"的上下文（重新开始筛选流程时调用）"""
        self.list_index = 0            # 下一个要处理的商品序号
        self.list_bottom = False       # 左侧列表是否已滚到底
        self.list_scroll_count = 0     # 已滚动次数
        self.browse_stage = "pick"     # pick=点列表项 / buy=点"购买"
        self.browse_retry = 0
        self.dialog_stage = "season"   # season / tier / confirm
        self.tier_clicked = False
        self.tier_retry = 0
        self.season_stage = "open"     # open=展开下拉 / pick=点选项
        self.season_retry = 0
        self.season_scroll = 0         # 下拉列表已向下滚动的次数
        self.season_last_sig = None    # 上一次滚动后下拉列表的文字签名（判断是否到底）
        self.grade_filter_done = False
        self.grade_stage = "open"      # open=点筛选 / set=勾成色 / ok=点确定
        self.grade_retry = 0
        self.sale_cards_done = set()   # 当前在售页已处理的卡片序号
        self.sale_scroll_count = 0     # 在售页网格已向下滚动的次数
        self.sale_bottom = False       # 在售页网格是否已滚到底

    def _region(self, name):
        return get_region(self.selector, name)

    def _joined(self, name, frame):
        region = self._region(name)
        return "".join(text for text, _ in self.ops.ocr_region_boxes(region, frame))

    def _click_text(self, region_name, keyword, frame, dx=0):
        """在指定区域内 OCR 查找包含关键字的文本并点击其中心"""
        region = self._region(region_name)
        for text, x1, y1, x2, y2 in self.ops.ocr_region_items(region, frame):
            if keyword in text:
                self.ops.click_point_at(((x1 + x2) // 2 + dx, (y1 + y2) // 2))
                return True
        return False

    def _hover_region(self, region_name):
        """把鼠标移到指定区域中心，返回区域四元组

        必须先移动鼠标：mouse_event 的滚轮事件发给光标所在的窗口，
        光标不在目标控件上滚动无效。
        """
        left, top, right, bottom = self._region(region_name)
        pydirectinput.moveTo((left + right) // 2, (top + bottom) // 2)
        time.sleep(0.15)
        return left, top, right, bottom

    def _scroll_region_down(self, region_name, clicks):
        """把鼠标移到指定区域中心后向下滚动滚轮"""
        self._hover_region(region_name)
        scroll_wheel(-clicks)

    # ---------------------------------------------------------------- 分派入口
    def prepare_step(self, page, frame, cfg) -> str:
        """处理一轮准备阶段

        Returns:
            'running'      继续准备阶段
            'to_snipe'     准备完成，可以进入关注页抢购
            'leave_follow' 当前在关注页但列表为空，需要先退出去（由上层执行）
        """
        if page == PAGE_FILTER_DIALOG:
            self._handle_filter_dialog(frame, cfg)
        elif page == PAGE_GRADE_PANEL:
            self._handle_grade_panel(frame, cfg)
        elif page == PAGE_SALE:
            return self._handle_sale(frame, cfg)
        elif page == PAGE_BROWSE:
            return self._handle_browse(frame, cfg)
        elif page == PAGE_LOBBY:
            return self._handle_lobby()
        elif page == PAGE_MARKET:
            return self._handle_market()
        elif page == PAGE_FOLLOW:
            # 关注页：列表非空时（脚本启动时游戏就停在这一页）跳过准备阶段直接抢购。
            # 列表为空时要退出去 —— 说明上一轮的"抢空后退出关注页"还没真正生效，
            # 此时进抢购只会读到空列表、按 ESC、又被判回关注页，无限空转
            joined, _total = read_follow_count(self.ops, self.selector, frame)
            if joined == 0:
                return "leave_follow"
            self.reset_prepare_context()
            return "to_snipe"
        return "running"

    # ------------------------------------------------------ 返航链（大厅/交易行）
    def enter_market(self):
        """点大厅底部图标栏里的「交易行」入口"""
        self.ops.click_region(self._region("lobby_market_btn"))
        self.ops.mark_progress()
        time.sleep(ACTION_WAIT)

    def enter_appearance(self):
        """点交易行的「典藏外观」标签，进入商品列表页"""
        self.ops.click_region(self._region("appearance_tab"))
        # 换了标签页等于重新开一轮扫描：列表序号要归零，赛季/品阶筛选也要重做
        # （重新进列表页后游戏端的筛选状态不会保留）
        self.reset_prepare_context()
        self.browse_filter_done = False
        self.ops.mark_progress()
        time.sleep(ACTION_WAIT)

    def enter_follow_page(self, frame):
        """点列表页右侧的「我的关注」入口，回到关注页继续抢购

        抢购成功后弹出的「获得外观」结果页、以及紧跟的 ESC，很容易把画面带到
        列表页或在售页；这时直接退回准备阶段等于把已经关注好的商品全部作废重来，
        所以要先设法回到关注页。入口文字没识别到时退回按区域中心点，保证点击必发。
        """
        if not self._click_text("my_follow_entry", "我的关注", frame):
            self.ops.click_region(self._region("my_follow_entry"))
        self.ops.mark_progress()
        time.sleep(ACTION_WAIT)

    # ------------------------------------------------------ 关注页排序
    @staticmethod
    def _match_sort_option(items):
        """在排序选项里找目标项，返回其中心坐标；找不到返回 None

        先找文字完全相等的，找不到再退回子串匹配（与选赛季同一策略）
        """
        loose = None
        for text, x1, y1, x2, y2 in items:
            if text.strip() == FOLLOW_SORT_TARGET:
                return (x1 + x2) // 2, (y1 + y2) // 2
            if loose is None and FOLLOW_SORT_TARGET in text:
                loose = ((x1 + x2) // 2, (y1 + y2) // 2)
        return loose

    def ensure_follow_sort(self, frame):
        """确保「我的关注」页的排序是「按稀有度升序」

        Returns:
            True 表示排序已经是目标项、或本次成功切换；False 表示这次没搞定

        步骤：先 OCR 下拉框当前显示的排序名
          - 已经是目标项        -> 直接返回，不点
          - 读到排序名          -> 点开下拉（点**文字自身**的位置），然后分几次
                                  重新截屏 OCR 找目标项并点击，点完再回读标题栏确认
          - 读不到排序名        -> 坐标没标对，只记日志不点击（关注页上误点的代价太大，
                                  可能点到「购买」，宁可这次不生效也不乱点）
        """
        items = self.ops.ocr_region_items(self._region("follow_sort"), frame)
        header = None
        current = ""
        for text, x1, y1, x2, y2 in items:
            if any(marker in text for marker in FOLLOW_SORT_MARKERS):
                current = text
                header = ((x1 + x2) // 2, (y1 + y2) // 2)
                break
        if header is None:
            logger.warning(
                f"[关注页排序] 下拉框区域未识别到排序名（读到: {items!r}），"
                "跳过本次设置；请用 region_selector.py 标定 follow_sort")
            return False
        if FOLLOW_SORT_TARGET in current:
            self._notify(f"✅ 关注页排序已是「{FOLLOW_SORT_TARGET}」")
            return True

        # 点文字自身而不是区域中心：估算坐标偏一点也能点中
        self.ops.click_point_at(header)
        self.ops.mark_progress()

        # 展开动画 + dxcam 帧缓冲都可能慢一拍，一次截图常常拍到"面板还没出现"的画面
        # （实测日志里读到的是面板后面的商品名），所以这里分几次重新截屏 OCR。
        # 注意循环里**不再点标题**：多点一次既可能把已经展开的面板点回去、也可能把没开的
        # 点开，状态不可控。真没点开的话，下一轮（main_gui 最多试 FOLLOW_SORT_TRY_LIMIT 轮）
        # 开头的"点标题"自然会再开一次
        reads = []
        option = None
        for _ in range(FOLLOW_SORT_OPEN_ATTEMPTS):
            time.sleep(FOLLOW_SORT_OPEN_WAIT)
            opened = self.ops.capture()
            if opened is None or opened.size == 0:
                reads.append("截屏失败")
                continue
            option_items = self.ops.ocr_region_items(self._region("follow_sort_options"), opened)
            reads.append([item[0] for item in option_items])
            option = self._match_sort_option(option_items)
            if option:
                break

        if option is None:
            logger.warning(
                f"[关注页排序] 连试 {FOLLOW_SORT_OPEN_ATTEMPTS} 次都没读到"
                f"「{FOLLOW_SORT_TARGET}」（每轮读到: {reads}），本次放弃；"
                "请用 region_selector.py 标定 follow_sort_options")
            return False

        self.ops.click_point_at(option)
        self.ops.mark_progress()

        # 点完回读标题栏确认，避免"看起来点了、其实没生效"被当成成功
        time.sleep(FOLLOW_SORT_OPEN_WAIT)
        after = self.ops.capture()
        now = ""
        if after is not None and after.size:
            for text, *_ in self.ops.ocr_region_items(self._region("follow_sort"), after):
                if any(marker in text for marker in FOLLOW_SORT_MARKERS):
                    now = text
                    break
        if FOLLOW_SORT_TARGET in now:
            self._notify(f"🔽 关注页排序已切换为「{FOLLOW_SORT_TARGET}」")
            return True
        logger.warning(
            f"[关注页排序] 点了「{FOLLOW_SORT_TARGET}」但标题栏回读是 {now!r}，可能没生效")
        self._notify(f"⚠️ 关注页排序可能没生效（标题栏回读：{now or '空'}）")
        return False

    def _handle_lobby(self):
        """停在大厅：点底部图标栏的「交易行」入口返回

        防卡死恢复连着按 ESC 会一路退回大厅，而大厅里再按 ESC 会弹出暂停菜单
        （越按越乱），所以这里必须主动点回交易行，而不是继续"返回上一层"。
        """
        self._notify("🏛 检测到游戏大厅，点底部「交易行」入口返回")
        self.enter_market()
        return "running"

    def _handle_market(self):
        """停在交易行首页：点「典藏外观」标签进入商品列表页"""
        self._notify("🖼 交易行 → 点「典藏外观」进入商品列表")
        self.enter_appearance()
        return "running"

    # ---------------------------------------------------------- 典藏外观列表页
    def _handle_browse(self, frame, cfg) -> str:
        # 1) 先做"赛季 + 品阶"筛选，只做一次
        if not self.browse_filter_done:
            if self._click_text("filter_btn", "筛选", frame):
                self._notify("🔍 打开筛选对话框")
                self.ops.mark_progress()
                time.sleep(ACTION_WAIT)
            return "running"

        # 2) 逐个商品：点列表项 -> 点"购买"
        items = self._read_list_items(frame)
        if self.list_index >= len(items):
            if not self.list_bottom:
                self._scroll_list_down()
                return "running"
            # 3) 列表处理完，进入"我的关注"
            if self._click_text("my_follow_entry", "我的关注", frame):
                self._notify("⭐ 进入「我的关注」，开始抢购阶段")
                self.ops.mark_progress()
                time.sleep(ACTION_WAIT)
                self.reset_prepare_context()
                return "to_snipe"
            return "running"

        if self.browse_stage == "pick":
            _, point = items[self.list_index]
            self.ops.click_point_at(point)
            self._notify(f"▶️ 选择商品 {self.list_index + 1}/{len(items)}")
            self.browse_stage = "buy"
            self.browse_retry = 0
            self.ops.mark_progress()
            time.sleep(ACTION_WAIT)
            return "running"

        # browse_stage == "buy"：点右侧"购买"进入在售页
        if self._click_text("detail_buy", "购买", frame):
            self.browse_retry += 1
            self._notify(f"🛒 点击购买（第 {self.browse_retry} 次）")
            self.ops.mark_progress()
            time.sleep(ACTION_WAIT)
            if self.browse_retry > BUY_RETRY_LIMIT:
                self._notify("⚠️ 购买按钮无响应，跳过该商品")
                self._advance_item()
        return "running"

    def _advance_item(self):
        """跳过当前商品，准备处理下一个"""
        self.list_index += 1
        self.browse_stage = "pick"
        self.browse_retry = 0
        self.reset_sale_context()

    def reset_sale_context(self):
        """重置当前在售页的上下文（成色筛选 + 已处理卡片 + 网格滚动进度）"""
        self.grade_filter_done = False
        self.grade_stage = "open"
        self.grade_retry = 0
        self.sale_cards_done = set()
        self.sale_scroll_count = 0
        self.sale_bottom = False

    def _list_tag_positions(self, frame=None):
        """读取左侧列表里每个商品项「典藏」标签的纵向中心，按顺序去重后返回

        用标签坐标而不是整块区域的文本做判断依据：列表没滚动时坐标必然完全
        一致，而 OCR 文本抖动、鼠标移入列表产生的悬停高亮都会让文本签名发生
        假变化 —— 那会把"其实没滚动"误判成"滚动了"，导致反复回到第一个商品。
        """
        if frame is None:
            frame = self.ops.capture()
        if frame is None or frame.size == 0:
            return []
        region = self._region("goods_list")
        tags = []
        for text, x1, y1, x2, y2 in self.ops.ocr_region_items(region, frame):
            if "典藏" in text and len(text) <= 3:
                tags.append((y1 + y2) // 2)
        tags.sort()

        # 同一个商品项可能被识别成多个框，按纵向间距去重
        merged = []
        for y in tags:
            if not merged or y - merged[-1] > 60:
                merged.append(y)
        return merged

    def _read_list_items(self, frame):
        """读取左侧商品列表，返回 [(名称, 点击坐标), ...]

        以每个商品项上的"典藏"标签作为锚点（它比商品名更稳定，
        商品名偶尔会 OCR 失败），点击点取标签上方约 50 像素处（缩略图中心）。
        """
        left, top, right, bottom = self._region("goods_list")
        center_x = (left + right) // 2
        return [(f"商品{i + 1}", (center_x, y - ITEM_TAG_OFFSET_Y))
                for i, y in enumerate(self._list_tag_positions(frame))]

    @staticmethod
    def _scroll_stalled(before, after):
        """判断这次滚动是否"等于没动"（含 OCR 坐标抖动）

        不能直接用 `before == after`：OCR 的 y 坐标有 ±1~2px 抖动，实测出现过
        754 → 753 这种只差 1 像素却被当成"滚动成功"的情况，于是序号被归零、
        同一屏商品又被从头加了一遍关注。真正的滚动会整屏移动约一个行高，
        所以用 SCROLL_MIN_DELTA_PX 作为下限。项数变化时按"动过了"处理。
        """
        if len(before) != len(after):
            return False
        if not before:
            return True
        return max(abs(a - b) for a, b in zip(before, after)) < SCROLL_MIN_DELTA_PX

    def _scroll_list_down(self):
        """向下滚动左侧列表；返回是否还有新内容"""
        # 先把鼠标停到列表上再截基准图：鼠标从别处移进来会触发悬停高亮 /
        # 提示文字，这些内容会混进 OCR 结果，让"没滚动"看起来像"滚动了"
        pydirectinput.moveTo(*LIST_SCROLL_POINT)
        time.sleep(0.15)
        before = self._list_tag_positions()
        scroll_wheel(-3)
        time.sleep(0.6)
        after = self._list_tag_positions()

        self.list_scroll_count += 1
        logger.info(f"[列表滚动] 商品项位置 {before} -> {after}")
        # 标签位置没变（含 OCR 抖动的"看起来变了 1 像素"）= 列表已经滚不动了
        if self._scroll_stalled(before, after) or self.list_scroll_count > LIST_SCROLL_LIMIT:
            self.list_bottom = True
            self._notify("📃 左侧列表已到底")
            return False
        # 视图整体上移，序号需要跟着归零；已关注过的商品会被星标判定自动跳过
        self.list_index = 0
        self.browse_stage = "pick"
        self._notify(f"⬇️ 列表向下滚动（第 {self.list_scroll_count} 次）")
        self.ops.mark_progress()
        return True

    # -------------------------------------------------------------- 筛选弹窗
    def _handle_filter_dialog(self, frame, cfg):
        if self.dialog_stage == "season":
            self._select_season(frame, cfg)
        elif self.dialog_stage == "tier":
            self._select_tier(frame, cfg)
        else:
            self._confirm_dialog(frame)

    def _select_season(self, frame, cfg):
        target = cfg["target_season"]
        current = self._joined("season_combo", frame)
        # 判断"是否已经选中"时不能只用 `target in current`：名单里同时有「棱镜攻势」
        # 和「棱镜攻势S2」、「电玩高手」和「电玩高手S2」，当前选着 S2 时会被误判成
        # "已经选好棱镜攻势"而跳过筛选，结果抢错赛季。这里额外要求赛季名后面不能再
        # 紧跟字母/数字（即不可能是 S2/S3 这类后缀）才算已选中
        idx = current.find(target)
        suffix = current[idx + len(target):] if idx >= 0 else ""
        if idx >= 0 and not re.match(r"[A-Za-z0-9]", suffix):
            self._notify(f"✅ 赛季已选中：{target}")
            self.dialog_stage = "tier"
            return
        if self.season_retry >= SEASON_RETRY_LIMIT:
            self._notify(f"⚠️ 连续 {SEASON_RETRY_LIMIT} 次未选中赛季「{target}」，继续后续步骤")
            self.dialog_stage = "tier"
            return

        if self.season_stage == "open":
            self.ops.click_region(self._region("season_combo"))
            self.season_stage = "pick"
            self.season_retry += 1
            # 每次重新展开都会回到列表顶部，滚动进度需要一起归零
            self.season_scroll = 0
            self.season_last_sig = None
            self.ops.mark_progress()
            time.sleep(ACTION_WAIT)
            return

        # 下拉已展开：先在当前可见的选项里找目标赛季
        fresh = self.ops.capture()
        if fresh is None or not fresh.size:
            fresh = frame

        items = self.ops.ocr_region_items(self._region("season_options"), fresh)

        # 先找"文字完全相等"的那一项，找不到再退回子串匹配。
        # 赛季名单里有「棱镜攻势」和「棱镜攻势S2」、「电玩高手」和「电玩高手S2」这类
        # 同名前缀的项，直接用 `target in text` 会先撞上 S2，选错赛季
        exact = None
        loose = None
        for text, x1, y1, x2, y2 in items:
            if text.strip() == target:
                exact = (x1, y1, x2, y2)
                break
            if loose is None and target in text:
                loose = (x1, y1, x2, y2)
        hit = exact or loose
        if hit:
            x1, y1, x2, y2 = hit
            self.ops.click_point_at(((x1 + x2) // 2, (y1 + y2) // 2))
            self._notify(f"🔽 选择赛季：{target}")
            self.season_stage = "open"
            self.ops.mark_progress()
            time.sleep(ACTION_WAIT)
            return

        if not items:
            # 一个选项都没读到：多半是下拉框没真正展开，重新点一次。
            # 这里不能去滚动，否则滚轮会落到下层的列表/页面上
            self._notify("⚠️ 未读到赛季选项，重新展开下拉框")
            self.season_stage = "open"
            time.sleep(ACTION_WAIT)
            return

        # 没找到：赛季列表很长时需要向下滚动才能看到靠后的赛季。
        # 向下滚到底（文字签名不再变化）说明列表已经翻完，目标赛季不存在，放弃筛选
        signature = "|".join(text for text, *_ in items)
        if signature == self.season_last_sig or self.season_scroll >= SEASON_SCROLL_LIMIT:
            self._notify(f"⚠️ 赛季列表已翻到底，仍未找到「{target}」，跳过赛季筛选")
            self.dialog_stage = "tier"
            self.season_stage = "open"
            return

        self.season_last_sig = signature
        self.season_scroll += 1
        self._notify(f"⬇️ 赛季列表向下滚动（第 {self.season_scroll} 次）")
        self._scroll_region_down("season_options", SEASON_SCROLL_CLICKS)
        self.ops.mark_progress()
        time.sleep(ACTION_WAIT)

    def _checkbox_box(self, label):
        """由选项文字框推出左侧复选框的像素判定方框"""
        _, x1, y1, x2, y2 = label
        cx = x1 - CHECKBOX_DX
        cy = (y1 + y2) // 2
        return (cx - CHECKBOX_HALF, cy - CHECKBOX_HALF,
                cx + CHECKBOX_HALF, cy + CHECKBOX_HALF)

    def _is_checkbox_checked(self, frame, label):
        """判断该复选框是否已勾选

        品阶和成色用的是同一套复选框：未勾选是纯深色方框，
        勾选后会有明显的白色对勾，用"亮像素占比"区分。
        """
        left, top, right, bottom = self._checkbox_box(label)
        if left < 0 or top < 0:
            return False
        roi = frame[top:bottom, left:right]
        if roi.size == 0:
            return False
        gray = roi.astype(np.int32).mean(axis=2)
        return bool((gray > CHECKBOX_BRIGHT_GRAY).mean() > CHECKBOX_CHECKED_RATIO)

    def _select_tier(self, frame, cfg):
        target = cfg["target_tier"]
        if self.tier_clicked:
            self.dialog_stage = "confirm"
            return

        keyword = target.replace("品阶", "")
        region = self._region("tier_row")
        label = None
        for text, x1, y1, x2, y2 in self.ops.ocr_region_items(region, frame):
            if keyword and keyword in text:
                label = (text, x1, y1, x2, y2)
                break

        if label is None:
            self.tier_retry += 1
            if self.tier_retry >= TIER_RETRY_LIMIT:
                self._notify(f"⚠️ 未找到品阶「{target}」，跳过勾选")
                self.dialog_stage = "confirm"
            return

        # 已经是勾选状态时再点一次会把它取消掉，必须跳过
        if self._is_checkbox_checked(frame, label):
            self._notify(f"☑️ 品阶「{target}」已是选中状态，无需重复勾选")
            self.tier_clicked = True
            self.dialog_stage = "confirm"
            return

        _, x1, y1, x2, y2 = label
        self.ops.click_point_at((x1 - CHECKBOX_DX, (y1 + y2) // 2))
        time.sleep(ACTION_WAIT)

        # 点完立即复核：没勾上就再试，避免点空或被其他窗口吃掉了点击
        check = self.ops.capture()
        if check is not None and check.size and self._is_checkbox_checked(check, label):
            self._notify(f"☑️ 勾选品阶：{target}")
            self.tier_clicked = True
            self.dialog_stage = "confirm"
            self.ops.mark_progress()
            return

        self.tier_retry += 1
        self._notify(f"⚠️ 品阶「{target}」勾选未生效（第 {self.tier_retry} 次）")
        if self.tier_retry >= TIER_RETRY_LIMIT:
            self._notify(f"⚠️ 品阶「{target}」仍未被勾选，跳过")
            self.dialog_stage = "confirm"

    def _confirm_dialog(self, frame):
        if self._click_text("dialog_confirm", "确认", frame):
            self._notify("✅ 赛季/品阶筛选已提交")
            self.browse_filter_done = True
            self.dialog_stage = "season"
            self.season_stage = "open"
            self.season_retry = 0
            self.tier_clicked = False
            self.tier_retry = 0
            self.ops.mark_progress()
            time.sleep(ACTION_WAIT)

    # ------------------------------------------------------------ 成色筛选侧栏
    def _handle_grade_panel(self, frame, cfg):
        if self.grade_stage == "set":
            self._select_grade(frame, cfg)
        elif self.grade_stage == "ok":
            if self._click_text("grade_ok", "确定", frame):
                self._notify("✅ 成色筛选已提交")
                self.grade_filter_done = True
                self.grade_retry = 0
                self.ops.mark_progress()
                time.sleep(ACTION_WAIT)

    def _select_grade(self, frame, cfg):
        target = str(cfg["target_grade"]).strip()
        region = self._region("grade_options")
        label = None
        for text, x1, y1, x2, y2 in self.ops.ocr_region_items(region, frame):
            value = text.strip()
            matched = (value == target) or (
                len(target) > 1 and target in value)
            if matched:
                label = (text, x1, y1, x2, y2)
                break

        if label is None:
            self.grade_retry += 1
            if self.grade_retry >= GRADE_RETRY_LIMIT:
                self._notify(f"⚠️ 未找到成色「{target}」，跳过勾选")
                self.grade_stage = "ok"
            return

        # 已经是勾选状态时再点一次会把它取消掉，必须跳过
        if self._is_checkbox_checked(frame, label):
            self._notify(f"☑️ 成色「{target}」已是选中状态，无需重复勾选")
            self.grade_stage = "ok"
            self.ops.mark_progress()
            return

        _, x1, y1, x2, y2 = label
        self.ops.click_point_at((x1 - CHECKBOX_DX, (y1 + y2) // 2))
        time.sleep(ACTION_WAIT)

        # 点完立即复核：没勾上就再试，避免点空或被其他窗口吃掉了点击
        check = self.ops.capture()
        if check is not None and check.size and self._is_checkbox_checked(check, label):
            self._notify(f"☑️ 勾选成色：{target}")
            self.grade_stage = "ok"
            self.ops.mark_progress()
            return

        self.grade_retry += 1
        self._notify(f"⚠️ 成色「{target}」勾选未生效（第 {self.grade_retry} 次）")
        if self.grade_retry >= GRADE_RETRY_LIMIT:
            self._notify(f"⚠️ 成色「{target}」仍未被勾选，跳过")
            self.grade_stage = "ok"

    # ------------------------------------------------------------- 在售列表页
    def _handle_sale(self, frame, cfg) -> str:
        # 1) 先按配置的成色筛选
        if not self.grade_filter_done:
            if self.grade_stage == "open":
                if self._click_text("filter_btn", "筛选", frame):
                    self._notify("🔍 打开成色筛选")
                    self.grade_stage = "set"
                    self.ops.mark_progress()
                    time.sleep(ACTION_WAIT)
            return "running"

        # 2) 逐张卡片：读价格 -> 达标则加关注
        cards = self._grid_cards()
        limit = cfg["price_limit"]
        pending = [i for i in range(len(cards)) if i not in self.sale_cards_done]
        if not pending:
            # 本屏的 6 张卡片处理完：先往下滚看还有没有更多在售商品，滚到底才返回列表页
            if not self.sale_bottom:
                self._scroll_sale_down()
                return "running"
            # 整个在售列表已翻完，返回列表页处理下一个商品
            self._notify("↩️ 在售列表已翻完，返回商品列表")
            self._advance_item()
            press_esc()
            self.ops.mark_progress()
            return "running"

        index = pending[0]
        self.sale_cards_done.add(index)
        card = cards[index]
        price, price_y = self._read_price(frame, card)
        if price is None:
            self._notify(f"⚠️ 第 {index + 1} 张卡片价格识别失败，跳过")
            return "running"
        if price > limit:
            # 在售列表按价格升序，这张都超上限说明后面只会更贵：
            # 不再扫本页剩下的卡片（也不再向下滚动），直接返回列表页处理下一个商品
            self._notify(f"⏭️ 第 {index + 1} 张卡片价格 {price} 超过上限 {limit}，返回列表页")
            self._advance_item()
            press_esc()
            self.ops.mark_progress()
            return "running"

        # 卡片中心由"价格实际所在的纵向位置"反推，而不是直接用固定网格的格子中心：
        # 滚动后卡片与网格错位时，格子中心可能落在两张卡的交界处，会造成
        # "读到的是上一张卡的价格、点下去的却是下一张卡"
        card_h = card[3] - card[1]
        center_y = int(price_y - card_h * (PRICE_ANCHOR_RATIO_Y - 0.5))
        if center_y < self._region("goods_grid")[1]:
            # 卡片已被滚到网格上方（只剩最下面一点），上一屏已经处理过，跳过
            return "running"
        offset = center_y - (card[1] + card[3]) // 2
        if abs(offset) > card_h * 0.1:
            logger.info(f"[在售页] 第 {index + 1} 张卡片与网格错位 {offset}px，已按价格位置校正")

        # 选中卡片，再看星标是否已经点亮（避免重复点击反而取消关注）
        self.ops.click_point_at(((card[0] + card[2]) // 2, center_y))
        time.sleep(0.4)
        fresh = self.ops.capture()
        if fresh is not None and fresh.size and self._is_star_followed(fresh):
            self._notify(f"⭐ 第 {index + 1} 张卡片（价格 {price}）已在关注列表")
            self.ops.mark_progress()
            return "running"

        self.ops.click_region(self._region("follow_star"))
        self._notify(f"⭐ 已关注第 {index + 1} 张卡片（价格 {price}）")
        self.ops.mark_progress()
        time.sleep(ACTION_WAIT)
        return "running"

    def _grid_signature(self):
        """采集在售页商品网格的文本特征，用于判断滚动是否还有新内容"""
        frame = self.ops.capture()
        if frame is None or frame.size == 0:
            return ""
        return self._joined("goods_grid", frame)

    def _scroll_sale_down(self):
        """向下滚动在售页商品网格；滚到底时置 sale_bottom

        滚动后可见的 6 张卡片全换了，sale_cards_done 必须清空重新扫一遍；
        已经关注过的卡片会被星标判定自动跳过，不会重复加关注。
        """
        # 同 _scroll_list_down：先把鼠标停到网格上再取基准，避免悬停高亮造成假变化
        self._hover_region("goods_grid")
        before = self._grid_signature()
        scroll_wheel(-SALE_SCROLL_CLICKS)
        time.sleep(0.6)
        after = self._grid_signature()

        self.sale_scroll_count += 1
        if before == after or self.sale_scroll_count > SALE_SCROLL_LIMIT:
            self.sale_bottom = True
            self._notify("📃 在售列表已到底")
            return False
        self.sale_cards_done.clear()
        self._notify(f"⬇️ 在售列表向下滚动（第 {self.sale_scroll_count} 次）")
        self.ops.mark_progress()
        return True

    def _grid_cards(self):
        """按网格区域等分出每张商品卡片的矩形"""
        left, top, right, bottom = self._region("goods_grid")
        cell_w = (right - left) / GRID_COLS
        cell_h = (bottom - top) / GRID_ROWS
        cards = []
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                x1 = int(left + col * cell_w)
                y1 = int(top + row * cell_h)
                cards.append((x1, y1, int(x1 + cell_w), int(y1 + cell_h)))
        return cards

    def _price_region(self, card, ratio):
        """按相对比例从卡片矩形里切出价格采样区域"""
        left, top, right, bottom = card
        width, height = right - left, bottom - top
        return (
            int(left + width * ratio[0]),
            int(top + height * ratio[1]),
            int(left + width * ratio[2]),
            int(top + height * ratio[3]),
        )

    def _read_price(self, frame, card):
        """读取卡片里面的价格数字

        先按标定好的位置读（区域小、最快最准）；读不到说明滚动后卡片和固定网格
        错位了，价格不在标定位置上，再用纵向加大的范围重读一次。

        Returns:
            (价格, 价格中心的纵向坐标)；识别不到返回 (None, None)
        """
        for ratio in (PRICE_RECT_RATIO, PRICE_RECT_RATIO_WIDE):
            region = self._price_region(card, ratio)
            boxes = self.ops.ocr_region_boxes(region, frame)
            digits = "".join(re.findall(r"\d", "".join(text for text, _ in boxes)))
            if not digits:
                continue
            value = int(digits)
            # 明显不合理的数字直接丢弃，避免误关注
            if not 1 <= value <= 9_999_999:
                continue
            ys = [cy for text, (_, cy) in boxes if re.search(r"\d", text)]
            price_y = sum(ys) / len(ys) if ys else (region[1] + region[3]) / 2
            return value, price_y
        return None, None

    def _is_star_followed(self, frame):
        """通过星标区域的金色像素占比判断该商品是否已关注"""
        x1, y1, x2, y2 = self._region("follow_star")
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return False
        roi = roi.astype(np.int32)
        blue, red = roi[:, :, 0], roi[:, :, 2]
        gold = (red > 150) & ((red - blue) > 60)
        return bool(gold.mean() > STAR_GOLD_RATIO)
