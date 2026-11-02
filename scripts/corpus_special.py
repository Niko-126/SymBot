"""特殊日作息派生：把用户挑的 5 条 B 站 vlog 事实推成 5 条 rhythm。

用法（仓库根目录，venv 里的 python）：
    python scripts/corpus_special.py            # 生成 + 自检 + 打印
    python scripts/corpus_special.py --dry-run  # 只打印，不写盘

产出（data/ 已 gitignore）：
    data/human_life/_derived/rhythm.special.jsonl   5 条
        vlog_sick_day         day_type=special  生病（低烧、失去味觉、吃粥面、活动量低）
        vlog_all_nighter      day_type=special  写字楼通宵加班，白天补觉、晚上续活
        vlog_holiday_outing   day_type=holiday  国庆假期第二天，见朋友出游
        vlog_trip_canada      day_type=travel   不上班慢节奏 + 回加拿大一个月，外出多
        vlog_6to1_day         day_type=restday  01:00 睡 06:00 起（约 5 小时），自习 + 健身

设计约定：
- 只读 ``_raw/bili-20261031-070622.jsonl``（标题/简介/评论），产出只保留枚举与推定时间轴。
- 不写真名/昵称、不带任何个人标识；脚本打印只出现 bvid 与分钟数，不打印标题和评论原文。
- 每条记录 synthetic=True，note 写明"据 vlog <bvid> 推定，不是精确时间轴"。
- 平铺必须 00:00→24:00 连续、合计精确 1440 分钟；自检不过就拒绝写盘。
- 异常一律不外抛：main() 兜住并打印原因，返回非 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final

ROOT: Final[Path] = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 `python scripts/corpus_special.py`
    sys.path.insert(0, str(ROOT))

RAW_FILE: Final[Path] = ROOT / "data" / "human_life" / "_raw" / "bili-20261031-070622.jsonl"
OUT_DIR: Final[Path] = ROOT / "data" / "human_life" / "_derived"
OUT_FILE: Final[Path] = OUT_DIR / "rhythm.special.jsonl"

DAY_MINUTES: Final[int] = 24 * 60

#: 5 条 vlog 的 bvid（只用来核对素材与写 note，不写任何真人标识）
BVIDS: Final[tuple[str, ...]] = (
    "BV18K421t7ex",  # 生病
    "BV1yJ41167jw",  # 通宵加班
    "BV1GNxz6YEzU",  # 国庆假期第二天
    "BV1KGxv6GEQM",  # 回加拿大一个月
    "BV1f3jc6kETj",  # 6 点起 1 点睡
)

# ---------------------------------------------------------------------------
# 5 条推定时间轴：每个块 (label, 分钟)，顺序就是"这一天怎么过"的推定
# 全部按 vlog 标题/简介/评论里的事实推，位置与切分都是推定，不是录像时间轴。
# ---------------------------------------------------------------------------

# 生病日：低烧 + 失去味觉，主要吃粥/面条，睡得碎、活动量低，没有工作
SICK_DAY_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 500),      # 00:00–08:20 夜里睡 + 早上赖床
    ("selfcare", 20),    # 08:20–08:40 量体温（偏低）
    ("meal", 25),        # 08:40–09:05 白粥
    ("screen", 40),      # 09:05–09:45 躺着刷手机
    ("sleep", 70),       # 09:45–10:55 回笼觉
    ("selfcare", 15),    # 10:55–11:10 吃药、喝水
    ("meal", 30),        # 11:10–11:40 面条（尝不出味道）
    ("leisure", 45),     # 11:40–12:25 听播客/看剧
    ("sleep", 60),       # 12:25–13:25 午睡
    ("selfcare", 20),    # 13:25–13:45 热毛巾、加湿
    ("screen", 45),      # 13:45–14:30
    ("meal", 25),        # 14:30–14:55 水果加餐
    ("leisure", 60),     # 14:55–15:55
    ("sleep", 50),       # 15:55–16:45 傍晚又迷糊过去
    ("selfcare", 15),    # 16:45–17:00
    ("meal", 30),        # 17:00–17:30 粥/面
    ("leisure", 75),     # 17:30–18:45
    ("screen", 40),      # 18:45–19:25
    ("errand", 15),      # 19:25–19:40 下楼拿快递/买药
    ("selfcare", 25),    # 19:40–20:05 擦身/泡脚
    ("leisure", 70),     # 20:05–21:15
    ("screen", 45),      # 21:15–22:00
    ("selfcare", 15),    # 22:00–22:15 睡前吃药
    ("sleep", 105),      # 22:15–24:00
)

# 通宵加班：写字楼空无一人还在赶活，凌晨继续，白天补觉，晚上又续上
ALL_NIGHTER_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("work", 300),       # 00:00–05:00 空写字楼通宵
    ("commute", 35),     # 05:00–05:35 回家路上
    ("selfcare", 20),    # 05:35–05:55 洗漱
    ("sleep", 340),      # 05:55–11:35 白天补觉
    ("meal", 20),        # 11:35–11:55 随便吃点
    ("screen", 30),      # 11:55–12:25
    ("sleep", 120),      # 12:25–14:25 继续睡
    ("selfcare", 20),    # 14:25–14:45
    ("meal", 35),        # 14:45–15:20
    ("work", 60),        # 15:20–16:20 下午远程补活
    ("leisure", 40),     # 16:20–17:00
    ("chores", 30),      # 17:00–17:30 洗衣服
    ("work", 120),       # 17:30–19:30 晚上接着干
    ("meal", 40),        # 19:30–20:10 晚饭
    ("screen", 45),      # 20:10–20:55
    ("work", 105),       # 20:55–22:40 继续加班
    ("selfcare", 20),    # 22:40–23:00
    ("leisure", 40),     # 23:00–23:40
    ("screen", 20),      # 23:40–24:00
)

# 节假日：国庆假期第二天，难得化妆出门，和许久没见的朋友见面、出游、一起吃饭
HOLIDAY_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 420),      # 00:00–07:00
    ("selfcare", 45),    # 07:00–07:45 难得化妆
    ("meal", 25),        # 07:45–08:10 早餐
    ("chores", 30),      # 08:10–08:40 做三明治带出门
    ("commute", 50),     # 08:40–09:30 地铁去市区
    ("social", 180),     # 09:30–12:30 和很久没见的朋友见面
    ("meal", 60),        # 12:30–13:30 一起吃饭
    ("commute", 30),     # 13:30–14:00 转场去景点
    ("leisure", 120),    # 14:00–16:00 出游、逛街、拍照
    ("meal", 30),        # 16:00–16:30 奶茶小吃
    ("leisure", 90),     # 16:30–18:00 继续逛
    ("commute", 45),     # 18:00–18:45 回程
    ("selfcare", 20),    # 18:45–19:05 卸妆
    ("meal", 45),        # 19:05–19:50 晚饭
    ("screen", 40),      # 19:50–20:30
    ("leisure", 60),     # 20:30–21:30 剪视频/看片
    ("screen", 30),      # 21:30–22:00
    ("selfcare", 20),    # 22:00–22:20
    ("sleep", 100),      # 22:20–24:00
)

# 旅行日：不上班的慢节奏，回加拿大一个月，白天外出逛街采购、咖啡馆公园，交通多
TRIP_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 430),      # 00:00–07:10
    ("selfcare", 40),    # 07:10–07:50
    ("meal", 30),        # 07:50–08:20 住处早餐
    ("chores", 20),      # 08:20–08:40 收拾包/行李
    ("commute", 40),     # 08:40–09:20 出门
    ("leisure", 90),     # 09:20–10:50 逛街区、咖啡馆
    ("meal", 30),        # 10:50–11:20 咖啡 + 点心
    ("errand", 45),      # 11:20–12:05 超市采购
    ("commute", 50),     # 12:05–12:55 转场
    ("meal", 50),        # 12:55–13:45 午饭
    ("leisure", 120),    # 13:45–15:45 公园、逛街
    ("study", 30),       # 15:45–16:15 线上课
    ("commute", 40),     # 16:15–16:55 回住处
    ("selfcare", 20),    # 16:55–17:15
    ("chores", 25),      # 17:15–17:40 洗衣服
    ("meal", 45),        # 17:40–18:25 自己做饭
    ("leisure", 60),     # 18:25–19:25
    ("study", 45),       # 19:25–20:10 网课
    ("leisure", 75),     # 20:10–21:25 看剧
    ("screen", 40),      # 21:25–22:05
    ("selfcare", 25),    # 22:05–22:30
    ("sleep", 90),       # 22:30–24:00
)

# 6 点起 1 点睡：01:00–06:00 睡（约 5 小时），白天外出自习 + 健身，深夜还在活动
SIX_TO_ONE_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("leisure", 60),     # 00:00–01:00 深夜剪片/看剧
    ("sleep", 300),      # 01:00–06:00 睡 5 小时
    ("selfcare", 30),    # 06:00–06:30 起床洗漱
    ("meal", 25),        # 06:30–06:55 早餐（奶酪贝果）
    ("exercise", 60),    # 06:55–07:55 早上健身
    ("selfcare", 20),    # 07:55–08:15 洗澡换衣
    ("commute", 30),     # 08:15–08:45 出门去自习
    ("study", 150),      # 08:45–11:15 上午自习
    ("meal", 20),        # 11:15–11:35 冰饮
    ("study", 120),      # 11:35–13:35 继续自习
    ("meal", 45),        # 13:35–14:20 一人食午饭
    ("study", 90),       # 14:20–15:50 下午自习
    ("commute", 30),     # 15:50–16:20 回家
    ("chores", 40),      # 16:20–17:00 收拾/洗衣
    ("screen", 30),      # 17:00–17:30
    ("meal", 40),        # 17:30–18:10 晚饭
    ("study", 60),       # 18:10–19:10 晚上学习
    ("leisure", 60),     # 19:10–20:10 散步/看剧
    ("exercise", 60),    # 20:10–21:10 晚间拉伸/健身
    ("selfcare", 25),    # 21:10–21:35 护肤
    ("leisure", 105),    # 21:35–23:20 看剧/剪视频
    ("screen", 40),      # 23:20–24:00
)

#: (id, day_type, 块, note) —— note 里只出现 bvid 与事实，不出现任何真人标识
RECIPES: Final[tuple[tuple[str, str, tuple[tuple[str, int], ...], str], ...]] = (
    (
        "vlog_sick_day",
        "special",
        SICK_DAY_BLOCKS,
        "据 B 站 vlog BV18K421t7ex 的标题/简介/评论推定的典型日，不是精确时间轴："
        "冻雨天独居生病、低烧、失去味觉，只能吃粥和面条，活动量低，以睡眠、吃药、喝水为主。",
    ),
    (
        "vlog_all_nighter",
        "special",
        ALL_NIGHTER_BLOCKS,
        "据 B 站 vlog BV1yJ41167jw 的标题/简介/评论推定的典型日，不是精确时间轴："
        "在空无一人的写字楼通宵加班，凌晨仍在工作，白天补觉、晚上继续赶活。",
    ),
    (
        "vlog_holiday_outing",
        "holiday",
        HOLIDAY_BLOCKS,
        "据 B 站 vlog BV1GNxz6YEzU 的标题/简介/评论推定的典型日，不是精确时间轴："
        "国庆假期第二天，难得化妆出门，和许久没见的朋友见面、出游、一起吃饭，社交与外出为主。",
    ),
    (
        "vlog_trip_canada",
        "travel",
        TRIP_BLOCKS,
        "据 B 站 vlog BV1KGxv6GEQM 的标题/简介/评论推定的典型日，不是精确时间轴："
        "不上班的慢节奏 + 回加拿大一个月，白天外出逛街、采购、咖啡馆和公园，交通与休闲占比高。",
    ),
    (
        "vlog_6to1_day",
        "restday",
        SIX_TO_ONE_BLOCKS,
        "据 B 站 vlog BV1f3jc6kETj 的标题/简介/评论推定的典型日，不是精确时间轴："
        "独居，01:00 睡、06:00 起（约 5 小时睡眠），白天外出自习、健身，深夜仍在活动。",
    ),
)


# ---------------------------------------------------------------------------
# 用户自述（2026-10-31）：广告公司一天 A/B/C + 独居发烧日
# ---------------------------------------------------------------------------
# 素材只读；时间点是本人写的，没报的时段按前后文补齐，所以整条标 synthetic=true。
# 产出不写具体公司名/客户名，也不贴原文长段落，只保留枚举与推定时间轴。

USER_AD_DAYS: Final[Path] = ROOT / "data" / "human_life" / "_raw" / "user-report-ad-days.txt"
USER_SICK: Final[Path] = ROOT / "data" / "human_life" / "_raw" / "user-report-sick-alone.txt"
USER_OVERTIME: Final[Path] = ROOT / "data" / "human_life" / "_raw" / "user-report-ad-overtime-notes.txt"

# A：策划/文案视角。8:55 起床、9:00 到公司、23:59 交稿；其余照自述时间点落段。
USER_DAY_A_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 535),      # 00:00–08:55 睡到 8:55 起床
    ("commute", 5),      # 08:55–09:00 5 分钟到公司，全勤到手
    ("meal", 10),        # 09:00–09:10 公司里吃完早餐
    ("work", 50),        # 09:10–10:00 和客户沟通
    ("screen", 60),      # 10:00–11:00 看微博、看世界大事、收集素材
    ("work", 60),        # 11:00–12:00 客户催了一下稿，答应今天给
    ("meal", 60),        # 12:00–13:00 午饭
    ("sleep", 30),       # 13:00–13:30 午睡（13:30 睡醒）
    ("screen", 60),      # 13:30–14:30 从午觉里缓过来
    ("work", 30),        # 14:30–15:00 写下一个大标题
    ("work", 60),        # 15:00–16:00 写不出来，脑暴
    ("screen", 60),      # 16:00–17:00 脑暴失败，看别人怎么做
    ("work", 30),        # 17:00–17:30 疯狂打框架、写小解释
    ("work", 30),        # 17:30–18:00 补小标题，死线袭来
    ("work", 30),        # 18:00–18:30 客户催稿
    ("work", 30),        # 18:30–19:00 极速飙稿
    ("meal", 40),        # 19:00–19:40 先去吃点东西
    ("leisure", 20),     # 19:40–20:00 一边吃一边煲新番
    ("work", 120),       # 20:00–22:00 挑 PPT 模板、排版
    ("work", 120),       # 22:00–24:00 文档贴进 PPT，23:59 交稿下班
)

# B：AE/执行视角。9:30 到（通常只有保洁），之后一路催稿改稿，夜班到零点后。
USER_DAY_B_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 510),      # 00:00–08:30
    ("selfcare", 20),    # 08:30–08:50 起床洗漱
    ("commute", 40),     # 08:50–09:30 通勤
    ("work", 30),        # 09:30–10:00 到了的只有保洁
    ("work", 30),        # 10:00–10:30 同事陆续到，梳理当天工作、发确认
    ("errand", 15),      # 10:30–10:45 双方确认后下楼买咖啡
    ("work", 15),        # 10:45–11:00 更新进度、开始催稿
    ("work", 30),        # 11:00–11:30 浅浅摸鱼，接客户电话，改昨天的稿
    ("work", 20),        # 11:30–11:50 梳理修改意见发文案/设计
    ("work", 10),        # 11:50–12:00 交初稿、内部校稿
    ("meal", 60),        # 12:00–13:00 吃饭，有事下午两点后说
    ("sleep", 60),       # 13:00–14:00 午休
    ("work", 60),        # 14:00–15:00 改 1.0
    ("work", 60),        # 15:00–16:00 发甲方、催修改意见
    ("work", 60),        # 16:00–17:00 甲方意见到了，改 2.0
    ("work", 60),        # 17:00–18:00 催设计/文案改稿，改 3.0
    ("work", 30),        # 18:00–18:30 交 3.0、催甲方看稿
    ("meal", 30),        # 18:30–19:00 扒两口饭
    ("work", 60),        # 19:00–20:00 早班的陆续走，剩下的继续
    ("work", 120),       # 20:00–22:00 夜班时间到，新一轮修改
    ("work", 60),        # 22:00–23:00 能报销打车，走一批，留下的继续磨
    ("work", 60),        # 23:00–24:00 半夜十二点后骂骂咧咧继续加班
)

# C：AD/项目视角。客户 8 点就催稿、通勤路上回消息，下午开会 2 小时起步，23:00 还在。
USER_DAY_C_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 480),      # 00:00–08:00
    ("selfcare", 15),    # 08:00–08:15 起床
    ("commute", 45),     # 08:15–09:00 通勤路上给客户回消息
    ("work", 60),        # 09:00–10:00 到公司解决问题、安排组内一天工作
    ("work", 30),        # 10:00–10:30 各组到齐，站着开早会
    ("work", 60),        # 10:30–11:30 黄金一小时，效率最高
    ("meal", 60),        # 11:30–12:30 陆续点饭、吃午餐
    ("screen", 30),      # 12:30–13:00 溜号摸鱼
    ("sleep", 60),       # 13:00–14:00 午休，有人不休打两把
    ("work", 150),       # 14:00–16:30 开会、头脑风暴，2 小时起步
    ("work", 90),        # 16:30–18:00 落方案、盯进度
    ("meal", 60),        # 18:00–19:00 客户新 brief 到了，先吃饭
    ("work", 240),       # 19:00–23:00 夜班：写方案、改设计、盯进度
    ("work", 60),        # 23:00–24:00 一抬眼 23:00，还在公司
)

# 独居发烧日：自述只讲状态与处理方式（昏睡/躺平/买药/外卖/看医生），时间轴全推定。
USER_SICK_BLOCKS: Final[tuple[tuple[str, int], ...]] = (
    ("sleep", 450),      # 00:00–07:30 昏睡
    ("selfcare", 20),    # 07:30–07:50 量体温、吃药
    ("screen", 40),      # 07:50–08:30 躺着刷手机
    ("sleep", 150),      # 08:30–11:00 又睡过去
    ("selfcare", 20),    # 11:00–11:20 起来喝水、吃药
    ("meal", 30),        # 11:20–11:50 点外卖，只吃得下清淡的
    ("sleep", 100),      # 11:50–13:30 躺平昏睡
    ("screen", 40),      # 13:30–14:10 没精神，刷手机
    ("errand", 30),      # 14:10–14:40 下楼买药
    ("selfcare", 20),    # 14:40–15:00 吃药
    ("sleep", 90),       # 15:00–16:30 又睡
    ("leisure", 40),     # 16:30–17:10 低耗听播客
    ("meal", 30),        # 17:10–17:40 外卖
    ("selfcare", 30),    # 17:40–18:10 物理降温、擦身
    ("screen", 50),      # 18:10–19:00
    ("leisure", 60),     # 19:00–20:00 看剧，不动脑
    ("errand", 20),      # 20:00–20:20 线上问诊/看医生
    ("meal", 40),        # 20:20–21:00 吃点水果加餐
    ("selfcare", 30),    # 21:00–21:30 洗澡擦身
    ("leisure", 60),     # 21:30–22:30
    ("screen", 30),      # 22:30–23:00
    ("selfcare", 20),    # 23:00–23:20 睡前吃药
    ("sleep", 40),       # 23:20–24:00
)

#: (id, day_type, 块, note) —— 用户自述 4 条；note 只写“用户提供的自述”，不写公司/客户名
USER_RECIPES: Final[tuple[tuple[str, str, tuple[tuple[str, int], ...], str], ...]] = (
    (
        "user_ad_day_a",
        "workday",
        USER_DAY_A_BLOCKS,
        "据用户提供的广告公司一天自述 A（原文 data/human_life/_raw/user-report-ad-days.txt），"
        "未报时段按上下文补齐：8:55 起床、9:00 到公司、13:00–13:30 午睡、23:59 交稿后补到 24:00。",
    ),
    (
        "user_ad_day_b",
        "workday",
        USER_DAY_B_BLOCKS,
        "据用户提供的广告公司一天自述 B（原文 data/human_life/_raw/user-report-ad-days.txt），"
        "未报时段按上下文补齐：9:30 到公司只有保洁、12:00–14:00 午休、19:00 后留下加班到零点后。",
    ),
    (
        "user_ad_day_c",
        "workday",
        USER_DAY_C_BLOCKS,
        "据用户提供的广告公司一天自述 C（原文 data/human_life/_raw/user-report-ad-days.txt），"
        "未报时段按上下文补齐：8:15 通勤路上回消息、10:30 黄金一小时、14:00 开会 2 小时以上、23:00 还在。",
    ),
    (
        "user_sick_day",
        "special",
        USER_SICK_BLOCKS,
        "据用户提供的独居发烧自述：昏睡/躺平/买药/外卖/看医生，时间轴为推定"
        "（原文 data/human_life/_raw/user-report-sick-alone.txt）。",
    ),
)

#: 16 条 activity：把自述里的动作拆成单条记录（day_type 全为 workday）
USER_AD_ACTIVITIES: Final[tuple[tuple[str, str, str, int, str, bool, int, str], ...]] = (
    (
        "user_ad_commute_reply", "commute", "morning", 45, "transit", True, 3,
        "据用户提供的自述 C：客户 8 点就催稿，通勤路上给客户回消息",
    ),
    (
        "user_ad_wake_up", "selfcare", "morning", 15, "home", True, 1,
        "据用户提供的自述 C：8:00 起床洗漱，8:15 出门通勤",
    ),
    (
        "user_ad_evening_show", "leisure", "evening", 60, "office", True, 2,
        "据用户提供的自述 A：19:00 先去吃点东西，一边吃一边煲新番",
    ),
    (
        "user_ad_scroll_material", "screen", "morning", 60, "office", True, 2,
        "据用户提供的自述 A：10:00 看微博、看世界大事、收集素材和流行语",
    ),
    (
        "user_ad_urge_copy", "work", "noon", 60, "office", False, 4,
        "据用户提供的自述 A/B：客户催稿，AE 挨着催文案和设计",
    ),
    (
        "user_ad_coffee", "errand", "morning", 15, "street", True, 2,
        "据用户提供的自述 B：双方确认后下楼买杯咖啡",
    ),
    (
        "user_ad_brainstorm", "work", "afternoon", 60, "office", False, 3,
        "据用户提供的自述 A/C：脑暴、头脑风暴会想方向",
    ),
    (
        "user_ad_outline", "work", "evening", 90, "office", False, 2,
        "据用户提供的自述 A：17:00 疯狂打框架、写小解释和小标题",
    ),
    (
        "user_ad_meeting", "work", "afternoon", 120, "office", False, 3,
        "据用户提供的自述 C：14:00 起开会、头脑风暴，2 小时起步",
    ),
    (
        "user_ad_ppt_paste", "work", "night", 120, "office", True, 2,
        "据用户提供的自述 A：20:00 挑 PPT 模板，22:00 把文档贴进 PPT",
    ),
    (
        "user_ad_lunch", "meal", "noon", 60, "office", False, 3,
        "据用户提供的自述 A/C：12:00 午饭，11:30 起陆续点饭",
    ),
    (
        "user_ad_nap", "sleep", "afternoon", 60, "office", True, 1,
        "据用户提供的自述 B/C：12:00–14:00 午休，有人不睡打两把",
    ),
    (
        "user_ad_night_revision", "work", "evening", 90, "office", False, 2,
        "据用户提供的自述 B 与加班真相要点：夜班改稿，客户下班前才给 brief",
    ),
    (
        "user_ad_handoff", "work", "night", 60, "office", False, 2,
        "据用户提供的自述 A：23:59 交稿，催客户给意见再下班",
    ),
    (
        "user_ad_taxi_home", "commute", "night", 40, "transit", True, 2,
        "据用户提供的自述 B：22:00 能报销打车，走一批人，留下的继续磨",
    ),
    (
        "user_ad_allnighter", "work", "late_night", 300, "office", False, 1,
        "据加班真相要点：重大节点/比稿会通宵，熬夜到凌晨 5 点、睡 3 小时再上班",
    ),
)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _hhmm(minutes: int) -> str:
    """分钟数转 HH:MM（1440 显示 24:00）。"""
    bounded = max(0, min(int(minutes), DAY_MINUTES))
    return f"{bounded // 60:02d}:{bounded % 60:02d}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """逐行读 JSONL；读不到或坏行都跳过，不抛异常。"""
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[跳过] 读不到 {path}（{exc}）")
        return rows
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _activity_enum() -> frozenset[str]:
    """活动类型枚举：优先用项目 schema（唯一权威），导入失败退回内置集合。"""
    try:
        from app.lifecorpus.schema import ACTIVITY_TYPES  # noqa: PLC0415

        return frozenset(ACTIVITY_TYPES)
    except Exception:  # noqa: BLE001 - 拿不到枚举也不能让整个脚本挂掉
        return frozenset(
            {
                "sleep", "meal", "work", "study", "commute", "exercise", "chores",
                "leisure", "social", "screen", "selfcare", "errand", "other",
            }
        )


def tile(blocks: Iterable[tuple[str, int]]) -> list[dict[str, str]]:
    """把有序块拼成 segments。"""
    segments: list[dict[str, str]] = []
    cursor = 0
    for label, minutes in blocks:
        segments.append({"start": _hhmm(cursor), "end": _hhmm(cursor + minutes), "label": label})
        cursor += minutes
    return segments


def label_totals(blocks: Iterable[tuple[str, int]]) -> dict[str, int]:
    """按标签汇总分钟数。"""
    totals: dict[str, int] = {}
    for label, minutes in blocks:
        totals[label] = totals.get(label, 0) + minutes
    return totals


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def check_recipe(
    record_id: str,
    blocks: Sequence[tuple[str, int]],
) -> tuple[list[str], int]:
    """自检一条 recipe：分钟合计、段数、标签枚举、块本身是否合法；返回 (问题列表, 总分钟)。"""
    problems: list[str] = []
    enum = _activity_enum()

    if not 2 <= len(blocks) <= 48:
        problems.append(f"{record_id}: 段数 {len(blocks)} 超出 [2, 48]")

    total = 0
    for index, (label, minutes) in enumerate(blocks, start=1):
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
            problems.append(f"{record_id}: 第 {index} 块分钟数非法 {minutes!r}")
            continue
        if label not in enum:
            problems.append(f"{record_id}: 第 {index} 块 label={label!r} 不在活动类型枚举内")
        total += minutes

    if total != DAY_MINUTES:
        problems.append(f"{record_id}: 合计 {total} 分钟，应为 {DAY_MINUTES}")
    return problems, total


def build_record(
    record_id: str,
    day_type: str,
    blocks: Sequence[tuple[str, int]],
    note: str,
) -> dict[str, Any]:
    """构造一条 rhythm 记录（synthetic 恒为 True）。"""
    return {
        "id": record_id,
        "kind": "rhythm",
        "day_type": day_type,
        "synthetic": True,
        "segments": tile(blocks),
        "note": note,
    }


def validate_with_schema(records: Sequence[dict[str, Any]], kind: str = "rhythm") -> list[str]:
    """用项目 schema 复核（拿不到 schema 就跳过，由 CLI 兜底）。"""
    try:
        from app.lifecorpus.schema import validate_record  # noqa: PLC0415

        return [f"{record.get('id')}: {error}" for record in records for error in validate_record(kind, record)]
    except Exception as exc:  # noqa: BLE001
        print(f"    （schema 自检跳过：{exc}）")
        return []


# ---------------------------------------------------------------------------
# 写盘与打印
# ---------------------------------------------------------------------------


def write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> bool:
    """写 JSONL；失败打印原因并返回 False。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[失败] 写 {path} 出错：{exc}")
        return False
    print(f"    写入 {path.relative_to(ROOT)}：{len(records)} 条")
    return True


def print_bvid_check() -> list[str]:
    """核对 5 个 bvid 是否真在原始采集结果里（只比 id，不打印标题/评论）。"""
    print("[1] 素材核对 —— bvid 是否在 _raw 采集结果里（只比对 id）")
    problems: list[str] = []
    available = {row.get("bvid") for row in _read_jsonl(RAW_FILE)}
    available.discard(None)
    if not available:
        problems.append("原始采集结果为空，无法核对 bvid")
    for bvid in BVIDS:
        ok = bvid in available
        print(f"    {bvid}  {'✓' if ok else '✗ 未找到'}")
        if not ok:
            problems.append(f"bvid {bvid} 不在原始采集结果里")
    print(f"    共 {len(available)} 条素材，本条脚本引用 {len(BVIDS)} 条")
    print()
    return problems


def print_self_check(
    recipes: Sequence[tuple[str, str, Sequence[tuple[str, int]], str]],
    title: str = "[2] 自检 —— 每条必须完整平铺 00:00→24:00 且合计 1440 分钟",
) -> list[str]:
    """逐条打印分钟总和、段数与标签分布。"""
    print(title)
    problems: list[str] = []
    for record_id, day_type, blocks, _note in recipes:
        block_problems, total = check_recipe(record_id, blocks)
        problems.extend(block_problems)
        totals = label_totals(blocks)
        detail = " ".join(f"{label}={minutes}" for label, minutes in sorted(totals.items()))
        print(
            f"    {record_id:<22} day_type={day_type:<8} {len(blocks):>2} 段  "
            f"合计 {total:>4}/{DAY_MINUTES} 分钟  {'✓' if total == DAY_MINUTES else '✗'}"
        )
        print(f"      {detail}")
        for item in block_problems:
            print(f"      ✗ {item}")
    print()
    return problems


def print_user_source_check() -> list[str]:
    """核对用户自述原文是否存在、关键词是否命中；只打印字数与命中数，不打印原文。"""
    print("[1b] 素材核对 —— 用户自述原文（只核字数与关键词，不打印原文）")
    problems: list[str] = []
    checks = (
        (USER_AD_DAYS, ("8:55", "23:59", "9:30", "10:30", "14:00", "23:00")),
        (USER_SICK, ("发烧", "昏昏欲睡", "外卖", "去医院")),
        (USER_OVERTIME, ("通宵", "比稿", "加班费", "brief")),
    )
    for path, keys in checks:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"    {path.name}  ✗ 读不到（{exc}）")
            problems.append(f"读不到用户自述原文：{path.name}")
            continue
        hits = sum(1 for key in keys if key in text)
        print(f"    {path.name:<34} 存在 {len(text):>5} 字，关键词命中 {hits}/{len(keys)}")
        if hits == 0:
            problems.append(f"{path.name} 一个关键词都没命中，可能不是预期素材")
    print()
    return problems


def build_user_activities() -> list[dict[str, Any]]:
    """把 USER_AD_ACTIVITIES 展开成 activity 记录（day_type 全为 workday，synthetic=true）。"""
    rows: list[dict[str, Any]] = []
    for record_id, activity_type, time_of_day, duration_min, location, alone, energy, note in USER_AD_ACTIVITIES:
        rows.append(
            {
                "id": record_id,
                "kind": "activity",
                "day_type": "workday",
                "activity_type": activity_type,
                "time_of_day": time_of_day,
                "duration_min": duration_min,
                "location": location,
                "alone": alone,
                "energy": energy,
                "note": note,
                "synthetic": True,
            }
        )
    return rows


def print_activity_check(activities: Sequence[dict[str, Any]]) -> list[str]:
    """打印 activity 条数、类型/时段/地点分布，检查必覆盖类型是否齐全。"""
    print("[2c] 自检 —— 用户自述 activity（条数 12–16、枚举、时段）")
    problems: list[str] = []
    if not 12 <= len(activities) <= 16:
        problems.append(f"activity 条数 {len(activities)} 不在 12–16")
    by_type: dict[str, int] = {}
    by_tod: dict[str, int] = {}
    by_location: dict[str, int] = {}
    for record in activities:
        by_type[record["activity_type"]] = by_type.get(record["activity_type"], 0) + 1
        by_tod[record["time_of_day"]] = by_tod.get(record["time_of_day"], 0) + 1
        by_location[record["location"]] = by_location.get(record["location"], 0) + 1
    print(f"    条数 {len(activities)}；类型 {len(by_type)} 种：{dict(sorted(by_type.items()))}")
    print(f"    时段：{dict(sorted(by_tod.items()))}")
    print(f"    地点：{dict(sorted(by_location.items()))}")
    required = ("work", "meal", "commute", "screen", "errand", "leisure", "selfcare")
    missing = [name for name in required if not by_type.get(name)]
    print(f"    必覆盖类型缺失：{missing if missing else '无 ✓'}")
    if missing:
        problems.append(f"activity 缺少类型：{', '.join(missing)}")
    print()
    return problems


def print_summary(records: Sequence[dict[str, Any]]) -> None:
    """打印日型分布与总睡眠时长，方便和"事实"对一眼。"""
    print("[3] 汇总 —— 日型分布与睡眠时长")
    by_day: dict[str, int] = {}
    for record in records:
        day_type = str(record.get("day_type"))
        by_day[day_type] = by_day.get(day_type, 0) + 1
        sleep_minutes = sum(
            (int(s["end"][:2]) * 60 + int(s["end"][3:5])) - (int(s["start"][:2]) * 60 + int(s["start"][3:5]))
            for s in record["segments"]
            if s.get("label") == "sleep"
        )
        print(f"    {record['id']:<22} {day_type:<8} 睡眠 {sleep_minutes:>3} 分钟（{sleep_minutes / 60:.1f} 小时）")
    print(f"    日型分布：{by_day}")
    print()


def _ensure_utf8_stdout() -> None:
    """重定向到管道/文件时强制 UTF-8，避免 Windows 控制台默认 cp936 把中文写成乱码。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(dry_run: bool = False) -> int:
    """主流程：核对素材 → 构造 → 自检 → 写盘。返回进程退出码。"""
    print("== 语料派生：B 站特殊日 vlog + 用户自述日 ==")
    print(f"项目根目录：{ROOT}")
    print(f"输出目录：{OUT_DIR.relative_to(ROOT)}（data/ 已 gitignore）")
    print()

    problems = print_bvid_check()
    problems.extend(print_user_source_check())
    problems.extend(print_self_check(RECIPES))
    problems.extend(print_self_check(USER_RECIPES, title="[2b] 自检 —— 用户自述 4 条（A/B/C 广告工作日 + 发烧日）"))

    records: list[dict[str, Any]] = []
    for record_id, day_type, blocks, note in RECIPES + USER_RECIPES:
        if len(note) > 300:
            problems.append(f"{record_id}: note 长度 {len(note)} 超过 300")
        records.append(build_record(record_id, day_type, blocks, note))

    activities = build_user_activities()
    problems.extend(validate_with_schema(records))
    problems.extend(validate_with_schema(activities, kind="activity"))
    problems.extend(print_activity_check(activities))
    print_summary(records)

    if problems:
        print(f"[失败] 自检发现 {len(problems)} 个问题，未写盘：")
        for item in problems[:20]:
            print(f"  - {item}")
        return 1

    groups: list[tuple[str, list[dict[str, Any]]]] = [
        ("rhythm.special.jsonl", records[: len(RECIPES)]),
        ("rhythm.user_ad_workday.jsonl", [r for r in records if str(r["id"]).startswith("user_ad_day")]),
        ("rhythm.user_sick_day.jsonl", [r for r in records if r["id"] == "user_sick_day"]),
        ("activity.user_ad.jsonl", activities),
    ]

    if dry_run:
        print("[dry-run] 自检全部通过，未写任何文件。")
        print()
    else:
        ok = True
        for name, items in groups:
            ok = write_jsonl(OUT_DIR / name, items) and ok
        if not ok:
            return 1
    print("下一步（人工确认后跑）：")
    print("  python scripts/life_corpus.py validate data/human_life/_derived --strict")
    print("  python scripts/life_corpus.py coverage data/human_life/_derived")
    print("  python scripts/life_corpus.py report   data/human_life/_derived")
    print()
    print("口径提醒：全部记录 synthetic=true；note 只写 bvid/“用户提供的自述”，不含真人、公司、客户标识。")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口；异常一律兜住，不向外抛。"""
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="corpus_special",
        description="从 B 站 vlog 与用户自述派生特殊日 rhythm / 广告工作日 / 活动（标准库，离线）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印核对与自检结果，不写文件")
    args = parser.parse_args(argv)
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - 约定：异常不外抛
        print(f"[异常] 派生中断：{type(exc).__name__}: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
