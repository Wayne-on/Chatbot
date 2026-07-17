# Dify DSL 分析

## 1. 输入与结论

- 输入文件：`Logistics Smart Customer Service - EN + Vietnamese + Chinese v2 Lang State.yml`
- DSL 版本：`0.3.0`，应用模式：`advanced-chat`
- 目标区域/渠道：代码中固定为 `VN` / `app`
- 图规模：14 个节点、17 条边
- 模型节点：3 个，均配置为 Azure OpenAI `gpt-5.4`
- 业务调用：没有 HTTP、插件、外部知识库或真实业务 Tool 节点；查询、POD、建单、改址校验和 FAQ 均由 Python Code 节点模拟
- 用户语言：英语、越南语、中文；语言写入会话变量并在仅输入运单号时继承

原图是“LLM 路由 + 确定性解析兜底 + 单次模拟调用 + LLM 润色”的轻量 Demo，并不是完整 SOP 引擎。迁移时需要补上显式步骤、写操作确认、身份校验、幂等和审计。

## 2. 节点与图结构

| ID | 标题 | 类型 | 主要职责 |
| --- | --- | --- | --- |
| `start` | Start | start | 接收 `sys.query`；无自定义开始变量 |
| `llm_understand` | LLM - User Understanding and Routing | llm | 三语意图识别、槽位抽取、历史上下文继承、工具选择 |
| `code_parse` | Parse Routing Result | code | 解析 LLM JSON，正则兜底抽取运单/电话，继承场景与语言，补全缺失槽位 |
| `assign_context_memory` | Memory - Update Current Context | assigner | 覆盖写入运单、电话、场景、语言、业务原因 |
| `if_slot_missing` | Need Slot Filling? | if-else | 缺运单号或场景不清时进入澄清 |
| `answer_clarify` | Reply - Slot Filling / Clarification | answer | 直接输出澄清话术 |
| `if_route_tool` | Tool Routing | if-else | 按四种模拟工具名分流；其余进入 FAQ |
| `code_mock_tools` | Mock Customer Service Tool Dispatcher | code | 模拟轨迹、POD、建单、改址资格校验 |
| `assign_tool_memory` | Memory - Save Tool Result | assigner | 保存本轮工具摘要 |
| `llm_final_reply` | Generate Final Reply with Tool Result | llm | 严格基于工具结果生成对应语言回复 |
| `answer_final` | Final Reply | answer | 输出工具路径回复 |
| `knowledge_policy` | Mock Policy FAQ Retrieval | code | 关键词匹配禁寄、时效、理赔、改址政策 |
| `llm_no_tool_reply` | Knowledge-Based Reply | llm | 严格基于模拟知识生成对应语言回复 |
| `answer_no_tool` | Final Reply - Knowledge-Based | answer | 输出 FAQ 路径回复 |

主路径：

```text
start -> llm_understand -> code_parse -> assign_context_memory
  -> if_slot_missing
     -> answer_clarify
     -> if_route_tool
        -> code_mock_tools -> assign_tool_memory -> llm_final_reply -> answer_final
        -> knowledge_policy -> llm_no_tool_reply -> answer_no_tool
```

`code_parse -> assign_context_memory` 发生在澄清之前，因此缺少运单号时场景和语言仍会被保留，下一轮只输入运单号即可继续。

## 3. 变量清单

### 3.1 开始与系统变量

- `sys.query`：当前用户消息，唯一实际开始输入。
- `country` / `channel`：不是变量；分别在 Prompt/Code 中硬编码为 `VN` / `app`。
- 文件上传配置存在但关闭；没有图片输入分支。

### 3.2 会话变量

| 变量 | 类型 | 初始值 | 写入来源 | 用途 |
| --- | --- | --- | --- | --- |
| `waybill_no` | string | `""` | `code_parse.waybill_no` | 跨轮复用运单号 |
| `phone` | string | `""` | `code_parse.phone` | 保存抽取到的电话；原图未做身份校验 |
| `scenario` | string | `""` | `code_parse.scenario` | 仅输入运单号时继承上一场景 |
| `last_tool_summary` | string | `""` | `code_mock_tools.tool_summary` | 支持后续追问 |
| `last_business_reason` | string | `""` | `code_parse.business_reason` | 保存模型给出的业务原因 |
| `lang` | string | `""` | `code_parse.lang` | `en` / `vi` / `zh` 语言状态 |

### 3.3 临时变量/节点输出

- `llm_understand.text`：JSON 文本。
- `code_parse`：`scenario`、`confidence`、`waybill_no`、`phone`、`lang`、`missing_slots_text`、`tool_name`、`need_human`、`clarify_question`、`business_reason`。
- `code_mock_tools`：`tool_status`、`tool_summary`、JSON 字符串 `tool_result`。
- `knowledge_policy`：`knowledge_text`、`matched_policy`。
- 两个最终 LLM 的 `text`。

DSL 没有 `current_step`、确认上下文、重试次数、最后结构化工具结果或幂等键；这些必须在目标状态模型中补充。

## 4. 意图、分类条件与默认工具

| DSL scenario | 语义/分类提示 | 默认工具 | 是否需要运单号 |
| --- | --- | --- | --- |
| `track` | 查件、包裹在哪里 | `query_track` | 是 |
| `urge_delivery` | 催派送、延误、长期无更新 | `create_ticket` | 是 |
| `signed_not_received` | 显示签收但本人未收到 | `query_pod` | 是 |
| `change_address` | 改地址、改电话、预约派送 | `change_address_check` | 是 |
| `complaint_claim` | 投诉、理赔、破损、遗失 | `create_ticket` | 是 |
| `faq_policy` | 禁寄、限制寄递、时效和规则 | `none` | 否 |
| `unclear` | 问候、过短或无法判断 | `none` | 否，需澄清 |
| `other` | 物流客服范围外 | `none` | 否 |

Prompt 还要求：新信息覆盖旧信息；仅输入运单号时继承上轮场景；多意图选择最新/主要且可执行的场景；情绪强烈、反复不满或赔偿争议时设置 `need_human=true`；不得承诺赔偿、改址或当日送达。

## 5. 参数提取和条件分支

### 5.0 三个 LLM Prompt 清单

| 节点 | System Prompt 的核心约束 | User Prompt 输入 | Memory |
| --- | --- | --- | --- |
| `llm_understand` | 只输出 JSON；理解英/越/中；结合当前消息与历史判断继续、纠正或切换；限定 8 个 scenario 和 5 个 tool name；缺运单号时追问；强情绪/赔偿争议可标记人工；禁止承诺赔偿、改址成功或今日送达；按当前消息决定语言 | 当前问题、固定 `VN/app`、本轮可提取运单/电话、历史运单、历史电话、上一场景、上一语言、上一工具摘要、上一业务原因 | 开启，最近 6 轮；query template 重复注入当前输入和上述历史字段 |
| `llm_final_reply` | 以 `code_parse.lang` 为最高语言依据；先共情、再结果、后建议；只能使用 Tool 结果；失败时说明暂不可用；按签收未收到/催件/改址场景强调对应信息；简洁无技术术语 | 当前问题、当前/历史语言、场景、当前/历史运单、上一工具摘要、工具类型/状态/摘要/完整 JSON 结果 | 开启，最近 6 轮；注入当前输入、历史运单、语言、工具摘要 |
| `llm_no_tool_reply` | 按检测语言回复；只能使用检索知识和通用客服措辞；知识不足时说明需核实；地区/渠道差异以当地或人工为准 | 当前问题、检测/历史语言、固定 `VN/app`、场景、历史运单、模拟三语知识、命中政策类型 | 开启，最近 6 轮；注入当前输入、历史运单、场景、语言 |

三个模型都配置 `completion_params={}`、chat mode、Azure OpenAI provider 和 `gpt-5.4`。原图没有结构化输出 Schema 节点，只由 `code_parse` 尝试截取首个 JSON 对象并在失败时使用空对象兜底。

### 5.1 运单号

- LLM 抽取优先，其次当前消息正则，最后会话历史。
- 当前消息正则：`JT` + 8–13 位数字，或 10–15 位纯数字。
- 模拟 Tool 校验：`JT` + 8–13 位数字，或 8–15 位纯数字。
- 两处规则不一致：8–9 位纯数字可以通过 Tool 校验，却不能由解析正则自动提取。
- 仅由运单号组成的消息会继承旧 `scenario` 和旧 `lang`。

### 5.2 电话

- 先去除运单样式，再抽取 7–15 位电话数字。
- 电话保存到会话状态并可能传给建单，但没有任何场景把电话列为缺失槽位，也没有验证收件人身份。

### 5.3 分支

1. `missing_slots_text` 包含 `waybill_no`：澄清。
2. `scenario == unclear`：澄清。
3. `tool_name` 为 `query_track` / `query_pod` / `create_ticket` / `change_address_check`：进入模拟工具。
4. 其他值：进入模拟 FAQ。

`need_human` 虽被模型和解析器产出，但图中没有使用它，实际不存在转人工分支。

## 6. 各 SOP 的原始完整行为

### 6.1 查询轨迹

1. 识别 `track`。
2. 缺运单号则询问并等待下一轮。
3. 调用 `query_track` 模拟函数。
4. 根据运单末位映射运输中、派送中、已签收、延误或异常。
5. LLM 基于工具摘要生成三语回复；单次执行完成。

### 6.2 催派送

1. 识别 `urge_delivery`。
2. 缺运单号则询问并等待下一轮。
3. 直接调用 `create_ticket`，没有先查轨迹、没有用户确认。
4. 返回确定性 `MOCK...` 工单号；单次执行完成。

### 6.3 签收未收到

1. 识别 `signed_not_received`。
2. 缺运单号则询问并等待下一轮。
3. 调用 `query_pod`；内部先生成轨迹状态。
4. 若不是已签收，返回无 POD 和当前状态；若已签收，返回签收时间、签收人“security/front desk”和电子签收记录。
5. 只建议查找或后续创建调查单，原图不会继续收手机号、校验身份或建单。

### 6.4 修改地址/预约派送

1. 识别 `change_address`。
2. 缺运单号则询问并等待下一轮。
3. 调用 `change_address_check`。
4. 运输中或延误：允许尝试，后续需要新地址、收件人姓名、电话；派送中/已签收/异常：拒绝或建议转人工。
5. 原图不收集新地址，也不执行修改。

### 6.5 投诉/理赔

1. 识别 `complaint_claim`。
2. 缺运单号则询问并等待下一轮。
3. 直接调用 `create_ticket`，没有材料收集、身份校验或用户确认。
4. 返回模拟预受理工单号，不承诺赔偿结果。

### 6.6 FAQ/规则

1. 识别 `faq_policy` 或走无工具默认分支。
2. 关键词匹配 `prohibited_items`、`delivery_time`、`claim`、`change_address` 或 `general`。
3. 返回内嵌三语政策文本，再由 LLM 按当前语言改写；单次执行完成。

### 6.7 不清楚/范围外

- `unclear` 使用固定三语分类澄清问题，等待下一轮。
- `other` 进入通用 FAQ 文本；没有明确拒答或转人工。

## 7. 模拟接口输入、输出与异常

统一调度输入：`waybill_no`、`phone`、`scenario`、`tool_name`、`user_query`、`business_reason`；内部固定 `country=VN`、`channel=app`。

| 工具 | 关键输入 | 输出字段 |
| --- | --- | --- |
| `query_track` | waybill、country、channel、scenario | status、current_node、exception、can_urge、eta、events |
| `query_pod` | waybill、country、channel | has_pod、signed_time、signer、pod_type、latest_event |
| `create_ticket` | waybill、phone、scenario、query、reason | ticket_id、ticket_type、current_status、expected_followup |
| `change_address_check` | waybill、country、channel、query | can_change、current_status、reason、required_info、next_step |

异常只有 `INVALID_WAYBILL_NO` 和 `UNKNOWN_TOOL`；没有超时、重试、鉴权、幂等查询、审计或统一 `trace_id`。所有模拟成功/失败最终被序列化为字符串交给 LLM。

## 8. 回复、前端结构和等待点

- 固定开场白为越南语，声明支持三语及查件、催派送、签收未收到、改址、投诉/理赔。
- 4 条建议问题分别覆盖英/越/中查件和中文签收未收到。
- 固定澄清话术有三语版本：缺运单号、意图不清、通用补充信息。
- 最终回复要求“先共情、再结果、后建议”，只能依据工具或知识，不得添加事实。
- DSL 没有卡片节点、JSON 前端协议或结构化 UI；只有字符串 Answer。`tool_result` 是内部 JSON 字符串，不直接作为卡片返回。
- 明确等待下一轮的只有缺运单号和意图不清。其他路径一次完成；原图没有确认等待状态。

## 9. 迁移时修正的缺口

1. 用 `ConversationState` 显式记录意图、步骤、状态、槽位、待确认动作和最后工具结果。
2. 保留确定性解析和路由，模型仅增强歧义理解，不能生成业务事实。
3. 催件、投诉、改址改为确认后写入，并携带幂等键。
4. 签收未收到增加轨迹前置校验、手机号后四位验证和确认。
5. `need_human` 接入真实状态分支。
6. 将 Code 模拟拆成 Pydantic Tool + 可替换 Backend Adapter。
7. API 返回结构化状态与动作字段，保留三语回复。
