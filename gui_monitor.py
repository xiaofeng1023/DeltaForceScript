# -*- coding: utf-8 -*-
# @Author: BugNotFound
# @Date: 2025-10-04
# @Description: PyQt6 GUI 监控窗口

import logging
from datetime import datetime

from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QGroupBox, QTextEdit,
                             QSpinBox, QDoubleSpinBox, QCheckBox, QDialog, QComboBox,
                             QLineEdit)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QImage, QPixmap

logger = logging.getLogger(__name__)

# 目标赛季可选列表。文字必须与游戏「筛选 → 赛季」下拉里的显示**逐字一致**，
# 脚本是按 OCR 到的文字去匹配并点击的；下拉框仍是可编辑的，名单里没有的赛季
# 可以直接手动输入。默认选中最后一项。
SEASON_OPTIONS = (
    "棱镜攻势", "美杜莎", "电玩高手", "命运王牌", "气象感应",
    "棱镜攻势S2", "悬赏令", "电玩高手S2", "造物纪元", "群星",
    "疾光魅影", "王者之剑",
)


class ScriptController(QObject):
    """脚本控制信号"""
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    preview_requested = pyqtSignal()


class MonitorWindow(QMainWindow):
    """PyQt6 监控窗口"""
    
    def __init__(self):
        super().__init__()
        self.controller = ScriptController()
        
        # 状态变量
        self.is_running = False
        self.is_paused = False
        self.minutes = "--"
        self.seconds = "--"
        self.status = "就绪"
        
        # 配置变量
        # 识别到倒计时到点后、点第一下「购买」前的等待（秒）。抢购窗口只有 1 秒，
        # 这段等待会直接变成比别人慢的时间，默认 0.30 秒留一点缓冲，想更快可调小（见 使用说明.md）
        self.buy_click_delay = 0.30  # 购买点击延迟（秒）
        self.buy_to_verify_delay = 0.0  # 购买到确认的延迟（秒）
        self.buy_interval = 0.30  # 购买按钮点击间隔（秒）
        self.buy_clicks = 2  # 购买按钮连续点击次数
        self.verify_interval = 0.07  # 确认按钮点击间隔（秒）
        self.verify_clicks = 25  # 确认按钮连续点击次数
        self.ocr_interval = 0.95  # OCR识别间隔（time >= 5）（秒）
        self.price_limit = 230  # 价格上限（三角币），超过则本轮不购买
        self.click_refresh_enabled = True  # 倒计时剩余5秒时点击刷新（阈值见 main_gui.REFRESH_TRIGGER_SECONDS）
        # 仅在我的关注页抢购：跳过整个准备阶段（不筛选、不自动加关注），
        # 只抢自己关注列表里已有的商品；该模式下不检查价格上限
        self.follow_only = False
        self.target_season = SEASON_OPTIONS[-1]  # 要抢购的赛季（筛选弹窗里选择），默认最后一项
        self.target_tier = "史诗品阶"          # 要抢购的品阶（四选一）
        self.target_grade = "A"               # 要抢购的成色（所有成色/S/A/B/C 五选一）
        self.stop_after_count = 0             # 抢到 N 个后停止，0 表示不限
        # 市场每日关闭时段（HH:MM），处于该时段内脚本会自行停止
        # 默认值与 main_gui.MARKET_CLOSE_TIME / MARKET_OPEN_TIME 保持一致
        self.market_close_time = "05:00"      # 市场关闭时间
        self.market_open_time = "08:00"       # 市场开市时间（需晚于关闭时间）
        
        self.init_ui()
        
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("Delta Force 脚本监控")
        self.setGeometry(100, 100, 350, 780)
        
        # 设置窗口始终置顶
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        
        # 中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout()
        # 窗口是置顶的，实际高度由 Qt 按各控件的 minimumSizeHint 撑开，setGeometry 里的
        # 780 只是建议值。默认的间距/边距会把窗口顶到 840+ 像素，底部压到游戏结果页的
        # 「跳过」区域（详见 main_gui.WINDOW_TOP_RATIO 的注释），这里收窄到最小
        main_layout.setSpacing(3)
        main_layout.setContentsMargins(3, 3, 3, 3)
        central_widget.setLayout(main_layout)
        
        # ========== 标题区域 ===========
        title_label = QLabel("🎮 Delta Force 自动购买脚本")
        title_font = QFont("微软雅黑", 16, QFont.Weight.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("color: #4CAF50; padding: 10px;")
        main_layout.addWidget(title_label)

        # 小字提示
        tip_label = QLabel("窗口已停在屏幕右侧，请勿拖到画面左侧")
        tip_font = QFont("微软雅黑", 9)
        tip_label.setFont(tip_font)
        tip_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip_label.setStyleSheet("color: #888888; padding-bottom: 6px;")
        main_layout.addWidget(tip_label)
        
        # ========== 状态信息组 ==========
        status_group = QGroupBox("运行状态")
        status_group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #2196F3;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        status_layout = QVBoxLayout()
        status_group.setLayout(status_layout)
        
        # 状态标签
        self.status_label = QLabel("状态: 就绪")
        self.status_label.setFont(QFont("微软雅黑", 12))
        self.status_label.setStyleSheet("color: #FF9800; padding: 5px;")
        status_layout.addWidget(self.status_label)
        
        main_layout.addWidget(status_group)
        
        # ========== 倒计时显示组 ===========
        timer_group = QGroupBox("倒计时")
        timer_group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #9C27B0;
                border-radius: 5px;
                margin-top: 0px;
                padding-top: 10px;
            }
        """)
        timer_layout = QVBoxLayout()
        timer_group.setLayout(timer_layout)

        # 缩小字体和间距的倒计时
        self.timer_label = QLabel("--分--秒")
        timer_font = QFont("微软雅黑", 24, QFont.Weight.Bold)
        self.timer_label.setFont(timer_font)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer_label.setStyleSheet("color: #00BCD4; padding: 10px;")
        timer_layout.addWidget(self.timer_label)

        main_layout.addWidget(timer_group)
        
        # ========== 脚本配置组 ==========
        config_group = QGroupBox("脚本配置")
        config_group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #FF5722;
                border-radius: 5px;
                margin-top: 0px;
                padding-top: 10px;
            }
        """)
        config_layout = QVBoxLayout()
        config_group.setLayout(config_layout)
        
        # 购买点击延迟设置
        delay_layout = QHBoxLayout()
        delay_label = QLabel("购买点击延迟:")
        delay_label.setFont(QFont("微软雅黑", 10))
        delay_label.setFixedWidth(120)
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.0, 5.0)
        self.delay_spin.setValue(self.buy_click_delay)
        self.delay_spin.setSingleStep(0.1)
        self.delay_spin.setDecimals(2)
        self.delay_spin.setSuffix(" 秒")
        self.delay_spin.setFont(QFont("微软雅黑", 10))
        self.delay_spin.valueChanged.connect(self.on_delay_changed)
        delay_layout.addWidget(delay_label)
        delay_layout.addWidget(self.delay_spin)
        delay_layout.addStretch()
        config_layout.addLayout(delay_layout)
        
        #购买到确认延迟设置
        buy_to_verify_layout = QHBoxLayout()
        buy_to_verify_label = QLabel("确认点击延迟:")
        buy_to_verify_label.setFont(QFont("微软雅黑", 10))
        buy_to_verify_label.setFixedWidth(120)
        self.buy_to_verify_spin = QDoubleSpinBox()
        self.buy_to_verify_spin.setRange(0.0, 5.0)
        self.buy_to_verify_spin.setValue(self.buy_to_verify_delay)
        self.buy_to_verify_spin.setSingleStep(0.1)
        self.buy_to_verify_spin.setDecimals(2)
        self.buy_to_verify_spin.setSuffix(" 秒")
        self.buy_to_verify_spin.setFont(QFont("微软雅黑", 10))
        self.buy_to_verify_spin.valueChanged.connect(self.on_buy_to_verify_delay_changed)
        buy_to_verify_layout.addWidget(buy_to_verify_label)
        buy_to_verify_layout.addWidget(self.buy_to_verify_spin)
        buy_to_verify_layout.addStretch()
        config_layout.addLayout(buy_to_verify_layout)
        
        # 购买按钮点击间隔
        buy_interval_layout = QHBoxLayout()
        buy_interval_label = QLabel("购买点击间隔:")
        buy_interval_label.setFont(QFont("微软雅黑", 10))
        buy_interval_label.setFixedWidth(120)
        self.buy_interval_spin = QDoubleSpinBox()
        self.buy_interval_spin.setRange(0.00, 1.0)
        self.buy_interval_spin.setValue(self.buy_interval)
        self.buy_interval_spin.setSingleStep(0.01)
        self.buy_interval_spin.setDecimals(2)
        self.buy_interval_spin.setSuffix(" 秒")
        self.buy_interval_spin.setFont(QFont("微软雅黑", 10))
        self.buy_interval_spin.valueChanged.connect(self.on_buy_interval_changed)
        buy_interval_layout.addWidget(buy_interval_label)
        buy_interval_layout.addWidget(self.buy_interval_spin)
        buy_interval_layout.addStretch()
        config_layout.addLayout(buy_interval_layout)
        
        # 确认按钮点击间隔
        verify_interval_layout = QHBoxLayout()
        verify_interval_label = QLabel("确认点击间隔:")
        verify_interval_label.setFont(QFont("微软雅黑", 10))
        verify_interval_label.setFixedWidth(120)
        self.verify_interval_spin = QDoubleSpinBox()
        self.verify_interval_spin.setRange(0.00, 1.0)
        self.verify_interval_spin.setValue(self.verify_interval)
        self.verify_interval_spin.setSingleStep(0.01)
        self.verify_interval_spin.setDecimals(2)
        self.verify_interval_spin.setSuffix(" 秒")
        self.verify_interval_spin.setFont(QFont("微软雅黑", 10))
        self.verify_interval_spin.valueChanged.connect(self.on_verify_interval_changed)
        verify_interval_layout.addWidget(verify_interval_label)
        verify_interval_layout.addWidget(self.verify_interval_spin)
        verify_interval_layout.addStretch()
        config_layout.addLayout(verify_interval_layout)
        
        # 购买连续点击次数
        buy_clicks_layout = QHBoxLayout()
        buy_clicks_label = QLabel("购买点击次数:")
        buy_clicks_label.setFont(QFont("微软雅黑", 10))
        buy_clicks_label.setFixedWidth(120)
        self.buy_clicks_spin = QSpinBox()
        self.buy_clicks_spin.setRange(1, 30)
        self.buy_clicks_spin.setValue(self.buy_clicks)
        self.buy_clicks_spin.setSuffix(" 次")
        self.buy_clicks_spin.setFont(QFont("微软雅黑", 10))
        self.buy_clicks_spin.valueChanged.connect(self.on_buy_clicks_changed)
        buy_clicks_layout.addWidget(buy_clicks_label)
        buy_clicks_layout.addWidget(self.buy_clicks_spin)
        buy_clicks_layout.addStretch()
        config_layout.addLayout(buy_clicks_layout)
        
        # 确认连续点击次数
        verify_clicks_layout = QHBoxLayout()
        verify_clicks_label = QLabel("确认点击次数:")
        verify_clicks_label.setFont(QFont("微软雅黑", 10))
        verify_clicks_label.setFixedWidth(120)
        self.verify_clicks_spin = QSpinBox()
        self.verify_clicks_spin.setRange(1, 30)
        self.verify_clicks_spin.setValue(self.verify_clicks)
        self.verify_clicks_spin.setSuffix(" 次")
        self.verify_clicks_spin.setFont(QFont("微软雅黑", 10))
        self.verify_clicks_spin.valueChanged.connect(self.on_verify_clicks_changed)
        verify_clicks_layout.addWidget(verify_clicks_label)
        verify_clicks_layout.addWidget(self.verify_clicks_spin)
        verify_clicks_layout.addStretch()
        config_layout.addLayout(verify_clicks_layout)
        
        # OCR识别间隔
        ocr_interval_layout = QHBoxLayout()
        ocr_interval_label = QLabel("OCR间隔(t>5s):")
        ocr_interval_label.setFont(QFont("微软雅黑", 10))
        ocr_interval_label.setFixedWidth(120)
        self.ocr_interval_spin = QDoubleSpinBox()
        self.ocr_interval_spin.setRange(0.01, 1.0)
        self.ocr_interval_spin.setValue(self.ocr_interval)
        self.ocr_interval_spin.setSingleStep(0.01)
        self.ocr_interval_spin.setDecimals(2)
        self.ocr_interval_spin.setSuffix(" 秒")
        self.ocr_interval_spin.setFont(QFont("微软雅黑", 10))
        self.ocr_interval_spin.valueChanged.connect(self.on_ocr_interval_changed)
        ocr_interval_layout.addWidget(ocr_interval_label)
        ocr_interval_layout.addWidget(self.ocr_interval_spin)
        ocr_interval_layout.addStretch()
        config_layout.addLayout(ocr_interval_layout)
        
        # 价格上限
        price_limit_layout = QHBoxLayout()
        price_limit_label = QLabel("价格上限:")
        price_limit_label.setFont(QFont("微软雅黑", 10))
        price_limit_label.setFixedWidth(120)
        self.price_limit_spin = QSpinBox()
        self.price_limit_spin.setRange(0, 999999)
        self.price_limit_spin.setValue(self.price_limit)
        self.price_limit_spin.setSingleStep(10)
        self.price_limit_spin.setSuffix(" 三角币")
        self.price_limit_spin.setFont(QFont("微软雅黑", 10))
        self.price_limit_spin.valueChanged.connect(self.on_price_limit_changed)
        price_limit_layout.addWidget(price_limit_label)
        price_limit_layout.addWidget(self.price_limit_spin)
        price_limit_layout.addStretch()
        config_layout.addLayout(price_limit_layout)
        
        # 目标赛季（可编辑：赛季名称随版本更新，允许手动输入）
        # 选项列表是弹出浮层，展开时覆盖在别的窗口上，不占主窗口高度，
        # 因此赛季多几个也不会把窗口撑高（详见 main_gui.WINDOW_TOP_RATIO 的高度约束）
        season_layout = QHBoxLayout()
        season_label = QLabel("目标赛季:")
        season_label.setFont(QFont("微软雅黑", 10))
        season_label.setFixedWidth(120)
        self.season_combo = QComboBox()
        self.season_combo.setEditable(True)
        self.season_combo.addItems(SEASON_OPTIONS)
        self.season_combo.setCurrentText(self.target_season)
        self.season_combo.setFont(QFont("微软雅黑", 10))
        self.season_combo.currentTextChanged.connect(self.on_target_season_changed)
        season_layout.addWidget(season_label)
        season_layout.addWidget(self.season_combo)
        season_layout.addStretch()
        config_layout.addLayout(season_layout)

        # 目标品阶（筛选弹窗里的四选一）
        tier_layout = QHBoxLayout()
        tier_label = QLabel("目标品阶:")
        tier_label.setFont(QFont("微软雅黑", 10))
        tier_label.setFixedWidth(120)
        self.tier_combo = QComboBox()
        self.tier_combo.addItems(["传说品阶", "史诗品阶", "稀有品阶", "普通品阶"])
        self.tier_combo.setCurrentText(self.target_tier)
        self.tier_combo.setFont(QFont("微软雅黑", 10))
        self.tier_combo.currentTextChanged.connect(self.on_target_tier_changed)
        tier_layout.addWidget(tier_label)
        tier_layout.addWidget(self.tier_combo)
        tier_layout.addStretch()
        config_layout.addLayout(tier_layout)

        # 目标成色（在售页成色筛选里的五选一）
        grade_layout = QHBoxLayout()
        grade_label = QLabel("目标成色:")
        grade_label.setFont(QFont("微软雅黑", 10))
        grade_label.setFixedWidth(120)
        self.grade_combo = QComboBox()
        self.grade_combo.addItems(["所有成色", "S", "A", "B", "C"])
        self.grade_combo.setCurrentText(self.target_grade)
        self.grade_combo.setFont(QFont("微软雅黑", 10))
        self.grade_combo.currentTextChanged.connect(self.on_target_grade_changed)
        grade_layout.addWidget(grade_label)
        grade_layout.addWidget(self.grade_combo)
        grade_layout.addStretch()
        config_layout.addLayout(grade_layout)

        # 抢到多少个后停止
        stop_layout = QHBoxLayout()
        stop_label = QLabel("抢到后停止:")
        stop_label.setFont(QFont("微软雅黑", 10))
        stop_label.setFixedWidth(120)
        self.stop_count_spin = QSpinBox()
        self.stop_count_spin.setRange(0, 999)
        self.stop_count_spin.setValue(self.stop_after_count)
        self.stop_count_spin.setSuffix(" 个")
        self.stop_count_spin.setSpecialValueText("不限")
        self.stop_count_spin.setFont(QFont("微软雅黑", 10))
        self.stop_count_spin.valueChanged.connect(self.on_stop_count_changed)
        stop_layout.addWidget(stop_label)
        stop_layout.addWidget(self.stop_count_spin)
        stop_layout.addStretch()
        config_layout.addLayout(stop_layout)
        
        # 剩余5秒时点击刷新选项（阈值见 main_gui.REFRESH_TRIGGER_SECONDS）
        refresh_layout = QHBoxLayout()
        self.refresh_checkbox = QCheckBox("剩余5秒时点击刷新")
        self.refresh_checkbox.setFont(QFont("微软雅黑", 10))
        self.refresh_checkbox.setChecked(self.click_refresh_enabled)
        self.refresh_checkbox.stateChanged.connect(self.on_refresh_changed)
        self.refresh_checkbox.setStyleSheet("""
            QCheckBox {
                padding: 5px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        refresh_layout.addWidget(self.refresh_checkbox)
        # 仅关注页模式。这个复选框必须和上面那个挤在同一行：单独占一行会把窗口
        # 撑高约 28px，而窗口底边现在就已经压到游戏「跳过」按钮上方了（见
        # main_gui.WINDOW_TOP_RATIO 的说明）。两个复选框合计 297px < 行宽 344px
        self.follow_only_checkbox = QCheckBox("仅在我的关注页抢购")
        self.follow_only_checkbox.setFont(QFont("微软雅黑", 10))
        self.follow_only_checkbox.setChecked(self.follow_only)
        self.follow_only_checkbox.setToolTip(
            "勾选后跳过准备阶段：不筛选商品、不自动加关注，只抢「我的关注」里已有的商品。\n"
            "该模式不检查价格上限；列表抢完后原地提醒，您手动加好关注脚本会自动继续")
        self.follow_only_checkbox.stateChanged.connect(self.on_follow_only_changed)
        self.follow_only_checkbox.setStyleSheet("""
            QCheckBox {
                padding: 5px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        refresh_layout.addWidget(self.follow_only_checkbox)
        refresh_layout.addStretch()
        config_layout.addLayout(refresh_layout)

        # 市场每日关闭时段：处于 [关闭时间, 开市时间) 内脚本会自动停止
        # （判定逻辑见 main_gui._market_closed）。两个时间都按 HH:MM 填
        market_layout = QHBoxLayout()
        market_label = QLabel("市场关闭时段:")
        market_label.setFont(QFont("微软雅黑", 10))
        market_label.setFixedWidth(120)
        self.market_close_edit = QLineEdit(self.market_close_time)
        self.market_close_edit.setFixedWidth(60)
        self.market_close_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.market_close_edit.setFont(QFont("微软雅黑", 10))
        self.market_close_edit.setToolTip("市场每天的关门时间，格式 HH:MM")
        market_tilde = QLabel("~")
        market_tilde.setFont(QFont("微软雅黑", 10))
        self.market_open_edit = QLineEdit(self.market_open_time)
        self.market_open_edit.setFixedWidth(60)
        self.market_open_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.market_open_edit.setFont(QFont("微软雅黑", 10))
        self.market_open_edit.setToolTip("市场重新开市时间，格式 HH:MM，需晚于关闭时间")
        self.market_close_edit.textChanged.connect(self.on_market_time_changed)
        self.market_open_edit.textChanged.connect(self.on_market_time_changed)
        market_layout.addWidget(market_label)
        market_layout.addWidget(self.market_close_edit)
        market_layout.addWidget(market_tilde)
        market_layout.addWidget(self.market_open_edit)
        market_layout.addStretch()
        config_layout.addLayout(market_layout)
        
        main_layout.addWidget(config_group)
        
        # ========== 日志区域 ==========
        log_group = QGroupBox("运行日志")
        log_group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                border: 2px solid #795548;
                border-radius: 5px;
                margin-top: 0px;
                padding-top: 10px;
            }
        """)
        log_layout = QVBoxLayout()
        log_group.setLayout(log_layout)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)
        # 明确给一个很小的最小高度：日志框默认的最小高度会把整个窗口顶高 60 多像素，
        # 而窗口是置顶的，底部会压到游戏结果页的「跳过」区域（详见 main_gui.WINDOW_TOP_RATIO）
        self.log_text.setMinimumHeight(26)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #263238;
                color: #B0BEC5;
                font-family: Consolas, monospace;
                font-size: 10px;
                border: 1px solid #37474F;
            }
        """)
        log_layout.addWidget(self.log_text)
        
        main_layout.addWidget(log_group)
        
        # ========== 控制按钮区域 ==========
        button_layout = QHBoxLayout()
        
        # 开始按钮
        self.start_btn = QPushButton("▶ 开始")
        self.start_btn.setFont(QFont("微软雅黑", 11, QFont.Weight.Bold))
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #CCCCCC;
            }
        """)
        self.start_btn.clicked.connect(self.on_start_clicked)
        button_layout.addWidget(self.start_btn)
        
        # 暂停/继续按钮
        self.pause_btn = QPushButton("⏸ 暂停")
        self.pause_btn.setFont(QFont("微软雅黑", 11, QFont.Weight.Bold))
        self.pause_btn.setEnabled(False)
        self.pause_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
            QPushButton:disabled {
                background-color: #CCCCCC;
            }
        """)
        self.pause_btn.clicked.connect(self.on_pause_clicked)
        button_layout.addWidget(self.pause_btn)
        
        # 停止按钮
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setFont(QFont("微软雅黑", 11, QFont.Weight.Bold))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
            QPushButton:disabled {
                background-color: #CCCCCC;
            }
        """)
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        button_layout.addWidget(self.stop_btn)
        
        self.preview_btn = QPushButton("👁 预览")
        self.preview_btn.setFont(QFont("微软雅黑", 9))
        self.preview_btn.setStyleSheet("""
            QPushButton {
                background-color: #673AB7;
                color: white;
                border: none;
                padding: 6px 10px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #5E35B1;
            }
        """)
        self.preview_btn.clicked.connect(self.on_preview_clicked)
        button_layout.addWidget(self.preview_btn)
        
        main_layout.addLayout(button_layout)
        
        # 添加弹性空间
        main_layout.addStretch()
        
        # 设置窗口样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: #FAFAFA;
            }
            QWidget {
                font-family: "微软雅黑";
            }
        """)
        
    def update_status(self, status):
        """更新状态"""
        self.status = status
        self.status_label.setText(f"状态: {status}")
        
        # 根据状态改变颜色
        if "运行" in status or "监控" in status:
            self.status_label.setStyleSheet("color: #4CAF50; padding: 5px;")
        elif "暂停" in status:
            self.status_label.setStyleSheet("color: #FF9800; padding: 5px;")
        elif "完成" in status or "成功" in status:
            self.status_label.setStyleSheet("color: #2196F3; padding: 5px;")
        elif "错误" in status or "失败" in status:
            self.status_label.setStyleSheet("color: #F44336; padding: 5px;")
        else:
            self.status_label.setStyleSheet("color: #757575; padding: 5px;")
    
    def update_timer(self, minutes, seconds):
        """更新倒计时"""
        self.minutes = str(minutes)
        self.seconds = str(seconds)
        self.timer_label.setText(f"{self.minutes}分{self.seconds}秒")
        
        # 如果时间快到了，变红色
        try:
            if int(minutes) == 0 and int(seconds) <= 5:
                self.timer_label.setStyleSheet("color: #F44336; padding: 20px;")
            else:
                self.timer_label.setStyleSheet("color: #00BCD4; padding: 20px;")
        except:
            pass
    
    def on_delay_changed(self, value):
        """购买点击延迟变更"""
        self.buy_click_delay = value
        self.add_log(f"⚙️ 购买点击延迟已设置为: {value}秒")
    
    def on_buy_to_verify_delay_changed(self, value):
        """购买到确认延迟变更"""
        self.buy_to_verify_delay = value
        self.add_log(f"⚙️ 购买确认间延迟已设置为: {value}秒")
    
    def on_buy_interval_changed(self, value):
        """购买点击间隔变更"""
        self.buy_interval = value
        self.add_log(f"⚙️ 购买点击间隔已设置为: {value}秒")
    
    def on_verify_interval_changed(self, value):
        """确认点击间隔变更"""
        self.verify_interval = value
        self.add_log(f"⚙️ 确认点击间隔已设置为: {value}秒")
    
    def on_buy_clicks_changed(self, value):
        """购买点击次数变更"""
        self.buy_clicks = value
        self.add_log(f"⚙️ 购买点击次数已设置为: {value}次")

    def on_verify_clicks_changed(self, value):
        """确认点击次数变更"""
        self.verify_clicks = value
        self.add_log(f"⚙️ 确认点击次数已设置为: {value}次")

    def on_ocr_interval_changed(self, value):
        """OCR识别间隔变更"""
        self.ocr_interval = value
        self.add_log(f"⚙️ OCR识别间隔已设置为: {value}秒")
    
    def on_price_limit_changed(self, value):
        """价格上限变更"""
        self.price_limit = value
        self.add_log(f"⚙️ 价格上限已设置为: {value}三角币")
    
    def on_target_season_changed(self, value):
        """目标赛季变更"""
        self.target_season = value.strip()
        self.add_log(f"⚙️ 目标赛季已设置为: {self.target_season}")

    def on_target_tier_changed(self, value):
        """目标品阶变更"""
        self.target_tier = value
        self.add_log(f"⚙️ 目标品阶已设置为: {value}")

    def on_target_grade_changed(self, value):
        """目标成色变更"""
        self.target_grade = value
        self.add_log(f"⚙️ 目标成色已设置为: {value}")

    def on_stop_count_changed(self, value):
        """抢到 N 个后停止"""
        self.stop_after_count = value
        status = "不限" if value == 0 else f"{value} 个"
        self.add_log(f"⚙️ 抢购数量上限已设置为: {status}")
    
    def on_refresh_changed(self, state):
        """剩余5秒时点击刷新选项变更"""
        self.click_refresh_enabled = self.refresh_checkbox.isChecked()
        status = "启用" if self.click_refresh_enabled else "禁用"
        self.add_log(f"⚙️ 剩余5秒时点击刷新: {status}")

    def on_follow_only_changed(self, state):
        """仅在我的关注页抢购 选项变更"""
        self.follow_only = self.follow_only_checkbox.isChecked()
        status = "启用" if self.follow_only else "禁用"
        self.add_log(f"⚙️ 仅在我的关注页抢购: {status}")

    def on_market_time_changed(self, _text):
        """市场关闭/开市时间变更

        不做格式校验，直接透传给主逻辑：时间填错或填反时 main_gui._market_closed
        会返回 False（即不启用该功能），不会把脚本永久挡住
        """
        self.market_close_time = self.market_close_edit.text().strip()
        self.market_open_time = self.market_open_edit.text().strip()
        self.add_log(f"⚙️ 市场关闭时段已设置为: "
                     f"{self.market_close_time or '未填'} ~ {self.market_open_time or '未填'}")
    
    def get_config(self):
        """获取当前配置"""
        return {
            'buy_click_delay': self.buy_click_delay,
            'buy_to_verify_delay': self.buy_to_verify_delay,
            'buy_interval': self.buy_interval,
            'buy_clicks': self.buy_clicks,
            'verify_interval': self.verify_interval,
            'verify_clicks': self.verify_clicks,
            'ocr_interval': self.ocr_interval,
            'price_limit': self.price_limit,
            'click_refresh_enabled': self.click_refresh_enabled,
            'follow_only': self.follow_only,
            'target_season': self.target_season,
            'target_tier': self.target_tier,
            'target_grade': self.target_grade,
            'stop_after_count': self.stop_after_count,
            'market_close_time': self.market_close_time,
            'market_open_time': self.market_open_time,
        }
    
    def add_log(self, message):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )
        logger.info(message)
    
    def on_start_clicked(self):
        """开始按钮点击"""
        self.is_running = True
        self.is_paused = False
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.update_status("运行中...")
        self.add_log("▶ 脚本已启动")
        self.controller.start_requested.emit()
    
    def on_pause_clicked(self):
        """暂停/继续按钮点击"""
        if self.is_paused:
            # 继续
            self.is_paused = False
            self.pause_btn.setText("⏸ 暂停")
            self.update_status("运行中...")
            self.add_log("▶ 脚本已继续")
            self.controller.resume_requested.emit()
        else:
            # 暂停
            self.is_paused = True
            self.pause_btn.setText("▶ 继续")
            self.update_status("已暂停")
            self.add_log("⏸ 脚本已暂停")
            self.controller.pause_requested.emit()
    
    def on_stop_clicked(self):
        """停止按钮点击"""
        self.is_running = False
        self.is_paused = False
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("⏸ 暂停")
        self.stop_btn.setEnabled(False)
        self.update_status("已停止")
        self.add_log("⏹ 脚本已停止")
        self.controller.stop_requested.emit()

    def on_preview_clicked(self):
        """预览区域按钮点击"""
        self.add_log("👁 正在生成区域预览...")
        self.controller.preview_requested.emit()

    def show_preview(self, pixmap):
        """显示区域预览对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle("区域预览")
        layout = QVBoxLayout(dialog)
        label = QLabel()
        scaled = pixmap.scaled(1280, 720, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        label.setPixmap(scaled)
        layout.addWidget(label)
        dialog.setLayout(layout)
        dialog.exec()
        self.add_log("👁 预览已关闭")
    
    def on_complete(self):
        """任务完成"""
        self.is_running = False
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.update_status("✅ 任务完成！")
        self.add_log("✅ 任务已完成")
