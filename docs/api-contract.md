# API 契约

## `POST /v1/chat`

请求：

```json
{
  "session_id": "session-001",
  "user_id": "user-001",
  "message": "我的快递显示签收了但没收到",
  "language": "zh-CN",
  "user_credential": "short-lived-token",
  "request_id": "optional-client-request-id"
}
```

`user_credential` 只在本次请求内传给需要鉴权的 Adapter，不会进入会话状态或响应。

响应：

```json
{
  "reply": "请提供您的运单号。",
  "status": "collecting",
  "current_intent": "delivered_not_received",
  "current_step": "waiting_waybill",
  "action_required": "provide_waybill_no",
  "data": {},
  "trace_id": "..."
}
```

状态值：`idle`、`collecting`、`processing`、`waiting_confirmation`、`completed`、`failed`、`transfer`、`cancelled`。

典型 `action_required`：`provide_waybill_no`、`provide_phone_last4`、`provide_new_address`、`provide_complaint_description`、`confirm_action`、`contact_human`。

## `GET /health`

进程存活即返回 `{"status":"ok"}`。

## `GET /ready`

配置和 Backend 已初始化即返回 `{"status":"ready"}`；真实 Backend 配置不完整时返回 503。

## 错误

- 请求 Schema 不合法：FastAPI 422。
- 可恢复业务错误：HTTP 200，响应状态为 `collecting` 或 `failed`，并给出安全话术。
- 未处理服务错误：HTTP 500，不返回凭证、密钥或内部响应体。

