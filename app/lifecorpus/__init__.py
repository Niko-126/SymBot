"""人类作息与社交语料管线（life corpus pipeline）。

模块划分：
    app/lifecorpus/schema.py    四种记录的字段定义与逐字段校验（返回错误列表）
    app/lifecorpus/pii.py       手机号 / 身份证 / 邮箱的保守检测与遮掩
    app/lifecorpus/validate.py  JSONL 读取、按文件名推断类型、逐行校验、目录汇总
    app/lifecorpus/coverage.py  覆盖矩阵与缺口清单（24h / 日型 / 活动 / 关系 / 场景 / 轮数）
    scripts/life_corpus.py      命令行入口：validate / coverage / report
    scripts/test_lifecorpus.py  自测脚本（标准库，无 pytest 依赖）

边界：
- 只使用标准库，不联网、不写数据库、不依赖项目其它模块。
- 本包只做"语料是否可用"的静态检查，不负责采集与训练。
﻿​﻿‌​‍‌​​‍‌‌‍‌‌‍‌​‌﻿﻿‌‍‌‌﻿﻿‌​​​‌​﻿"""

from __future__ import annotations

from .coverage import CoverageGap, CoverageReport, build_coverage, format_coverage, load_records
from .pii import PiiFinding, mask_object, mask_text, scan_and_mask, scan_object, scan_text
from .schema import KINDS, FieldError, validate_record
from .validate import DirReport, FileReport, Issue, infer_kind, validate_dir, validate_file

__version__ = "0.1.0"

__all__ = [
    "KINDS",
    "CoverageGap",
    "CoverageReport",
    "DirReport",
    "FieldError",
    "FileReport",
    "Issue",
    "PiiFinding",
    "build_coverage",
    "format_coverage",
    "infer_kind",
    "load_records",
    "mask_object",
    "mask_text",
    "scan_and_mask",
    "scan_object",
    "scan_text",
    "validate_dir",
    "validate_file",
    "validate_record",
    "__version__",
]
