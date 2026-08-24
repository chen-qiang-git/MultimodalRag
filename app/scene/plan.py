"""规则驱动的场景导购计划。

这里的计划只描述真实库存可检索的任务槽位；不让模型凭空决定要推荐哪些品类。
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SceneTask:
    key: str
    label: str
    sub_categories: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class ScenePlan:
    scene: str
    title: str
    intro: str
    notes: tuple[str, ...]
    tasks: tuple[SceneTask, ...]

    def to_payload(self) -> dict:
        return asdict(self)


SCENE_PLANS: dict[str, ScenePlan] = {
    "outdoor": ScenePlan(
        scene="outdoor",
        title="爬山准备清单",
        intro="按脚下保护、收纳防护和补水补能来挑。",
        notes=("优先确认天气、路线难度和补给条件。",),
        tasks=(
            SceneTask("footwear", "脚下保护", ("徒步鞋",), ("徒步鞋", "登山鞋", "防滑")),
            SceneTask("carry", "收纳防护", ("背包", "防晒"), ("登山背包", "防晒霜")),
            SceneTask("energy", "补水补能", ("功能饮料",), ("功能饮料", "电解质饮料")),
        ),
    ),
    "travel": ScenePlan(
        scene="travel",
        title="旅行准备清单",
        intro="按防晒、轻装出行和续航保障来挑。",
        notes=("出行前请按目的地天气和行李规则确认容量与规格。",),
        tasks=(
            SceneTask("sun", "防晒防护", ("防晒",), ("防晒霜", "防晒喷雾")),
            SceneTask("carry", "轻装出行", ("背包", "短袖T恤"), ("旅行背包", "短袖T恤")),
            SceneTask("power", "续航保障", ("移动电源",), ("充电宝", "移动电源")),
        ),
    ),
}


def get_scene_plan(scene: str | None) -> ScenePlan | None:
    return SCENE_PLANS.get(scene or "")


def build_task_query(user_input: str, task: SceneTask) -> str:
    """保留用户原始场景，同时注入任务关键词提高该槽位的召回准确度。"""
    return " ".join(part for part in (user_input.strip(), *task.keywords) if part)
