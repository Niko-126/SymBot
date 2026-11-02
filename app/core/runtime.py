from __future__ import annotations

from app.core import config as config_mod
from app.core import db
from app.mind import llm as llm_mod
from app.persona import load as persona_load
from app.persona.load import Persona
from app.relation import engine


class Runtime:
    def __init__(self) -> None:
        self.cfg = config_mod.get()
        self.default_name = "小涵"
        self.persona: Persona = persona_load.load(self.cfg.persona_file)
        self.default_name = self.persona.name
        db.init(self.cfg.db_file)
        engine.init_traits(self.persona)
        # 预检：base_url 与 model/force_model 是否同源（不联网、零 token）；结果给 /v1/health 看﻿‌​‌‍​‍‍​﻿‌﻿﻿‍‌​‍‍​‌﻿﻿﻿​​﻿​﻿​​‍﻿​﻿
        from app.core import preflight

        self.preflight: dict = preflight.check(self.cfg)
        if not self.preflight.get("ok"):
            import logging

            logging.getLogger("symemory").warning(
                "provider 预检未通过：%s", "；".join(self.preflight.get("warnings") or [])
            )
        # 真实 provider 下默认打开「她的一天」；mock（测试）保持默认关，不改测试基线
        try:
            if not self.cfg.provider.is_mock and not db.kv_get("human_life_enabled", ""):
                db.kv_set("human_life_enabled", "1")
        except Exception:  # noqa: BLE001 - 引导失败不影响启动
            pass
        # 她自己的小号不是"群成员"：清掉历次遗留的自我记录
        try:
            from app.relation import users as users_mod

            users_mod.purge_self(str(getattr(self.cfg.onebot, "self_id", "") or ""))
        except Exception:  # noqa: BLE001 - 清理失败不影响启动
            pass
        mode = db.kv_get("context_mode", "")
        if mode in ("budget", "ratio"):
            self.cfg.context.mode = mode
        self.llm = llm_mod.build(self.cfg, self.persona)
        self.last_prompt: dict = {}
        self.last_meta: dict = {}

    def apply_provider(self, base_url: str, api_key: str, model: str) -> None:
        """在设置里改完 API 后，不重启就生效。"""
        self.cfg.provider.base_url = base_url.strip()
        self.cfg.provider.api_key = api_key.strip()
        self.cfg.provider.model = model.strip()
        self.llm = llm_mod.build(self.cfg, self.persona)

    def reload_persona(self) -> None:
        """导入新的人设后，重新读一遍人物包。"""
        self.persona = persona_load.load(self.cfg.persona_file)
        self.default_name = self.persona.name
        engine.init_traits(self.persona)
        # 会话里的 her_name 是"她现在的名字"。用户还没给她定过名时（name_locked=0），
        # 换 YAML 必须跟着改——否则每轮 loop 都用旧 her_name 覆盖 persona.raw["name"]，
        # 换人设里的 name 等于没换（docs/23 §3.1 第 15 条）。定过名的保持用户的叫法。
        try:
            row = db.one("SELECT name_locked FROM session WHERE id = 1")
            if row is not None and not int(row["name_locked"] or 0):
                db.execute("UPDATE session SET her_name = ? WHERE id = 1", (self.default_name,))
        except Exception:  # noqa: BLE001 - 名字同步失败不该挡住换人设
            pass


_runtime: Runtime | None = None


def init() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime


def get() -> Runtime:
    if _runtime is None:
        return init()
    return _runtime
