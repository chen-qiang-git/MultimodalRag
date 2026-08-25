# 旅行天气导购 Agent 设计

## 目标

将“去某地旅行，带什么/买什么”作为现有购物 Agent 内的独立旅行天气导购链路处理。该链路先获得可验证的高德天气事实，再只从本项目商品库的真实子类中规划多品类购物任务并检索商品，最终输出带天气依据的旅行准备清单。

## 触发与边界

- 仅当 Governor 判定为 `scene_search`、场景为 `travel` 且能从用户输入解析出目的地时进入。
- 未给出目的地时不调用天气服务，保留现有旅行场景导购。
- 已明确具体商品、品牌、购物操作、单品追问不进入该 Agent。
- 未给出日期时查询目的地未来 4 天预报；用户给出日期时只使用高德预报覆盖范围内的日期。
- 指定日期超出预报范围时不伪造天气，回退为普通旅行导购并明确天气不可用。

## 架构

```text
Governor
  → TravelWeatherAgent
      → destination/date resolver
      → AMap weather MCP
      → AMap REST fallback
  → TravelShoppingPlanner
      → catalog taxonomy snapshot
      → LLM task JSON validation
  → TravelTaskRetrieval
      → per-task multi-category recall
      → ID de-duplication
  → Reranker / Evidence / Decision
  → TravelWeatherResponse
  → Guard
```

`TravelWeatherAgent` 是 LangGraph 中的独立节点；它属于主购物 Agent，不暴露为独立 API，也不替换现有普通商品推荐流程。

## 天气 MCP 与密钥

- 采用高德 MCP：`uvx amap-mcp-server`，环境变量为 `AMAP_MAPS_API_KEY`。
- 参考 `D:\traStar\backend\app\agents\trip_planner_agent.py` 的 MCP 调用模式；Rewrite-RAG 自己实现轻量 MCP 客户端，不引入参考项目的完整 `hello_agents` 框架。
- Key 从 `D:\traStar\.env` 读取后写入 Rewrite-RAG 已被 Git 忽略的 `.env`，不输出、不提交。
- MCP 工具失败、超时或返回不可解析数据时，使用高德天气 REST API 作为同一 Key 的降级路径。
- MCP 与 REST 都失败时，`weather_status=unavailable`，后续仍可走普通旅行导购，但不得给出任何天气结论。

## 旅行选品规划

节点每次从商品仓库动态生成 `category → sub_category` 目录，并作为唯一允许的选品空间写入 Planner 提示词。Planner 输入为：

- 目的地、已解析日期范围、规范化逐日天气、温度、风力；
- 用户预算、排除项和偏好；
- 当前仓库真实品类/子类目录。

Planner 只返回结构化任务，不返回商品：

```json
[
  {
    "sub_category": "防晒",
    "query": "三亚 晴热 防晒",
    "weather_reason": "未来预报有晴热日间时段"
  }
]
```

代码必须二次校验 `sub_category` 是否存在于目录，丢弃库外任务、空任务及重复任务。每轮最多生成五项任务，保证至少两类商品（库存允许时）。

## 检索、证据与回答

- 每项任务按天气词、目的地、用户约束和合法子类分别检索；商品 ID 去重后再进入现有精排与决策。
- 每一件最终商品携带 `travel_task` 与 `weather_reason`，使 trace、SSE 和回复都可回溯。
- 专用提示词先概述已验证的天气，再按任务输出库内商品；商品名、价格和属性必须来自商品证据。
- 回答不能把天气条件伪造成商品属性，也不能推荐未被召回的商品。
- 模型输出缺少天气依据、任务标签或商品证据时，使用确定性旅行天气模板。

## 状态与客户端

`AgentState` 增加 `travel_weather`、`travel_plan` 与 `travel_weather_status`。SSE 最终事件透传这些字段，Android 保持现有商品卡片展示，只补充可选天气计划字段供后续展示与排障。

## 可观测性与降级

trace 按顺序记录：目的地/日期解析、天气来源（MCP/REST/不可用）、规范化天气摘要、合法任务数、每个任务的召回数和最终商品数。降级不会阻断普通旅行推荐，也不会编造天气数据。
