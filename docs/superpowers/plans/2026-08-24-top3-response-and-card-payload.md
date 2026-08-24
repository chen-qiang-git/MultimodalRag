# Top-3 回复与商品卡片数据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 强制三商品推荐文字完整展示，并让 Android 卡片获得可点击的商品摘要。

**Architecture:** 后端响应节点负责严格的 Top-3 文本覆盖与确定性回退；SSE 适配层负责将已排序商品映射为 Android `Product` 摘要。Android 继续以 `product_id` 导航并调用现有详情接口。

**Tech Stack:** Python、FastAPI、LangGraph、Kotlin/Compose。

---

### Task 1: 强制 Top-3 回复覆盖

**Files:**
- Modify: `app/prompts/response_prompt.py`
- Modify: `app/workflow/nodes.py`

- [x] 将 Prompt 改为有 N 个候选就必须输出 N 个编号商品，每项含名称、价格、匹配理由。
- [x] 将回复校验改为逐个候选验证；任一漏写则调用 `_template_answer(top)`。
- [x] 保持 0、1、2 件候选的原有诚实兜底行为。

### Task 2: 补齐 Android 卡片摘要

**Files:**
- Modify: `app/api/stream.py`

- [x] 在 `_products_payload` 输出 `category`、`sub_category`、`skus`、`description` 和 `image_urls`。
- [x] 保留 `product_id`，使现有卡片点击继续导航到 `/api/products/{product_id}`。
- [x] 静态检查差异并以中文提交说明提交；按用户要求不自动执行测试套件。
