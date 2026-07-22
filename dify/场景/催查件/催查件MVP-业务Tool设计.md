# 催查件 MVP｜业务 Tool 设计

## 1. 结论

Π/Dify 不建议直接编排多个内部原始接口。MVP 对 DSL 暴露两个稳定的场景级业务 Tool：

| Tool | 类型 | DSL 调用时机 | 内部职责 |
| --- | --- | --- | --- |
| query_expedite_snapshot | 只读 | 获取运单号后 | 查运单详情；未命中时查订单兜底；有效后查对外轨迹；排序并取最新轨迹；标准化状态；生成卡片数据 |
| create_expedite_work_order | 写操作 | 已代收且用户否认本人申请自取，完成资料收集并明确确认后 | 注入工单类型、真实 IM sessionId、渠道、鉴权、幂等与审计后创建工单 |

不作为 MVP Tool：

- “签收未收到”跨 SOP 跳转：本期由 DSL 固定话术兜底。
- 手机号关联运单查询：本期用户直接在对话中提供运单号。
- 内部轨迹、时效和工单类型查询：属于后续“签收未收到”流程。
- 卡片渲染：属于 Π/IM 输出协议；Tool 只返回卡片数据。

## 2. 为什么使用场景级 Tool

原始接口分别属于多个内部服务，并存在主查/兜底、取消订单过滤、状态归一化和错误分类。若直接放进 DSL：

- DSL 会绑定内部服务名、响应结构和状态中文文案。
- 鉴权信息可能进入平台配置或模型上下文。
- “接口失败”“无有效运单”“无轨迹”容易被混为同一种结果。
- 轨迹状态映射会散落在多个 DSL 版本中。
- 工单类型编码、sessionId 转换、幂等和审计难以统一。

场景级 Tool 适配层应是业务事实的唯一来源，DSL 只消费稳定枚举。

## 3. query_expedite_snapshot

### 3.1 入参

    {
      "waybillNo": "JT123456781",
      "requestId": "optional-trace-id"
    }

### 3.2 内部调用链

1. 调用 POST /waybillouterapi/common/get。
2. 未查到运单详情时，调用 POST /order-query-api/waybill/queryOrderInfo。
3. 过滤已取消订单；仍未命中则返回 INVALID_WAYBILL。
4. 有效运单调用 POST /ops/pod/opsPodTracking/outer/qos/keywordList。
5. 对轨迹按扫描时间倒序确认最新一条，不依赖上游列表顺序。
6. 返回标准化状态、最新轨迹、可展示事件和卡片数据。

### 3.3 标准出参

    {
      "success": true,
      "statusCode": "IN_TRANSIT",
      "waybillNo": "JT123456781",
      "latestTrack": {
        "rawStatus": "运送中",
        "scanTime": "2026-07-22 09:30:00",
        "scanTypeName": "到件",
        "networkCode": "001",
        "networkName": "上海转运中心",
        "customerTracking": "快件已到达上海转运中心"
      },
      "events": [],
      "cardData": {},
      "errorCode": null,
      "retryable": false,
      "traceId": "..."
    }

### 3.4 状态枚举

| statusCode | 判定 |
| --- | --- |
| INVALID_WAYBILL | 运单详情和订单兜底均未查到，或订单已取消 |
| TRACK_EMPTY | 运单有效，但轨迹明细为空 |
| IN_TRANSIT | 最新状态为“运送中”“已揽件”“留仓件” |
| OUT_FOR_DELIVERY | 最新状态为“派送中”“派件中” |
| SELF_PICKUP | 最新状态为“已代收” |
| SIGNED | 最新状态为“已签收” |
| UNKNOWN | 有轨迹，但原始状态未命中已知映射 |
| SYSTEM_ERROR | 任一必要接口超时、失败或返回结构不完整 |

SYSTEM_ERROR 不得降级为 TRACK_EMPTY 或 INVALID_WAYBILL。

## 4. create_expedite_work_order

### 4.1 DSL 入参

    {
      "waybillNo": "JT123456786",
      "customerPhone": "13800138000",
      "problemDescription": "并非本人要求自取，希望尽快派送",
      "conversationId": "Pi/Dify conversation id",
      "requestId": "idempotency seed"
    }

### 4.2 适配层补充字段

以下内容不得由模型生成：

- firstTypeCode
- secondTypeCode
- 真实 Long 类型 sessionId
- sessionChannelSourceCode
- customerType
- 业务标签
- 鉴权头、服务 Token
- 幂等键和审计字段

适配层需要将 Π/Dify 的 conversationId 映射为真实 IM sessionId。若无法映射，应返回失败，不得使用假 ID 创建工单。

### 4.3 标准出参

    {
      "success": true,
      "status": "SUCCESS",
      "requestId": "...",
      "workOrderId": null,
      "message": "工单创建成功",
      "errorCode": null,
      "retryable": false,
      "traceId": "..."
    }

原接口只返回 Boolean，适配层应至少生成可追踪的 requestId；若后端能返回工单号，应补充 workOrderId。

### 4.4 写操作约束

- DSL 必须先向用户展示脱敏手机号和问题描述。
- 只有用户明确回复“确认”后才调用。
- “取消”“否”或含糊回答不得调用。
- Tool 必须支持幂等；网络超时后先查询幂等结果，不自动重复创建。
- data=false、空响应或异常均视为失败。

## 5. 当前仍需业务确认

1. 中国运单号的正式格式规则。
2. 订单“已取消”的准确状态码。
3. 轨迹 details 是否保证最新记录在首条。
4. 原始状态完整枚举及大小写/空格规则。
5. 已代收问题对应的一级、二级工单类型编码。
6. Π 会话 ID 到真实 IM Long sessionId 的映射方式。
7. Tool 的网关地址、鉴权、超时、重试和限流约定。
8. 轨迹卡片的 Π/IM 输出协议。
9. 正式客服话术及 SLA 承诺边界。

## 6. Π/Dify 节点绑定方式

当前主文件 `中国物流智能客服-Demo.yml` 保留两个可运行的 Code 接入位，没有硬编码 `type: tool` 节点。原因是自定义 API Tool 的 `provider_id` 由具体 Π 租户在导入 OpenAPI 后生成，直接把其他环境的 provider ID 写进 DSL 会造成导入失败或节点失效。

正式联调顺序：

1. 在目标 Π 租户导入 `催查件MVP-业务Tool.openapi.yaml`，配置真实网关与认证。
2. 在催查件查询分支新增 `query_expedite_snapshot` Tool 节点：
   - `waybillNo` 绑定 `code_parse.waybill_no`。
   - `requestId` 绑定平台 trace/request id；没有独立 trace id 时可临时使用 `sys.conversation_id`，但服务端仍应生成唯一请求 ID。
3. 保留一个确定性结果适配 Code，把 Tool 响应转换为主 DSL 当前使用的输出：`normalized_status`、`tool_status`、`tool_summary`、`tool_result`、`tracking_card_json`、`flow_reply`、`next_step`。
4. 在工单分支新增 `create_expedite_work_order` Tool 节点：
   - `waybillNo` 绑定 `code_parse.waybill_no`。
   - `customerPhone` 绑定 `code_parse.phone`。
   - `problemDescription` 绑定 `code_parse.problem_description`。
   - `conversationId` 绑定 `sys.conversation_id`。
   - `requestId` 必须使用每次确认操作的幂等 ID，不能只使用固定会话 ID。
5. 工单 Tool 后增加确定性结果适配：只有 `success=true` 才允许回复“已创建”；超时、`data=false`、空响应或映射不到真实 IM sessionId 时均回复失败，不得由模型补写成功结果。

`provider_id`、认证密钥和内部 Token 不进入模型提示词、会话变量或仓库 DSL。
