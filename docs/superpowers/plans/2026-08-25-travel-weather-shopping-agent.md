# 旅行天气导购 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在松仔购物 Agent 内增加旅行天气导购节点：先查可验证的高德天气，再基于库内真实品类规划并召回多类商品。

**Architecture:** Governor 仍负责识别 `scene_search/travel`，图路由仅在旅行场景含目的地时进入 `travel_weather` 节点。节点通过高德 MCP 获取天气，失败时降级同一高德 Key 的 REST 接口；随后由 Planner 在实时商品目录白名单中生成任务，复用既有场景任务检索、精排、证据和商品卡片链路。天气不可用、日期超出预报范围或未识别目的地时，不编造天气，回退普通旅行场景导购。

**Tech Stack:** Python 3、FastAPI、LangGraph、Pydantic、httpx、MCP Python SDK（stdio，`uvx amap-mcp-server`）、Android Kotlin 序列化。

---

## 文件边界

| 文件 | 责任 |
| --- | --- |
| `app/core/config.py` | 高德 MCP、REST 的超时与开关配置。 |
| `requirements.txt` | 添加固定主版本的 MCP Python SDK。 |
| `app/schemas/agent_state.py` | 目的地/日期槽位和天气导购状态契约。 |
| `app/travel/weather.py` | 天气数据模型、高德 MCP 调用、REST 降级及预报规范化。 |
| `app/travel/planner.py` | 实时商品目录、受白名单约束的旅行任务规划及确定性降级。 |
| `app/workflow/travel_weather.py` | 旅行请求解析、天气获取、任务规划、trace 写入的独立图节点。 |
| `app/workflow/graph.py` | 将有目的地的旅行场景路由至专用节点。 |
| `app/workflow/nodes.py` | 复用任务检索并保存 `travel_task`；选择专用回复。 |
| `app/prompts/travel_weather_response_prompt.py` | 天气事实、任务、商品证据绑定的回答提示词和模板。 |
| `app/api/stream.py` | SSE/普通响应透传天气导购字段。 |
| `android-client/app/src/main/java/com/omnicart/agent/core/model/RecommendResponse.kt` | 可选接收天气导购字段，不影响现有商品卡片。 |
| `.env`（忽略） | 仅由用户授权的本地密钥迁移填入 `AMAP_MAPS_API_KEY`，绝不提交。 |

### Task 1: 建立配置与状态契约

**Files:**
- Modify: `requirements.txt`
- Modify: `app/core/config.py`
- Modify: `app/schemas/agent_state.py`
- Modify: `app/governor/nodes.py`

- [ ] **Step 1: 增加 MCP 运行依赖和最小配置项**

在 `requirements.txt` 增加与 stdio 客户端 API 对应的主版本约束：

```text
mcp>=1.27,<2
```

在 `Settings` 中添加以下字段，全部有可安全启动的默认值；Key 保持空字符串，不能硬编码：

```python
amap_maps_api_key: str = ""
amap_mcp_enabled: bool = True
amap_mcp_command: str = "uvx"
amap_mcp_server: str = "amap-mcp-server"
amap_weather_timeout_seconds: float = 8.0
amap_rest_base_url: str = "https://restapi.amap.com/v3/weather/weatherInfo"
```

`Settings` 必须继续通过当前 `.env` 加载逻辑读取 `AMAP_MAPS_API_KEY`，不可打印该值。

- [ ] **Step 2: 扩展 Governor 与工作流状态**

在 `SlotSchema` 中新增仅表达解析结果的字段：

```python
travel_destination: Optional[str] = None
travel_start_date: Optional[str] = None  # ISO YYYY-MM-DD
travel_end_date: Optional[str] = None    # ISO YYYY-MM-DD
```

在 `AgentState` 的场景导购字段后增加：

```python
travel_weather_status: Literal["not_requested", "available", "unavailable", "out_of_range"] = "not_requested"
travel_weather: Dict[str, Any] = Field(default_factory=dict)
travel_plan: Dict[str, Any] = Field(default_factory=dict)
```

更新 Governor 的结构化输出提示词/解析白名单，使 LLM 可输出上述三项；并保持所有非旅行意图为 `None`。在 `rewrite_extract_node` 的规则后处理增加：只有 `intent == "scene_search"`、`scene == "travel"` 且 `travel_destination` 非空时，才能保留该目的地；其余意图清空三个旅行槽位，避免旧上下文误触发天气服务。

- [ ] **Step 3: 静态检查并提交契约变更**

不执行测试（按项目约定）。只执行：

```powershell
uv run python -m py_compile app/core/config.py app/schemas/agent_state.py app/governor/nodes.py
git diff --check
git add requirements.txt app/core/config.py app/schemas/agent_state.py app/governor/nodes.py
git commit -m "feat: 增加旅行天气导购状态契约"
```

预期：编译命令无输出且退出码为 0；`git diff --check` 无输出。

### Task 2: 实现高德天气客户端及可靠降级

**Files:**
- Create: `app/travel/__init__.py`
- Create: `app/travel/weather.py`

- [ ] **Step 1: 定义无业务副作用的天气模型**

在 `app/travel/weather.py` 定义以下 Pydantic 模型和纯函数：

```python
class TravelWeatherDay(BaseModel):
    date: str
    day_weather: str = ""
    night_weather: str = ""
    day_temp: str = ""
    night_temp: str = ""
    day_wind: str = ""
    day_power: str = ""

class TravelWeatherSnapshot(BaseModel):
    destination: str
    source: Literal["mcp", "rest"]
    days: list[TravelWeatherDay]
    summary: str

def select_requested_days(days: list[TravelWeatherDay], start_date: str | None, end_date: str | None) -> list[TravelWeatherDay]:
    """无日期时返回前四天；有日期时只返回闭区间内的预报。"""

def summarize_weather(days: list[TravelWeatherDay]) -> str:
    """仅由天气 API 字段组成中文摘要，不推断未返回的气象事实。"""
```

日期筛选为空时调用方必须得到 `out_of_range`，不得以其它日期替代。

- [ ] **Step 2: 实现 MCP 优先、REST 降级的单一入口**

实现 `AMapWeatherClient`，唯一公有方法如下：

```python
class AMapWeatherClient:
    async def forecast(
        self, destination: str, start_date: str | None, end_date: str | None
    ) -> tuple[TravelWeatherSnapshot | None, str]:
        """返回 (snapshot, status)，status 为 available/unavailable/out_of_range。"""
```

MCP 实现使用 stdio 会话且将 Key 仅传入子进程环境：

```python
params = StdioServerParameters(
    command=settings.amap_mcp_command,
    args=[settings.amap_mcp_server],
    env={**os.environ, "AMAP_MAPS_API_KEY": settings.amap_maps_api_key},
)
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        # 从名字含 weather 的工具中选择；未找到视为 MCP 失败。
```

解析工具返回的 JSON 文本；只接受包含逐日 `casts`（或等价规范化字段）的结果。MCP 抛异常、超时、工具不存在、结果不可解析或没有日期时，记录 warning 级日志（不含 Key），再请求：

```python
response = await client.get(
    settings.amap_rest_base_url,
    params={
        "key": settings.amap_maps_api_key,
        "city": destination,
        "extensions": "all",
        "output": "JSON",
    },
    timeout=settings.amap_weather_timeout_seconds,
)
```

REST 只接受 `status == "1"` 且 `forecasts[0].casts` 为非空列表；映射 `date/dayweather/nightweather/daytemp/nighttemp/daywind/daypower`。Key 缺失时直接返回 `(None, "unavailable")`，不发起网络请求。两个来源均失败也只返回不可用，不向用户输出错误详情。

- [ ] **Step 3: 静态检查并提交天气客户端**

```powershell
uv run python -m py_compile app/travel/__init__.py app/travel/weather.py
git diff --check
git add app/travel/__init__.py app/travel/weather.py
git commit -m "feat: 接入高德旅行天气客户端"
```

预期：只有静态语法检查；不启动 MCP、不调用高德网络、不跑测试。

### Task 3: 生成受库内品类约束的旅行选品任务

**Files:**
- Create: `app/travel/planner.py`
- Modify: `app/workflow/scene_plan.py`

- [ ] **Step 1: 从仓库生成实时子类白名单**

在 `app/travel/planner.py` 实现：

```python
def build_catalog_taxonomy(products: list[dict]) -> dict[str, list[str]]:
    """返回排序、去空值后的 category -> sub_category，不接受写死的品类表。"""

def flatten_allowed_sub_categories(taxonomy: dict[str, list[str]]) -> set[str]:
    return {sub for subs in taxonomy.values() for sub in subs}
```

复用当前商品仓库已有的“全量商品/目录”读取入口；若没有可复用入口，在仓库实现最小 `list_products_for_planning()`，只返回 `category` 和 `sub_category`，不把全商品正文塞入提示词。

- [ ] **Step 2: 定义 Planner 的严格输出及校验**

定义：

```python
class TravelShoppingTask(BaseModel):
    sub_category: str
    query: str
    weather_reason: str

class TravelShoppingPlan(BaseModel):
    destination: str
    weather_summary: str
    tasks: list[TravelShoppingTask]
```

实现 `async def build_travel_shopping_plan(destination: str, weather_summary: str, days: list[TravelWeatherDay], taxonomy: dict[str, list[str]], budget: BudgetSchema, exclusions: list[str], preferences: list[str]) -> TravelShoppingPlan`。提示词必须写明：

```text
你是旅行选品规划器。只能从“允许子类”选择 sub_category；只输出 JSON 数组；
不得输出商品名、品牌、价格、库外品类，也不得把天气描述成商品属性。
每项字段固定为 sub_category、query、weather_reason；最多 5 项。
```

调用现有 LLM 网关的低温结构化生成通道。模型文本先提取 JSON 数组，再逐项执行：`sub_category in allowed_sub_categories`、`query.strip()`、`weather_reason.strip()`、`sub_category` 去重。最多保留五项。若库中有至少两个子类且模型有效任务少于两个，补足确定性任务：根据天气摘要的“雨/风/晴/热/冷/雪”关键词选择名称或商品关键词匹配的真实子类；仍不足时从目录字母序前两项补足。所有回退任务的 `weather_reason` 必须写“按旅行通用准备补充；未将其当作天气事实”。

- [ ] **Step 3: 让现有场景节点可继续服务非天气旅行**

保留 `scene_plan_node` 原有逻辑不变，仅确保它不会覆盖已经由专用 Agent 写入的 `state.scene_plan`、`state.scene_task_queries`：

```python
if state.travel_plan:
    return state
```

这样未带目的地的旅行请求继续使用 `app/scene/plan.py` 的静态旅行清单，带目的地请求改由新 Planner 产出动态多品类任务。

- [ ] **Step 4: 静态检查并提交 Planner**

```powershell
uv run python -m py_compile app/travel/planner.py app/workflow/scene_plan.py
git diff --check
git add app/travel/planner.py app/workflow/scene_plan.py
git commit -m "feat: 增加库内品类约束的旅行选品规划"
```

### Task 4: 增加独立 TravelWeatherAgent 节点并接入图路由

**Files:**
- Create: `app/workflow/travel_weather.py`
- Modify: `app/workflow/graph.py`

- [ ] **Step 1: 实现旅行专用节点**

在 `app/workflow/travel_weather.py` 提供以下函数：

```python
def should_use_travel_weather(state: AgentState) -> bool:
    return (
        state.intent == "scene_search"
        and state.slots.scene == "travel"
        and bool(state.slots.travel_destination)
    )

async def travel_weather_node(state: AgentState) -> AgentState:
    destination = state.slots.travel_destination
    if not destination:
        state.travel_weather_status = "not_requested"
        return state
    snapshot, status = await AMapWeatherClient().forecast(
        destination, state.slots.travel_start_date, state.slots.travel_end_date
    )
    state.travel_weather_status = status
    return state
```

节点按以下顺序执行：

1. 读取 Governor 已解析的目的地和 ISO 日期；为空时直接返回，`travel_weather_status="not_requested"`。
2. 调用 `AMapWeatherClient.forecast`。
3. `available` 时调用 `build_catalog_taxonomy` 与 `build_travel_shopping_plan`，将结果写入：

```python
state.travel_weather = snapshot.model_dump()
state.travel_plan = plan.model_dump()
state.scene_plan = {
    "scene": "travel",
    "title": f"{destination}旅行准备",
    "intro": snapshot.summary,
    "notes": ["天气来自高德预报；商品仅来自当前商品库"],
}
state.scene_task_queries = [task.model_dump() for task in plan.tasks]
```

4. `unavailable` 或 `out_of_range` 时仅设置状态及结构化原因，不写天气摘要、不写任务，由后续 `scene_plan_node` 执行普通旅行导购。
5. 添加四条可审计 trace：`resolve_travel_request`、`fetch_travel_weather`、`build_travel_plan`（成功时）、`fallback_travel_scene`（降级时）。每条 `input_summary/output_summary` 禁止含 API Key。

- [ ] **Step 2: 变更图路由，保证专用节点仅替换目标分支**

修改 `_router`：

```python
if state.intent == "scene_search":
    return "travel_weather" if should_use_travel_weather(state) else "scene_plan"
```

添加节点和边：

```python
wf.add_node("travel_weather", travel_weather_node)
# governor 条件映射增加 "travel_weather": "travel_weather"
wf.add_edge("travel_weather", "scene_plan")
```

专用节点总是接回 `scene_plan`：成功时该节点是 no-op，失败时则生成普通场景计划；两条路径均继续既有 `retrieval -> reranker -> evidence -> decision`，不创建独立 HTTP 接口。

- [ ] **Step 3: 静态检查并提交节点路由**

```powershell
uv run python -m py_compile app/workflow/travel_weather.py app/workflow/graph.py
git diff --check
git add app/workflow/travel_weather.py app/workflow/graph.py
git commit -m "feat: 接入旅行天气导购工作流节点"
```

### Task 5: 将天气任务带入检索、回复和 SSE/Android 契约

**Files:**
- Modify: `app/workflow/nodes.py`
- Create: `app/prompts/travel_weather_response_prompt.py`
- Modify: `app/api/stream.py`
- Modify: `android-client/app/src/main/java/com/omnicart/agent/data/remote/dto/RecommendResponse.kt`

- [ ] **Step 1: 保留任务原因并复用任务检索**

在 `_retrieve_scene_tasks` 写入商品字段时，兼容旅行任务：

```python
item["scene_task"] = {
    "key": task.get("key") or task.get("sub_category"),
    "label": task.get("label") or task.get("sub_category"),
}
if task.get("weather_reason"):
    item["travel_task"] = {
        "sub_category": task["sub_category"],
        "weather_reason": task["weather_reason"],
    }
```

保留现有按任务独立检索、商品 ID 去重行为；不要改变普通搜索、购物车或非旅行场景逻辑。

- [ ] **Step 2: 新增不会编造事实的专用回复提示词与模板**

在 `app/prompts/travel_weather_response_prompt.py` 定义：

```python
def build_travel_weather_response_prompt(
    weather: dict, plan: dict, items: list[dict]
) -> str:
    return "旅行天气导购提示词"

def travel_weather_template_answer(
    weather: dict, plan: dict, items: list[dict]
) -> str:
    return "旅行天气导购模板回复"
```

提示词应强制模型使用 `weather.summary` 和逐日预报作为唯一天气来源，并要求每个商品对应 `travel_task.weather_reason`。模板固定为：天气摘要一段、商品清单（任务标签 + 库内商品名 + 价格 + 原因）和一句预算/偏好追问。只遍历 `items`，绝不根据任务凭空造商品。

在 `response_node` 和 `stream_response` 中优先判定：

```python
is_travel_weather = (
    state.travel_weather_status == "available"
    and bool(state.travel_weather)
    and bool(state.travel_plan)
)
```

为真时使用专用提示词；模型输出为空、过短或未覆盖候选商品任务标签时，使用 `travel_weather_template_answer`。否则保持既有场景提示词和普通提示词分支。

- [ ] **Step 3: 透传 API 与 Android 可选字段**

在 `_recommendation_payload` 及非流式 `/guide` 返回体增加：

```python
"travel_weather_status": state.travel_weather_status,
"travel_weather": state.travel_weather or None,
"travel_plan": state.travel_plan or None,
```

Android DTO 增加可空、默认值字段（名称与 JSON snake_case 映射一致）：

```kotlin
@SerialName("travel_weather_status") val travelWeatherStatus: String = "not_requested",
@SerialName("travel_weather") val travelWeather: JsonObject? = null,
@SerialName("travel_plan") val travelPlan: JsonObject? = null,
```

不改变 Android 商品卡片、流式 Markdown 与历史会话 UI；本次仅保证字段能安全接收，后续 UI 可读取。

- [ ] **Step 4: 静态检查并提交端到端契约变更**

```powershell
uv run python -m py_compile app/workflow/nodes.py app/prompts/travel_weather_response_prompt.py app/api/stream.py
git diff --check
git add app/workflow/nodes.py app/prompts/travel_weather_response_prompt.py app/api/stream.py android-client/app/src/main/java/com/omnicart/agent/core/model/RecommendResponse.kt
git commit -m "feat: 输出旅行天气导购结果"
```

预期：只做语法、空白差异检查；遵循当前约定，不运行 Python/Android 测试、不启动后端、Docker、MCP 或 Android。

### Task 6: 本地密钥迁移与交付检查

**Files:**
- Modify: `D:/knowledge/Rewrite-RAG/.env`（已忽略，不纳入 Git）
- Modify: `docs/superpowers/specs/2026-08-25-travel-weather-shopping-agent-design.md`（仅当实施中接口与计划有实质差异）

- [ ] **Step 1: 按用户授权迁移高德 Key**

只从 `D:/traStar/.env` 读取 `AMAP_MAPS_API_KEY`，将其写入 Rewrite-RAG 的 `.env`；若目标已存在该键则只替换该单行。全过程不得在终端、日志、Git diff、计划或回复中打印 Key。目标 `.env` 必须仍被 `.gitignore` 排除。

- [ ] **Step 2: 进行不联网的交付自检并报告降级语义**

```powershell
git diff --check
git status --short
git log --oneline -5
```

确认内容：工作流只在有目的地旅行场景进入专用节点；无 Key/MCP/REST 失败与日期超范围均回到普通旅行导购；普通搜索、购物操作、现有安卓卡片均无路由改动。报告此次新增提交，但不暂存或提交用户原有图片和无关变更。

## 计划自检

- 规格覆盖：目的地与日期边界由 Task 1/4 实现；MCP 优先与 REST 降级由 Task 2 实现；动态真实品类、多任务约束与白名单校验由 Task 3 实现；多类召回/去重、可验证回复、SSE/Android 字段由 Task 5 实现；密钥不泄露与降级交付由 Task 6 覆盖。
- 类型一致性：任务统一使用 `sub_category/query/weather_reason`；状态统一使用 `not_requested/available/unavailable/out_of_range`；`travel_weather`、`travel_plan` 均为 `dict` 透传。
- 边界：MCP/REST 只在节点执行时调用；没有目的地不会调用天气；日期超范围不替代日期；天气不可用不生成天气事实；Planner 与模板都不产生库外商品。
- 用户约束：所有步骤仅列出静态/差异检查，未安排完整测试、服务启动或设备联调。
