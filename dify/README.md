# 中国物流智能客服 DSL

本目录是 Π 平台 DSL 的正式开发区。当前唯一主 DSL：

- `中国物流智能客服-Demo.yml`

当前 v0.3 使用“确定性快速路径 + 分层语义理解 + 会话任务状态机 + 场景 SOP + 独立 Tool/RAG 接入位”。`scenario` 是二级意图，`level3_intent` 是三级意图；一个会话只有一个活动任务，其他明确诉求进入 `pending_tasks_json`。

当前三级意图只是根据已有 Demo 能力建立的过渡白名单，不等于生产的 66 个正式三级意图。拿到业务清单后，必须完成一对一映射、SOP/Tool 归属和表驱动回归集。

两个越南 DSL 仅作实现方式参考，不是中国正式导入文件。

## 主处理链路

    用户输入
      -> 确定性快速路径
         -> 纯运单号 / 当前等待的手机号 / 确认 / 取消 / 是否 / 继续：不调模型
         -> 自由口语 / 否定 / 纠正 / 切换 / 多意图：调用分层语义模型
      -> Code 白名单校验二/三级意图和槽位
      -> 激活一个任务，其他诉求按顺序进待办队列
      -> 当前场景 SOP / RAG / Tool
      -> 保存 current_step、槽位、待办和脱敏 Tool 摘要
      -> 当前任务完成后，提示回复“继续”处理队首任务

静态工作流没有 Agent Loop，所以当前不在同一轮无限调度多个 Tool。多意图采用“逐轮安全执行”，写操作每项单独确认。

## 催查件 MVP 链路

    统一中文意图识别
      -> 槽位与安全校验
      -> delivery_followup
      -> query_expedite_snapshot
      -> INVALID_WAYBILL / TRACK_EMPTY / IN_TRANSIT / OUT_FOR_DELIVERY
         / SELF_PICKUP / SIGNED / UNKNOWN / SYSTEM_ERROR

- `SELF_PICKUP`：询问是否愿意本人取件；回答“否”后收集手机号和问题描述，展示脱敏确认信息，明确确认后才调用写 Tool。
- `SIGNED`：询问是否已收到；回答“未收到”时使用固定话术兜底，暂不进入未建设的“签收未收到”SOP。
- 系统异常与无轨迹、无有效运单严格分开，不得互相降级。

## Tool 接入

建议对 DSL 暴露两个场景级 Tool：

| Tool | 类型 | 内部职责 |
| --- | --- | --- |
| `query_expedite_snapshot` | 只读 | 运单详情、订单兜底、取消订单过滤、对外轨迹、最新轨迹排序、状态归一化和卡片数据 |
| `create_expedite_work_order` | 写操作 | 注入工单类型、真实 IM `sessionId`、渠道、鉴权、幂等和审计后创建工单 |

主 DSL 目前用两个 Code 节点保留接入位：

- `Tool接入位-查询催查件快照`：返回明确标记的开发预览数据，用于验证完整分支。
- `Tool接入位-创建催查件工单`：正式 Tool 未绑定前安全失败，绝不伪造建单成功。

真实服务可用后，在 Π 平台把这两个节点替换为对应自定义 Tool，并保持下游标准输出。契约见：

- `场景/催查件/催查件MVP-业务Tool设计.md`
- `场景/催查件/催查件MVP-业务Tool.openapi.yaml`

## 本地验证

    python dify/validate_dsl.py

校验包括 YAML、节点/边/变量引用、所有 Code 节点语法、快速路径拓扑、二/三级意图契约、多意图队列、写操作确认、催查件八种状态、无效运单恢复以及重复确认。

开发预览状态规则：

| 运单后缀 | 状态 |
| --- | --- |
| `00` | `INVALID_WAYBILL` |
| 末位 `0` | `TRACK_EMPTY` |
| 末位 `1–3` | `IN_TRANSIT` |
| 末位 `4–5` | `OUT_FOR_DELIVERY` |
| 末位 `6` | `SELF_PICKUP` |
| 末位 `7–8` | `SIGNED` |
| 末位 `9` | `UNKNOWN` |
| `99` | `SYSTEM_ERROR` |

这些规则只用于开发预览，不得作为生产业务规则。

## 真实接入前仍需确认

- 中国运单号正式格式和已取消订单状态码。
- 完整轨迹状态枚举和最新轨迹排序规则。
- 已代收问题对应的一级、二级工单类型编码。
- Π `conversation_id` 到真实 IM Long `sessionId` 的映射。
- Tool 网关、鉴权、超时、重试、幂等和限流。
- Π/IM 轨迹卡片协议和最终客服话术。

## 当前能力边界

- 尚未导入正式 66 个三级意图及它们的标准说法、槽位、SOP 和 Tool 映射。
- `query_expedite_snapshot` 仍是固定时间的开发预览数据；`create_expedite_work_order` 仍安全失败，不会伪造真实工单。
- 其他业务 Tool 仍是通用 Mock；`change_address_check` 只做资格判断，没有执行真实改址。
- FAQ 仍是模拟政策文本，不是真实知识库召回，也没有来源、版本、权限和无答案评估。
- “签收未收到”完整 SOP、工单进度 Tool、真实转人工仍未接入。
- 快速路径使用 Variable Aggregator 汇合分支；导入 Π 租户前需确认当前平台版本支持该节点。
