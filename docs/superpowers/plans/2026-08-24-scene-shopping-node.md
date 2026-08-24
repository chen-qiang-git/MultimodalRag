# 场景导购独立节点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让宽泛场景需求走独立的计划、跨品类选品与专用回答链路。

**Architecture:** Governor 产生 `scene_search` 后，新的场景计划节点把场景转为具名任务槽位。检索节点按任务槽位独立召回并去重，场景回答器按任务而非普通排名生成最终文本，并提供不依赖模型的确定性降级。

**Tech Stack:** Python 3、FastAPI、LangGraph、Pydantic、DashScope、现有 pgvector/JSON 商品仓库。

---

### Task 1: 定义场景计划数据结构与规则目录

**Files:**
- Create: `app/scene/plan.py`
- Modify: `app/schemas/agent_state.py`

- [ ] **Step 1: 定义不可变场景任务结构**

```python
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
        return {
            "scene": self.scene,
            "title": self.title,
            "intro": self.intro,
            "notes": list(self.notes),
            "tasks": [
                {
                    "key": task.key,
                    "label": task.label,
                    "sub_categories": list(task.sub_categories),
                    "keywords": list(task.keywords),
                }
                for task in self.tasks
            ],
        }
```

- [ ] **Step 2: 建立 outdoor 与 travel 的任务计划**

```python
SCENE_PLANS = {
    "outdoor": ScenePlan(
        "outdoor", "爬山准备清单", "按脚下保护、收纳防护和补水补能来挑。", (),
        (
            SceneTask("footwear", "脚下保护", ("徒步鞋",), ("徒步鞋", "登山鞋", "防滑运动鞋")),
            SceneTask("carry", "收纳防护", ("背包", "防晒"), ("登山背包", "防晒霜")),
            SceneTask("energy", "补水补能", ("功能饮料",), ("功能饮料", "电解质饮料")),
        ),
    ),
    "travel": ScenePlan(
        "travel", "旅行准备清单", "按防晒、轻装出行和续航保障来挑。", (),
        (
            SceneTask("sun", "防晒防护", ("防晒",), ("防晒霜", "防晒喷雾")),
            SceneTask("carry", "轻装出行", ("背包", "短袖T恤"), ("旅行背包", "短袖T恤")),
            SceneTask("power", "续航保障", ("移动电源",), ("充电宝", "移动电源")),
        ),
    ),
}
```

- [ ] **Step 3: 在 AgentState 中加入状态字段**

```python
scene_plan: dict = Field(default_factory=dict)
scene_task_queries: list[dict] = Field(default_factory=list)
```

- [ ] **Step 4: 静态验证与提交**

Run: `D:\soft2\python\python.exe -m py_compile app\scene\plan.py app\schemas\agent_state.py`

Expected: exit code 0.

Commit: `feat: 定义场景导购计划与任务槽位`

### Task 2: 接入场景计划节点与工作流

**Files:**
- Create: `app/workflow/scene_plan.py`
- Modify: `app/workflow/graph.py`

- [ ] **Step 1: 实现场景计划节点**

```python
async def scene_plan_node(state: AgentState) -> AgentState:
    plan = get_scene_plan(state.slots.scene)
    if not plan:
        return state
    state.scene_plan = plan.to_payload()
    state.scene_task_queries = [
        {"key": task.key, "label": task.label, "query": build_task_query(state.user_input, task),
         "sub_categories": list(task.sub_categories)}
        for task in plan.tasks
    ]
    return state
```

- [ ] **Step 2: 在主图中插入节点**

```python
if state.intent == "scene_search":
    return "scene_plan"

wf.add_node("scene_plan", scene_plan_node)
wf.add_edge("scene_plan", "retrieval")
```

- [ ] **Step 3: 静态验证与提交**

Run: `D:\soft2\python\python.exe -m py_compile app\workflow\scene_plan.py app\workflow\graph.py`

Expected: exit code 0.

Commit: `feat: 接入场景导购计划节点`

### Task 3: 按任务槽位检索并保证覆盖

**Files:**
- Modify: `app/workflow/nodes.py`

- [ ] **Step 1: 为场景任务增加独立召回分支**

```python
async def _retrieve_scene_tasks(state: AgentState) -> list[dict]:
    selected, seen = [], set()
    for task in state.scene_task_queries:
        hits = await _text.search_chunked(task["query"], top_k=DEFAULT_TOP_K)
        hit = next((item for item in hits if item.get("sub_category") in task["sub_categories"]
                    and item.get("product_id") not in seen), None)
        if hit:
            hit["scene_task"] = {"key": task["key"], "label": task["label"]}
            selected.append(hit)
            seen.add(hit["product_id"])
    return selected
```

- [ ] **Step 2: 在 retrieval_node 中优先使用场景任务结果**

```python
if state.intent == "scene_search" and state.scene_task_queries:
    results = await _retrieve_scene_tasks(state)
else:
    results = await _text.search_chunked(
        query,
        top_k=DEFAULT_TOP_K,
        category=slots.category,
        sub_category=slots.sub_category,
        price_max=budget.max,
        price_min=budget.min,
        candidate_ids=state.candidate_ids or None,
    )
```

- [ ] **Step 3: 保留现有子类轮转作为未知场景兼容路径**

只对 `scene_task_queries` 为空的场景执行 `_diversify_by_subcategory`。

- [ ] **Step 4: 静态验证与提交**

Run: `D:\soft2\python\python.exe -m py_compile app\workflow\nodes.py`

Expected: exit code 0.

Commit: `feat: 按场景任务覆盖跨品类选品`

### Task 4: 增加场景专用回答器和确定性降级

**Files:**
- Create: `app/prompts/scene_response_prompt.py`
- Modify: `app/workflow/nodes.py`

- [ ] **Step 1: 建立场景回答提示词构建器**

```python
def build_scene_response_prompt(plan: dict, items: list[dict]) -> str:
    return f"""你是松仔。基于以下场景计划和商品证据生成准备清单。
场景：{plan['title']}
商品证据：{json.dumps(items, ensure_ascii=False)}
每项必须输出任务标签、完整商品名、价格和场景理由；只引用证据中的商品属性。"""
```

- [ ] **Step 2: 建立确定性场景回答**

```python
def _scene_template_answer(plan: dict, items: list[dict]) -> str:
    lines = [f"{plan['title']}，松仔按下面几件帮你配："]
    for item in items:
        task = item.get("scene_task", {}).get("label", "实用装备")
        lines.append(f"{task}：{item['title']}（{item['brand']}）¥{int(item['price'])}")
    return "\n".join(lines)
```

- [ ] **Step 3: 在 response_node 和 stream_response 选择场景回答器**

```python
if state.intent == "scene_search" and state.scene_plan:
    prompt = build_scene_response_prompt(state.scene_plan, top)
```

模型异常、空内容或未引用候选商品时调用 `_scene_template_answer`。

- [ ] **Step 4: 静态验证与提交**

Run: `D:\soft2\python\python.exe -m py_compile app\prompts\scene_response_prompt.py app\workflow\nodes.py`

Expected: exit code 0.

Commit: `feat: 增加场景导购专用回答`

### Task 5: 透传场景计划与追踪信息

**Files:**
- Modify: `app/api/stream.py`
- Modify: `android-client/app/src/main/java/com/omnicart/agent/core/model/RecommendResponse.kt`

- [ ] **Step 1: 在 SSE 结果载荷加入场景计划**

```python
"scene_plan": result.scene_plan,
"scene_task_queries": result.scene_task_queries,
```

- [ ] **Step 2: 在 Android 响应模型保留可选场景字段**

```kotlin
@SerializedName("scene_plan")
val scenePlan: Map<String, Any?>? = null,
```

- [ ] **Step 3: 静态差异检查与提交**

Run: `git diff --check`

Expected: exit code 0.

Commit: `feat: 透传场景导购计划信息`
