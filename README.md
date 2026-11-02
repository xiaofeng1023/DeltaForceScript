# DeltaForceScript

## ISSUE的统一回复

- 开发相关的进展在 dev 分支，合适的时候会合并到主分支
  - 三角洲的购买机制改了，下个版本计划会移除部分对于点击速度的优化，转而加入一些自动筛选皮肤的功能
  
    | 旧机制        | 新机制                     |
    | ---------- | ----------------------- |
    | 抢购模式，先到先得  | **随机抽选**购买资格            |
    | 需要拼手速，代抢盛行 | 记录参与抢购的若干名买家，从中**随机抽取** |
    | 无筛选功能      | 新增**皮肤筛选**功能            |
    
- 关于OCR识别的问题：我屏幕是2k的，可能需要改一下region_2k.json的数值，按照比例换算一下，您可以问问AI：“文件中提供了2k屏幕下的坐标，1k/4k下的对应坐标如何换算”
- 关于requirement.txt安装paddlepaddle-gpu的问题，参考官方链接：https://www.paddlepaddle.org.cn/install/quick?docurl=/documentation/docs/zh/develop/install
- 另外问问有没有好用的练枪的软件，我太菜了，大战场天天被当陀螺抽
- 看到有些兄弟模型下载有问题，我传了一个网盘的版本：
```
  通过网盘分享的文件：PP-OCRv5_for_DeltaForceScript
  链接: https://pan.baidu.com/s/1xUiHcZrw-AS85GxltRCC6g?pwd=dn3q 提取码: dn3q 
  --来自百度网盘超级会员v6的分享
```

## 项目简介

DeltaForceScript 是一个基于 PyQt6 的 Windows 自动购买（抢购）辅助脚本，使用 PaddleOCR 识别游戏画面，自动在交易行的多个页面之间导航、筛选商品、加关注并在倒计时末尾点击购买。项目以简单的 GUI 暴露常用配置，让用户无需修改代码即可微调行为。

主要功能：
- **页面识别**：根据界面特征文本自动判断当前处于哪个页面，并按页面分派对应操作
- **准备阶段**：自动完成「赛季 + 品阶」筛选、逐个商品进入在售页、按「成色」筛选、按价格上限自动加关注；读到超上限的价格时立即按 ESC 返回列表页换下一个商品（依据是「在售列表按价格升序」），一屏 6 张都在限内则自动向下滚继续抓（滚到底为止），左侧商品列表也会自动滚动到底（判定"是否真的滚动了"留了 12px 抖动余量：OCR 坐标有 ±1~2px 抖动，真正的滚动会整屏移动约一个行高，以前"754 → 753 差 1 像素"会被误判成滚动成功、把同一屏商品重复加一遍关注）；品阶/成色的复选框已勾选时会跳过，避免误取消筛选。**滚动后卡片会与固定网格错位**（滚轮步长不是卡片行高的整数倍），此时价格不再依赖固定采样格：先按原小范围读，读不到就纵向扩大范围重读一次，并用**价格实际所在的纵向位置反推卡片中心**再点击 —— 否则会出现「读到上一张卡的价格、点下去的却是下一张卡」，导致价格上限拦截失效；卡片被滚到网格上方时直接跳过不点击
- **抢购阶段**：在「我的关注」页读取倒计时文本（格式如 `0分1秒`），剩余 5 秒时刷新、剩余 1 秒时点击购买与确认；购买与确认点完后**立即按 ESC 返回关注列表**（不再等结算、不再点刷新），回到列表后等 `POST_BUY_SETTLE_SECONDS`（默认 1.0 秒）再读右上角三角币、余额减少即判定抢到（若这一下 ESC 把关注页也连带带出，下一轮会自动导航回关注页，见下）；已关闭 `pydirectinput` 每次鼠标调用自带的 0.2 秒停顿，一轮购买的 27 次点击不再白等 5.4 秒
- **末尾几秒的识别开销压到最低**：抢购窗口每秒轮询十几次，每轮只做 **1 次 OCR**（只读倒计时）。页面识别与关注数读取都会 OCR 关注页标识，原先每轮要做 3 次 OCR（实测约 100 ms/轮），现改为：读到倒计时就跳过页面识别（`snipe_live` 标记，读不到时立刻恢复完整识别），关注数只在读不到倒计时时才读。优化后约 54 ms/轮；购买/确认点击循环**最后一次点完不再多睡一个间隔**，一轮抢购的等待（`buy_click_delay` 0.30 + 购买间隔 0.30 + 确认间隔 1.68 + 结算等待 1.0）从 3.65 秒降到 3.28 秒
- **起点自适应**：脚本启动时若游戏已停在「我的关注」页，会跳过准备阶段直接进入抢购阶段；停在「典藏外观」列表页则走完整准备阶段。关注页列表为空时不会进入抢购（进了只会读到空列表），而是先退出关注页
- **关注页自动切排序**：每次进入「我的关注」页，先把顶部排序下拉框切成**「按稀有度升序」**（下拉框里的第 2 项，紧跟「默认排序」）（已经是就跳过；避免拖慢倒计时末尾的抢购，进入关注页后最多试 `FOLLOW_SORT_TRY_LIMIT` 轮，某一轮没切成下几轮接着试）。先 OCR 下拉框当前值，**只有读到排序名（含「排序 / 升序 / 降序」的文字）才点击**，且点的是识别到的文字位置而非区域中心；框错了只记一行日志然后跳过，绝不乱点。点开下拉框后会**连续重新截屏 4 次**找目标项（面板有展开动画、帧缓冲也可能慢一拍），点完还会**回读标题栏复核**是否真的切成了目标项。两个区域 `follow_sort` / `follow_sort_options` 的默认坐标已按 **2560×1440 实机截图**实测标定（2K 直接可用，其他分辨率需用 `region_selector.py` 重标；改目标排序见 `FOLLOW_SORT_TARGET`）
- **仅关注页模式**：勾选「仅在我的关注页抢购」后完全跳过准备阶段 —— 不筛选商品、不自动加关注，只抢自己关注列表里已有的商品；该模式不检查价格上限，列表抢完后停在原地提醒手动加关注（不再点击、不导航），用户加完关注回到关注页脚本会自动继续；运行中勾选/取消下一轮即生效
- 抢购前再次校验价格上限；弹出「获得外观」结果页时自动点「跳过」
- **抢购后自动回关注页继续抢**：抢购成功后的 `ESC` 有时会连同关注页一起带出（落到列表页或在售页），此时脚本按当前页面自己走回「我的关注」页，而不是退回准备阶段 —— 列表页点右侧「我的关注」入口、游戏大厅点「交易行」入口、交易行点「典藏外观」标签、在售页/弹窗/侧栏按返回键退一层；连续 `SNIPE_RETURN_ROUNDS`（默认 8）轮都回不去才认输退回准备阶段。避免"买到第一件就把已经关注好的商品全部作废、重新筛选一遍"
- 关注列表被抢空（真的读到 `我的关注：0/150`）后自动退出关注页、回到列表页开始下一轮（退出时等页面切换完成再确认，未退出则补按 ESC，见 `FOLLOW_EXIT_WAIT_SECONDS` / `FOLLOW_EXIT_ATTEMPTS`）；支持「抢到 N 个后停止」
- **市场关闭时段自动停止**：游戏市场每天 05:00 关闭、08:00 开市，处于 `[关闭时间, 开市时间)` 时脚本会自行停止（跨夜挂机到点即停；时段内启动则一开始就停）。两个时间在界面可改，填反或填成非法格式时视为不启用该功能，避免把脚本永久挡住
- 按页面区分防卡死恢复动作；恢复动作是"退回上一层"，连按 ESC 退过头时会走**返航链**自己回来（识别「游戏大厅」→ 点底部「交易行」入口 → 识别「交易行首页」→ 点顶部「典藏外观」标签 → 回到列表页），不会卡在大厅里认不出页面；连续 5 次恢复仍无进展则停机提示
- 提供 GUI 配置项：目标赛季 / 目标品阶 / 目标成色 / 价格上限 / 抢到后停止 / 市场关闭时段 / 仅在我的关注页抢购，以及各类点击延迟、次数与 OCR 间隔
- 支持多屏/Windows Graphics Capture（dxcam）捕获


## 目录结构（主要文件）

```
.
├── gui_monitor.py
├── main_gui.py
├── page_flow.py
├── models
│   ├── PaddlePaddle--PP-OCRv5_server_det
│   │   └── snapshots
│   │       └── master
│   │           ├── inference.json
│   │           ├── inference.pdiparams
│   │           └── inference.yml
│   └── PaddlePaddle--PP-OCRv5_server_rec
│       └── snapshots
│           └── master
│               ├── inference.json
│               ├── inference.pdiparams
│               └── inference.yml
├── README.md
├── regions_2k.json
├── region_selector.py
├── requirement.txt
└── window_capture.py
```


## 依赖

请使用 Python 3.10/3.11 及对应的 CUDA 驱动（如需 GPU 版本的 paddlepaddle）。依赖项见 `requirement.txt`：

- dxcam
- numpy
- pywin32
- pillow
- PyQt6
- PyDirectInput
- paddleocr
- paddlepaddle-gpu (如使用 GPU)

在虚拟环境中安装依赖（**必须用 `.venv` 自己的解释器装**，不要用 `activate` 后的裸 `pip`，
否则依赖会装进系统全局 Python，虚拟环境里依然缺包）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirement.txt
```

> 也可以直接双击 `start.bat`：它会自动检验 `.venv` 是否存在、依赖是否**真的装在 `.venv` 里**，
> 缺哪个就用 `.venv\Scripts\python.exe -m pip` 补装，再启动界面。

如果使用 GPU 的 `paddlepaddle-gpu`，请确保与本机 CUDA 驱动版本匹配（参见 PaddlePaddle 官方安装说明）。
注意 **`paddlepaddle-gpu` 3.x 没有发布在 PyPI**（PyPI 上最高只到 2.6.2），不能用 `-r requirement.txt` 装，
要按 CUDA 版本从 Paddle 官方源装：

```powershell
.\.venv\Scripts\python.exe -m pip uninstall -y paddlepaddle
.\.venv\Scripts\python.exe -m pip install paddlepaddle-gpu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
```


## 快速开始（运行步骤）

1. 准备模型（可选）：程序启动时会自动在 `models/` 下递归查找同时包含 `inference.json` 与 `inference.pdiparams` 的检测/识别模型目录并优先加载（启动更快、可离线）；未找到时由 PaddleOCR 自动下载。推荐放置位置：
   
   - 检测模型：`models/PaddlePaddle--PP-OCRv5_server_det/snapshots/master`
   - 识别模型：`models/PaddlePaddle--PP-OCRv5_server_rec/snapshots/master`

   模型地址：
   
   https://modelscope.cn/models/PaddlePaddle/PP-OCRv5_server_det
   
   https://modelscope.cn/models/PaddlePaddle/PP-OCRv5_server_rec

3. 运行程序（管理员权限）：

```powershell
python main_gui.py
```

脚本会尝试以管理员权限重新启动以获取更稳定的鼠标/键盘控制（仅 Windows）。

3. 在 GUI 中：
- 使用 `RegionSelector` 工具（脚本已提供）按页面标定区域并保存到 `regions_2k.json`（未标定的区域会使用 `page_flow.py` 里的内置默认值）。
- 在 GUI 的“脚本配置”区域调整：
  - 目标赛季（可编辑下拉框，内置 11 个赛季可选，也可直接输入赛季名）
  - 目标品阶（传说品阶 / 史诗品阶 / 稀有品阶 / 普通品阶）
  - 目标成色（所有成色 / S / A / B / C）
  - 价格上限（超过则不加关注、不购买）
  - 抢到后停止（0 = 不限）
  - 市场关闭时段（关闭时间 ~ 开市时间，默认 05:00 ~ 08:00，该时段内脚本自动停止）
  - 仅在我的关注页抢购（跳过准备阶段，只抢已关注的商品，不检查价格上限）
  - 购买点击延迟、购买确认间延迟、购买/确认点击次数与间隔
  - OCR识别间隔（调整识别频率）
- 点击“开始”启动脚本，脚本会在日志区域打印运行信息


## 配置说明（关键项）

- target_season：筛选弹窗里要选中的赛季。GUI 下拉可选 11 个赛季（棱镜攻势 / 美杜莎 / 电玩高手 / 命运王牌 / 气象感应 / 棱镜攻势S2 / 悬赏令 / 电玩高手S2 / 造物纪元 / 群星 / 疾光魅影），默认选中最后一项 `疾光魅影`；下拉框可编辑，名单外的赛季可直接手输。选赛季时**先按文字完全相等匹配，找不到才退回子串匹配**，所以「棱镜攻势」不会误选成「棱镜攻势S2」
- target_tier：筛选弹窗里要勾选的品阶
- target_grade：在售页成色筛选里要勾选的成色
- price_limit：价格上限，价格识别失败的商品会被跳过；在售页读到超限价格即返回列表页换下一个商品
- stop_after_count：抢到多少个后停止，0 表示不限
- market_close_time / market_open_time：市场每日关闭与开市时间（默认 `05:00` / `08:00`）。`[关闭时间, 开市时间)` 内脚本自行停止；开市时间不晚于关闭时间、或时间格式非法时视为不启用
- buy_click_delay：倒计时到触发点时，等待多少秒再点击购买（GUI 中为“购买点击延迟”，默认 0.30 秒 —— 抢购窗口只有 1 秒，这段等待就是纯粹的落后，想更快可调小）
- buy_to_verify_delay：购买后到点击确认之间的等待
- buy_clicks：购买按钮点击次数
- buy_interval：购买按钮多次点击之间的间隔（GUI 中为“购买点击间隔”，默认 0.30 秒）
- verify_clicks：确认按钮点击次数
- verify_interval：确认按钮多次点击之间的间隔
- ocr_interval：两次 OCR 识别之间的间隔
- click_refresh_enabled：是否在倒计时剩余 5 秒时点击刷新
- follow_only：仅在我的关注页抢购。为 `true` 时跳过准备阶段（不筛选、不自动加关注），只抢关注列表里已有的商品；该模式不检查 `price_limit`，列表抢完后原地提醒手动加关注（不点击、不导航），用户加完关注回到关注页自动继续

这些设置可在 GUI 中实时调整，且修改后会记录到日志。


## 区域选择（RegionSelector）

运行 `region_selector.py`（或在 GUI 中触发）以交互方式选择屏幕区域。
选择完成后请保存为 `regions_2k.json`（项目示例文件已包含），示例保存格式为：

```json
{
  "time": [100, 50, 400, 120],
  "buy": [500, 300, 620, 360],
  "verify": [520, 380, 660, 420],
  "follow_count": [110, 1240, 320, 1285]
}
```

确保 `time` 区域能完整包含倒计时文本，`follow_count` 能完整包含 `我的关注：N/150`。
运行 `region_selector.py` 时可以一路按 `ESC` 跳过不需要的区域，脚本会用内置默认值补上。

只标定个别区域、以及标定「需要手动展开的控件」（如赛季下拉选项列表，切窗口时会自动收起）

```powershell
python region_selector.py season_options --delay 8
```

`--delay N` 表示按下回车后等待 N 秒再截屏，这段时间用来切回游戏并点开要框选的控件。

## TODO

- [ ] 改用uv来管理依赖
- [ ] 这段时间有点忙，等12月底看看能不能优化一下性能，试试能不能靠网络抓包来搞（可能不太好搞，就怕他加密）

## 常见问题与故障排查

1. OCR 识别不准确或无识别结果：
   - 检查 `time` 区域是否正确截取（不要包含过多背景）
   - 提高 OCR 识别对比度（可在 `region_selector` 保存截图后手动检查）
   - 增大 `ocr_interval` 或调整识别模型

2. 点击不准确或失败：
   - 确认 `buy` 和 `verify` 区域坐标是否正确
   - 尝试以管理员权限运行（脚本会自动尝试请求管理员权限）
   - 调整 `buy_click_delay` 与 `buy_to_verify_delay`，应对界面响应延迟

3. GPU/模型问题：
   - 若使用 GPU，确保 `paddlepaddle-gpu` 与 CUDA 驱动匹配
   - 若无法加载模型，可尝试使用 CPU 版本 `paddlepaddle`（速度较慢）


## 安全与免责声明

- 本工具仅为自动化辅助工具。请确保在合法和符合相关服务条款的前提下使用。
- 使用脚本可能违反部分平台的使用协议，请自行承担风险。


## 开发与贡献

欢迎提交 issue 或 PR：
- 改进 OCR 容错、区域选择体验
- 增加更多点击策略（随机偏移、重试逻辑等）
