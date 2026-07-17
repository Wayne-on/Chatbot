# Logistics Customer Service LangGraph

这是一个从 Dify DSL 重新组织而来的三语（中文、越南语、英语）物流客服 Demo。当前分支使用外层 LangGraph 编排确定性节点和模型节点，显式步骤机管理多轮业务状态，Pydantic Tools 负责校验，Adapter 负责最终业务调用。`main` 分支保留功能相同的 DeepAgents 实现，便于对比。

当前 Demo 使用 `DeepSeek-V4-Flash` 驱动 LangGraph 的语义节点，业务接口继续使用 `MockBackend`。Graph 明确执行 `load_session -> deterministic_router -> semantic_router（按需） -> resolve_decision -> execute_business`；语义节点结合最近六组真实对话和业务 State 输出主/次意图、关系和槽位。明确的运单号、手机号后四位、确认或取消走确定性快速路径。多个诉求共用已验证槽位，并由一个活动场景加待办队列顺序完成。Tool 执行完成后，DeepSeek 再结合当前问题、历史、对应 Skill 和真实结果生成自然回复。业务写操作始终由状态机守门，模型不可直接执行。

## 已实现

- 查轨迹、查包裹体积
- 催派送：先查轨迹，确认后幂等提交
- 签收未收到：查轨迹、手机号后四位校验、确认后建投诉单
- 修改地址：资格检查、收新地址、确认后幂等提交
- 投诉/理赔预受理和工单查询
- VN/app FAQ、三语回复、取消、场景切换、转人工
- 最近六组对话记忆、会话级运单/工单历史、问候/夸奖/短反应和历史统计
- 受控多意图：主意图 + 最多三个待办意图、共享运单号、只读任务顺序合并、写任务分别确认
- 模型断网自动降级与短暂熔断、会话隔离、显式状态、写操作审计、敏感字段防泄漏
- Mock/HTTP Backend 互换边界
- 单元测试、多轮测试、API 测试和 DSL 回归案例

## 环境

- Python 3.11（`pyproject.toml` 支持 3.11–3.13）
- [uv](https://docs.astral.sh/uv/)

如果尚未安装 uv：

```bash
python -m pip install --user uv
```

## 启动

```bash
uv sync
uv run uvicorn customer_service_agent.main:app --reload
```

仓库本地已经有被 `.gitignore` 排除的 `.env`。在其他环境部署时，才需要从 `.env.example` 创建配置并通过 Secret Manager 或环境变量注入密钥。

如果用户级脚本目录尚未加入 `PATH`，可将上面的 `uv` 替换为 `python -m uv`。

服务地址默认是 `http://127.0.0.1:8000`。健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

浏览器聊天测试界面：

```text
http://127.0.0.1:8000/
```

页面内置查件、签收未收到、催件、改址和越南语 FAQ 快捷场景，也会根据当前步骤显示测试运单号、手机号和确认按钮。开发接口文档仍位于 `http://127.0.0.1:8000/docs`。

聊天示例：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "session-001",
    "user_id": "user-001",
    "message": "显示签收了但没收到",
    "language": "zh-CN",
    "user_credential": "short-lived-token"
  }'
```

继续同一业务流程必须复用同一个 `session_id`。`user_credential` 只传给本轮 Adapter，不进入状态、Prompt、日志或响应。

## 测试和质量检查

```bash
uv run pytest
uv run ruff check .
```

代表性测试路径：

```text
显示签收了但没收到
-> JT123456785       # Mock 中末位 5/6 表示 delivered
-> 1234              # Mock 身份校验通过值
-> 确认
-> 返回真实 Mock Tool 生成的 CMP... 工单号
```

Mock 轨迹沿用 DSL 的末位映射：0–2 运输中、3–4 派送中、5–6 已签收、7–8 延误、9 异常。

## 配置模型

`.env`：

```env
MODEL_NAME=deepseek-v4-flash
DEEPSEEK_API_KEY=
MODEL_BASE_URL=https://api.deepseek.com
MODEL_TEMPERATURE=0
MODEL_TIMEOUT=30
MODEL_MAX_RETRIES=2
MODEL_FAILURE_COOLDOWN_SECONDS=30
MODEL_THINKING_ENABLED=false
MODEL_ROUTING_MODE=new_scene
```

高置信度单一业务短语、明确槽位、确认/取消、问候和夸奖由 Graph 走确定性路径并跳过模型节点；多个意图、否定/纠正、歧义表达、复杂追问或场景切换进入 `semantic_router`。`MODEL_ROUTING_MODE=ambiguous_only` 可进一步限制普通模型路由，但多意图与语义冲突仍优先请求模型。客服路由关闭 DeepSeek 默认思考模式以降低延迟；连接失败后按 `MODEL_FAILURE_COOLDOWN_SECONDS` 暂停重试，清晰的多意图按用户提及顺序安全推进，带否定或冲突的表达保守澄清。

模型客户端只在 Agent 层初始化。语义节点使用从 Skills 提炼的紧凑场景目录，生成最终回复时只读取当前场景的完整 `SKILL.md`。`ConversationCheckpointer` 是唯一持久会话状态源，Graph 的单轮运行状态只保存请求、规则决策、模型决策和最终响应；需要语义理解时显式传入最近消息及脱敏业务摘要。模型只输出受 Pydantic 约束的计划和 Tool 建议，不直接获得任何业务 Tool；所有查询和写入都由服务层验证后唯一执行，写操作还必须经过确认、业务复核、幂等与审计路径。

## 配置真实业务接口

```env
BUSINESS_BACKEND=http
BUSINESS_API_BASE_URL=https://internal-business-api.example
BUSINESS_SERVICE_TOKEN=secret-from-secret-manager
BUSINESS_QUERY_MAX_RETRIES=2
```

`HttpBackend` 当前使用以下约定路径，接入时只需在 Adapter 层按内部契约调整：

- `GET /v1/shipments/{waybill}/tracking`
- `GET /v1/shipments/{waybill}/volume`
- `POST /v1/shipments/{waybill}/verify-receiver`
- `POST /v1/delivery-followups`
- `POST /v1/complaints`
- `GET /v1/complaints/{ticket_id}`
- `GET /v1/shipments/{waybill}/address-change-eligibility`
- `POST /v1/shipments/{waybill}/address-changes`
- `GET /v1/knowledge/search`
- `POST /v1/human-transfers`
- `GET /v1/idempotency/{key}`（写超时后的结果核对）

查询类调用按配置有限重试。写操作不会自动重复；发生超时时只查询幂等结果。

## 真实接口接入清单

1. 确认各 endpoint、请求/响应字段、错误码、超时和 SLA。
2. 将平台密钥放入 Secret Manager/环境变量，不注入模型上下文。
3. 确认用户短期凭证传递方式和运单级授权规则。
4. 让业务系统最终校验用户权限、运单状态及写操作约束。
5. 确认所有写接口支持幂等键和按幂等键查询结果。
6. 对齐审计字段：`user_id`、`waybill_no`、`request_id`、`action`、`timestamp`。
7. 将内存 Checkpointer 替换为 Redis/PostgreSQL，并确定 TTL 与保留策略。
8. 接入正式 FAQ/RAG 或 MCP，替换 DSL 内嵌示例政策。
9. 接入结构化日志/指标平台，补齐模型 Token、P50/P90/P95、Tool 成功率、兜底率和重复工单率。
10. 完成鉴权、限流、熔断、压测、灰度与数据合规评审。

## 目录

```text
docs/                         DSL 分析、迁移矩阵、架构和 API 契约
src/customer_service_agent/
  agent.py                    模型客户端与 LangGraph 统一入口
  workflow.py                 StateGraph 节点、条件边和单轮运行状态
  state.py                    ConversationState 和 Checkpointer
  router.py                   三语确定性路由与槽位解析
  skills/*/SKILL.md           独立客服场景 SOP
  tools/                      Pydantic Tool 输入与统一执行边界
  adapters/                   MockBackend / HttpBackend
  services/                   多轮步骤机、场景和回复服务
  api/                        FastAPI 接口
tests/                        单元、多轮、集成和 DSL 回归测试
```

## 文档

- [DSL 分析](docs/dsl-analysis.md)
- [迁移矩阵](docs/migration-matrix.md)
- [架构说明](docs/architecture.md)
- [LangGraph 与 DeepAgents 实现对比](docs/implementation-comparison.md)
- [API 契约](docs/api-contract.md)
- [已知问题和待确认项](docs/open-questions.md)

原始 DSL 只作为分析输入，项目没有修改该文件。
