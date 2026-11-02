"""打印模型切换规则与当前配置，用于核对"到底什么时候换模型"。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import db, runtime
from app.mind import models

rt = runtime.init()
print("=== 当前设置 ===")
print(models.current_settings())
print("\n=== 路由表（混合模式）===")
for task, key in models.TASK_ROUTE.items():
    spec = models.BY_KEY[key]
    print(f"  {task:<16} -> {spec.style}（{spec.name}）")

print("\n=== 决策顺序 ===")
print("  1) deep_think 开 且任务是对话类 -> 知性沉着（DeepSeek-V4-Pro）")
print("  2) mix_mode 开 -> 按 TASK_ROUTE 分配")
print("  3) 否则 -> 用户选的风格（单模型）")

print("\n=== 各场景实际选择 ===")
rows = []
combos = [(False, False, "gentle"), (False, False, "crisp"), (True, False, "gentle"), (False, True, "gentle")]
for deep, mix, style in combos:
    db.kv_set("deep_think", "1" if deep else "0")
    db.kv_set("mix_mode", "1" if mix else "0")
    db.kv_set("style_pref", style)
    for task in ("chat_default", "chat_emotional", "chat_playful", "summary", "letter"):
        spec = models.pick(task)
        rows.append((deep, mix, style, task, spec.style, spec.name))
print(f"{'深思':<6}{'混合':<6}{'风格':<8}{'任务':<16}{'选中风格':<10}模型")
for r in rows:
    print(f"{str(r[0]):<6}{str(r[1]):<6}{r[2]:<8}{r[3]:<16}{r[4]:<10}{r[5]}")
