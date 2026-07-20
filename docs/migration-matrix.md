# Dify 到 LangGraph 迁移矩阵

| Dify 原节点/场景 | 当前作用 | LangGraph 目标实现 | 类型 | 是否需多轮状态 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `start` / `sys.query` | 接收用户消息 | `POST /v1/chat` + `ChatRequest` | API | 否 | `session_id` 作为 thread id |
| `llm_understand` | 三语理解、意图/槽位/语言抽取 | `deterministic_router` 条件进入 `semantic_router`；输入最近六组消息和业务 State；输出主意图、待办意图及关系 | 路由 | 是 | 明确槽位走代码，多意图/否定/纠正/切换走模型 |
| `code_parse` | JSON 解析、正则兜底、历史继承 | `router.py` 与 Pydantic 决策模型 | 路由/校验 | 是 | 不信任模型槽位；代码重新校验 |
| `assign_context_memory` | 保存运单、电话、场景、语言 | `ConversationState` + Checkpointer，另保留最近六组对话 | State | 是 | 不保存 credential/token；复用 session_id 时校验 owner |
| `if_slot_missing` | 缺运单或意图不清分支 | `ConversationService` 步骤机 | State | 是 | 支持取消、修改、切换、重复询问 |
| `answer_clarify` | 输出三语澄清 | `ResponseService` 模板 | 回复 | 是 | 返回 `action_required` |
| `if_route_tool` | 四种工具分流 | `resolve_decision -> execute_business`，再由 SceneManager 选择 Skill 与 Tool | 编排 | 是 | 简单路径确定性执行 |
| `code_mock_tools` | 模拟业务调用 | Pydantic Tools -> `BusinessBackend` | Tool/Adapter | 否 | 统一 ToolResult、trace、耗时 |
| `assign_tool_memory` | 保存工具摘要 | `last_tool_result` / `scene_context` | State | 是 | 保存脱敏结构化结果 |
| `llm_final_reply` | 根据工具结果生成三语回复 | LangGraph `response_writer` 结合当前问题、历史、Skill、业务 State 和完整 Tool 数据生成；模板降级 | 回复 | 是 | 校验业务标识符；工具失败不得生成业务事实 |
| `knowledge_policy` | 模拟 FAQ 检索 | `retrieve_faq` + Knowledge Adapter | Tool | 否 | 当前仍为 mock，便于替换 RAG/MCP |
| `llm_no_tool_reply` | 基于知识或上下文回复 | FAQ 检索；问候/夸奖/短反应/历史统计由 conversation 场景处理；可选模型润色 | Skill/回复 | 是 | 模型不可用时仍保留三语确定性回复 |
| `answer_final` / `answer_no_tool` | 字符串输出 | `ChatResponse` | API | 否 | Demo 保留内部状态字段 |
| `track` | 查物流轨迹 | `tracking` Skill + `query_tracking` | Skill/Tool | 可能 | 缺运单号时追问 |
| DSL 未显式区分体积查询 | 无 | `query-package-volume` Skill + `query_package_volume` | Skill/Tool | 可能 | 任务要求新增；规则待业务确认 |
| `urge_delivery` | 直接创建催件单 | `delivery-followup` Skill + `query_tracking` + `urge_delivery` | Skill/Tool | 是 | 新增用户确认和幂等，修正原风险 |
| `signed_not_received` | 查询 POD | `delivered-not-received` Skill + 轨迹 + 身份校验 + 投诉 | Skill/Tool | 是 | 增加手机号后四位和确认 |
| `change_address` | 只校验能否改址 | `change-address` Skill + eligibility + `change_address` | Skill/Tool | 是 | 收新地址并确认后执行 |
| `complaint_claim` | 直接建预受理工单 | `complaint` Skill + `create_complaint` | Skill/Tool | 是 | 收描述、确认、幂等、审计 |
| `faq_policy` | 禁寄/时效/理赔/改址政策 | `faq` Skill + `retrieve_faq` | Skill/Tool | 否 | VN/app 规则仍是 Demo 文本 |
| `unclear` / `other` | 澄清或通用回复 | `conversation` 处理社交和元对话；真正不明意图才进入 `fallback` | Skill | 可能 | 普通寒暄不累计失败；真正连续无法识别才转人工 |
| `need_human`（未接边） | 模型给出转人工建议 | `transfer_to_human` Tool + `transfer` 状态 | Tool/State | 否 | 迁移后真正生效 |
| `query_pod` | 查询签收证明 | `query_tracking` 返回签收摘要 | Tool | 否 | 后续可拆成真实 POD Tool |
| `create_ticket` | 催件/调查/投诉共用建单 | `urge_delivery` / `create_complaint` 分离 | Tool | 否 | 按动作分别校验和审计 |
| `change_address_check` | 检查改址资格 | `check_address_change` | Tool | 否 | 与写工具 `change_address` 分开 |
| DSL 无工单查询 | 无 | `query_complaint` | Tool | 可能 | 任务清单要求新增 |
| DSL 无用户校验 | 无 | `verify_receiver` | Tool | 是 | 最终权限由业务 Backend 决定 |
| DSL 无确认 | 无 | `waiting_confirmation` + `pending_confirmation` | State | 是 | 写操作执行前重新校验 |
| DSL 无幂等/审计 | 无 | idempotency key + Adapter audit | 安全 | 是 | 同一请求不重复写入 |

## 阶段状态

- Phase 1：DSL 分析、工程骨架、状态、Mock Backend。
- Phase 2：tracking、query-package-volume、delivered-not-received、complaint、fallback。
- Phase 3：delivery-followup、change-address、FAQ、工单查询、转人工和结构化返回。
- Phase 4 接口边界已预留；真实持久化、真实接口、生产鉴权/限流/监控仍需环境信息。
