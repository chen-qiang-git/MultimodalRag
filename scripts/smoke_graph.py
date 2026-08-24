# -*- coding: utf-8 -*-
"""手动冒烟脚本 — 跑通 v2.0 主图（Mock 模式下无需真实 API/DB）。

用法：python scripts/smoke_graph.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.schemas.agent_state import AgentState  # noqa: E402
from app.workflow.graph import run_agent  # noqa: E402


async def main():
    queries = [
        ("推荐500以内的蓝牙耳机", {}),
        ("推荐护肤品", {}),
        ("你好呀", {}),
        ("我想买双耐克", {}),
        ("刚才那款能带上飞机吗", {
            "last_products": [{
                "product_id": "p_digital_028",
                "title": "Anker 安克 20000mAh 大容量充电宝 22.5W快充 PD双向 兼容苹果安卓 可上飞机",
                "brand": "Anker安克",
                "price": 149,
            }],
        }),
        ("刚才那款能带上飞机吗", {}),  # 无快照 → 追问指代
    ]
    for q, snapshot in queries:
        out = await run_agent(AgentState(user_input=q, context_snapshot=snapshot))
        top1 = out.ranked_items[0].get("title") if out.ranked_items else None
        print("Q:", q)
        print("  intent:", out.intent, "| sub:", out.slots.sub_category,
              "| budget:", out.slots.budget.max, "| clar:", out.needs_clarification)
        print("  candidates:", len(out.ranked_items), "| top1:", top1)
        print("  final:", (out.final_response or "(empty)").replace("\n", " ")[:120])
        print()


if __name__ == "__main__":
    asyncio.run(main())
