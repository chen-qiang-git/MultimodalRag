# Rewrite-RAG — 豆仔 v2.0 重写项目

基于 LangGraph 的多模态电商导购助手（豆仔）v2.0 重写代码库。

- 方案文档：[豆仔v2.0重构方案_差距清单与决策记录.md](豆仔v2.0重构方案_差距清单与决策记录.md)
- 数据库：直连原 PostgreSQL + pgvector（**原库原表**，决策 D6a），不跑迁移 / 索引 / 数据同步
- Android 端契约保持不变，API 兼容层适配
- 当前阶段：Agent 核心 v1 完成——新 State Schema、Governor 子图（P1/P3）、主链路
  （retrieval → rerank(D4 性价比混合) → evidence → decision(Top-3) → response → guard），
  P9 direct_answer 直答节点已接入；Web 测试壳可用（多轮 / 指代消解 / P9 均验证通过）

## 目录结构

```
app/
├─ core/            # 配置 / 数据库会话 / Redis（仅缓存） 负责配置和数据库
├─ models/          # SQLAlchemy ORM 模型（对齐原库原表：products / conversations / user_preference_entries / 向量表）
├─ schemas/         # 基本的数据结构定义 全局状态：AgentState
            agent_state ──→ governor / workflow / guard          （新核心，全链路）
            product ──────→ repositories / retrieval / scoring    （商品数据流）
            visual ───────→ scoring（未来 visual_node P4）         （多模态输入）
            decision_result / evidence_metrics ──→ decision       （评分输出与证据质量）
            
├─ governor/        # Governor 子图：preresolve + profile + nodes（P1 编译/校验/P3 澄清）

├─ workflow/        # 主图：graph.py + nodes.py + direct_answer.py（P9 直答节点）
├─ prompts/         # P1 Governor / P3 Clarification / P7 Response / P8 Chitchat / P9 Direct Answer
├─ retrieval/       # 语义检索 + chunk 检索（检索） 
├─ rerank/          # Qwen Rerank + 性价比混合分 D4（blender.py）
├─ decision/        # 规则词表 + V4 评分（已移植）
├─ verification/    # 护栏 ResponseGuard（已适配新 State）
├─ repositories/    # PG 仓库（原库原表直连） 负责数据访问
├─ model_gateway/   # Qwen 系列模型网关（已移植）
├─ observability/   # Trace 收集负责链路追踪 
├─ services/        # 压缩 P10 / 画像 P11（已移植）
└─ api/             # Web 测试壳：SSE /api/recommend/stream + 静态测试页（进程内会话记忆）

data/               # eval / golden 回归基线
ecommerce_agent_dataset/  # 数据集 JSON（105 件，本地兜底检索用）
tests/              # 已移植的材料模块单测
scripts/smoke_graph.py  # 手动冒烟：跑通主图（Mock 或真实 API）
static/dialogue_test.html  # Web 测试页（槽位可视化 + 多轮对话）
run.py              # 启动 Web 测试台：python run.py [port]（默认 8007）
requirements.txt / .env.example  # 依赖与配置模板（.env 本地不入库）
```

## 快速自测

```bash
python scripts/smoke_graph.py
```

脚本依次跑 推荐500以内的蓝牙耳机 / 推荐护肤品 / 你好呀 / 我想买双耐克 / 刚才那款能带上飞机吗，
覆盖 search / 澄清 / chitchat / brand / direct_answer 五类意图，
打印 intent、槽位、候选数、Top-1 与最终回复。

## 本地 Android 联调

Rewrite-RAG 运行在 Windows 宿主机；PostgreSQL、pgvector 与 Redis 保持在现有 Docker 容器中。无需为 Rewrite-RAG 再建立容器。

1. 将 `.env.example` 复制为本地 `.env`，填入 Qwen 密钥，以及 Docker 已发布到 Windows 的 PostgreSQL / Redis 端口。
2. 确保 `.env` 中 `OMNICART_HOST=0.0.0.0`、`OMNICART_PORT=8006`。这让模拟器和同一局域网内的真机可以访问宿主机服务。
3. 将 `android-client/local.properties.example` 复制为 `android-client/local.properties`，设置 Android SDK 路径和 `BASE_URL`。

`BASE_URL` 选择规则：

- Android 模拟器使用 `http://10.0.2.2:8006/`；`10.0.2.2` 会映射到 Windows 宿主机。
- 物理 Android 设备使用 `http://<Windows-宿主机局域网-IP>:8006/`；手机和电脑需在同一局域网，并允许 Windows 防火墙放行该端口。
- Android 中的 `localhost` 与 `127.0.0.1` 指向设备自己，不能用于访问 Windows 后端。

本地联调为 HTTP 明文开发模式；交付或公网部署时必须改用 HTTPS，并收紧 `network_security_config.xml`。

## Web 测试台

```bash
python run.py            # 使用 .env 的 OMNICART_HOST / OMNICART_PORT，热重载开启
```

浏览器打开 `http://127.0.0.1:<OMNICART_PORT>/`，左侧多轮对话，右侧实时展示每轮 Governor 槽位。
会话记忆在进程内（重启清空），支持追问、指代消解、P9 直答（如"刚才那款能带上飞机吗"）。
关闭热重载：`python run.py --no-reload`
