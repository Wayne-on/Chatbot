# 架构说明

```text
FastAPI
  -> CustomerServiceAgent（统一入口）
     -> ConversationService（显式步骤机）
        -> Router（确定性槽位/确认/取消/转人工）
        -> DeepAgent（结合最近六组对话和业务 State 做语义规划）
        -> BusinessTools（Pydantic 输入、统一结果、耗时日志）
           -> BusinessBackend
              -> MockBackend
              -> HttpBackend
        -> DeepSeek 最终回复生成（结合 Skill、历史和真实 Tool 结果）
        -> Checkpointer（当前为内存，可替换 Redis/PostgreSQL）
```

`CustomerServiceAgent` 是 API 的唯一业务入口。当前配置使用 `deepseek-v4-flash`：DeepAgent 对新场景、多意图、语义追问、否定、纠正和切换输出受 Pydantic 约束的计划；明确的运单号、手机号后四位、地址、工单号和写操作确认继续走确定性快速路径。语义计划包含一个当前主意图、最多三个次要意图及其并列/顺序/条件/备选/纠正关系。Tool 返回后，另一次受约束的模型调用结合当前问题、最近对话、对应 Skill 和真实结果生成自然回复。若模型不可用或回复校验失败，系统自动使用安全模板。

没有额外包一层业务 LangGraph。DeepAgents 自身使用 LangGraph runtime，而本项目的客服步骤少且边界固定，由显式步骤机表达更容易测试和审计。

## 状态边界

- 会话键：`session_id`。
- Checkpointer 同时保存显式业务状态、一个活动场景、待办意图队列、最近六组用户/助手消息、会话中出现过的运单号与工单号；队列推进复用已验证的运单号，但场景切换会清理手机号和地址等敏感场景槽位。会话所有者变化时全部清空。
- `user_credential` 只沿当前请求传到 Tool/Adapter，不写入状态、Prompt 或日志。
- 每个会话独立加锁，防止同一会话并发确认造成重复写入。

## 写操作保护

写操作统一采用：收集参数 -> 查询/校验 -> 展示动作 -> `waiting_confirmation` -> 用户确认 -> 再校验 -> 带幂等键执行 -> 审计。查询类失败可有限重试，写操作不自动重试。

## 多意图边界

- 多个只读诉求按计划顺序执行，并在一个回复中返回结构化 `results`。
- 查询与写操作共存时，先完成查询，再进入写操作的参数收集和确认。
- 多个写操作逐个处理，每个操作单独确认并使用独立幂等键。
- “或者”等备选关系不会擅自执行，系统先让用户选择。
- 模型不可用时，清晰的多意图按用户提及顺序降级；包含否定或纠正时只澄清，不依据关键词执行。

## DeepAgents 集成

项目使用官方 `create_deep_agent` 接口。启动时把包内受信任 Skill 文件播种到 `InMemoryStore/StoreBackend`，禁止 Agent 文件写入，不向 Web 服务暴露宿主文件系统。语义规划阶段一次性使用精简 Skill 目录，避免为一次分类触发多轮文件读取；最终回复阶段只加载已选场景的完整 Skill。`ConversationCheckpointer` 是唯一会话状态源；最近消息和脱敏业务摘要在需要理解时显式传给 DeepAgent，不另建第二套隐式模型记忆。项目业务步骤机仍是 Tool 选择、高风险操作和状态跳转的最终守门层。

DeepSeek 连接失败会开启短暂冷却，避免每条消息反复等待网络重试。冷却期间确定性 Router、会话级标识符历史和三语无 Tool 回复继续工作；不会因为“额”、感谢、夸奖或询问历史而累计失败并错误转人工。

## 替换点

- Checkpointer：实现 `ConversationCheckpointer` 协议。
- 业务系统：实现 `BusinessBackend` 协议或配置 `HttpBackend`。
- 模型：通过 `MODEL_NAME`、`MODEL_BASE_URL`、`MODEL_API_KEY` 或 `DEEPSEEK_API_KEY` 切换 OpenAI-compatible 服务。
- FAQ：替换 Backend 的 `retrieve_faq` 为内部知识库/MCP，不改上层 Tool 契约。
