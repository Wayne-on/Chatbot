# LangGraph 与 DeepAgents 实现对比

两条分支保持相同的 API、ConversationState、Router、Skill、Tool、Backend、写操作确认和多意图测试。差异集中在模型与确定性流程如何编排。

| 维度 | `main` DeepAgents | `langgraph-implementation` |
| --- | --- | --- |
| 核心入口 | `create_deep_agent` | 显式 `StateGraph` |
| 流程表达 | Agent runtime + 业务步骤机 | 七个命名节点 + 条件边 + 业务步骤机 |
| 语义输出 | DeepAgents structured response | Chat model structured output |
| Skill 使用 | Agent runtime 能力与精简目录 | Prompt 目录 + 按场景读取完整 Skill |
| Tool 权限 | 不向 Agent 开放业务 Tool | 不向语义节点开放业务 Tool |
| 确定性快速路径 | 服务层判断 | Graph 条件边直接跳过模型节点 |
| 节点可观测性 | 更偏 Agent 调用轨迹 | 每个阶段都有固定节点名 |
| 修改固定流程 | 需要调整服务层 | 可以直接修改节点或边 |
| 自主规划扩展 | 更方便 | 需要显式增加节点和路由 |
| 依赖规模 | DeepAgents 及其 provider extras | LangGraph + langchain-openai |

## 共同安全边界

- 模型只做语义计划和回复生成，不执行真实业务写操作。
- 运单号、手机号后四位、确认/取消等确定性值由代码校验。
- 多意图进入一个活动场景加待办队列，写操作逐个确认。
- `ConversationCheckpointer` 保存最近六组消息和结构化业务 State。
- Tool 层负责参数、权限边界、幂等、超时、审计和真实接口调用。

## 选择建议

- 需要显式流程图、节点级监控、人工审核节点、暂停/恢复或未来复杂分支时，LangGraph 分支更合适。
- 需要更强的自主 Skill 选择、任务分解和 Agent 扩展速度时，DeepAgents 主分支更省代码。
- 当前物流客服的业务效果应由同一组回归案例对比，不应仅凭框架名称判断。
