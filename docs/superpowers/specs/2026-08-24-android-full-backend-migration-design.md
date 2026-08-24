# Android 全量后端迁移设计

## 目标

将 `D:\knowledge\Multimodal-RAG\android-client` 迁入 `D:\knowledge\Rewrite-RAG`，并让 Rewrite-RAG 在 Windows 本地运行时完整兼容 Android 既有 HTTP 与 SSE 契约。PostgreSQL 与 Redis 继续由 Docker 容器提供。

## 部署边界

Android 模拟器或真机通过宿主机地址访问本地 FastAPI。Rewrite-RAG 不容器化；其进程监听 `0.0.0.0`。数据库与 Redis 通过环境变量连接 Docker 容器：从 Windows 宿主机使用已发布端口，从未来容器化场景使用服务名。密钥仅从未提交的 `.env` 注入。

## 兼容策略

Android 以现有 `OmniCartApi`、请求/响应模型和 `AgentStreamClient` 为唯一契约来源。Rewrite-RAG 适配该契约，不要求 Android 业务页面改造。推荐 SSE 保持 `token`、`result`、`done` 事件顺序，并持久化 `conversation_id`、消息和上下文快照，以支持重启和多轮恢复。

## 迁移范围

迁移 Android 客户端目录、其 Gradle 配置和本地 BASE_URL 配置；迁移或适配 health、商品、推荐、会话、上传、购物车、结算/订单、鉴权、地址、偏好、记忆和语音接口。商品图片路径与鉴权头行为保持兼容。

## 实施顺序

1. 迁移 Android 客户端目录及其构建配置到 Rewrite-RAG。
2. 调整本地运行入口，支持局域网/模拟器访问，统一由环境变量配置端口和监听地址。
3. 迁移基础仓库、模型、服务与 API 路由；保留当前 Rewrite-RAG Agent 核心。
4. 将现有推荐图接入兼容 SSE 端点与 PostgreSQL 会话持久化。
5. 补齐 Android 依赖的剩余 API，统一在 Rewrite-RAG 的 FastAPI 应用注册。
6. 配置 Android 本地 BASE_URL 指向宿主机地址，进行人工页面联调。

## 非目标

本轮不容器化 Rewrite-RAG，不重写 Android UI，不修改 PostgreSQL schema，不自动执行测试或启动服务。

## 风险与处理

旧后端 API 的实现可能引用旧模块。迁移时优先迁移其依赖闭包，并将重复核心逻辑收敛到 Rewrite-RAG 现有实现。对话状态不得继续存于进程内字典；必须落到既有 PostgreSQL 会话表。任何真实密钥不得进入模板、代码或 Git 提交。
