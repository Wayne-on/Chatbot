from __future__ import annotations

from typing import Any

TRACKING_STATUS_LABELS = {
    "zh": {
        "in_transit": "运输中",
        "out_for_delivery": "派送中",
        "delivered": "已签收",
        "delayed": "运输延误",
        "exception": "异常处理中",
    },
    "vi": {
        "in_transit": "đang vận chuyển",
        "out_for_delivery": "đang giao hàng",
        "delivered": "đã giao",
        "delayed": "chậm vận chuyển",
        "exception": "đang xử lý bất thường",
    },
    "en": {
        "in_transit": "in transit",
        "out_for_delivery": "out for delivery",
        "delivered": "delivered",
        "delayed": "delayed in transit",
        "exception": "under exception review",
    },
}

TRACKING_NODE_LABELS = {
    "zh": {
        "Shanghai Transfer Center": "上海转运中心",
        "Destination Outlet": "目的地派送网点",
        "East China Sorting Center": "华东分拨中心",
        "Exception Handling Center": "异常处理中心",
    },
    "vi": {
        "Shanghai Transfer Center": "Trung tâm trung chuyển Thượng Hải",
        "Destination Outlet": "Bưu cục phát hàng",
        "East China Sorting Center": "Trung tâm phân loại Hoa Đông",
        "Exception Handling Center": "Trung tâm xử lý bất thường",
    },
    "en": {},
}

TRACKING_ETA_LABELS = {
    "zh": {
        "in_transit": "预计 1–2 天内到达下一站，实际进度以最新轨迹为准。",
        "out_for_delivery": "预计今天派送，具体时间以快递员实际安排为准。",
        "delivered": "系统记录显示派送已经完成。",
        "delayed": "轨迹超过 48 小时没有更新，可以提交催件或核实请求。",
        "exception": "需要网点进一步核实异常原因。",
    },
    "vi": {
        "in_transit": "Dự kiến đến trạm tiếp theo trong 1–2 ngày; tiến độ thực tế theo tracking mới nhất.",
        "out_for_delivery": "Dự kiến giao hôm nay; thời gian cụ thể theo sắp xếp của bưu tá.",
        "delivered": "Hệ thống ghi nhận việc giao hàng đã hoàn tất.",
        "delayed": "Tracking chưa cập nhật hơn 48 giờ; có thể gửi yêu cầu giục giao/xác minh.",
        "exception": "Bưu cục cần xác minh thêm nguyên nhân bất thường.",
    },
    "en": {
        "in_transit": "It should reach the next station within 1–2 days; live tracking remains authoritative.",
        "out_for_delivery": "Delivery is expected today, subject to the courier's actual route.",
        "delivered": "The system records the delivery as completed.",
        "delayed": "Tracking has not updated for more than 48 hours, so a follow-up can be requested.",
        "exception": "The outlet needs to verify the exception further.",
    },
}

TEMPLATES: dict[str, dict[str, str]] = {
    "ask_waybill": {
        "zh": "为了继续处理，请提供运单号。J&T 运单号通常以 JT 开头。",
        "vi": "Để tiếp tục hỗ trợ, vui lòng cung cấp mã vận đơn J&T.",
        "en": "To continue, please provide your J&T waybill number.",
    },
    "invalid_waybill": {
        "zh": "这个运单号 {waybill} 不符合当前查询格式，请核对后发送完整正确的运单号。J&T 运单号通常以 JT 开头，后接 8–13 位数字。",
        "vi": "Mã vận đơn {waybill} không đúng định dạng tra cứu hiện tại. Vui lòng kiểm tra và gửi lại mã J&T đầy đủ, thường bắt đầu bằng JT và theo sau bởi 8–13 chữ số.",
        "en": "Waybill {waybill} does not match the supported format. Check it and send the complete J&T number, normally JT followed by 8–13 digits.",
    },
    "tracking_result": {
        "zh": "我查到运单 {waybill} 目前为{status}，最新节点是{node}。{eta}",
        "vi": "Tôi đã tra cứu vận đơn {waybill}: hiện {status}, điểm mới nhất là {node}. {eta}",
        "en": "I found waybill {waybill}: it is {status}, most recently at {node}. {eta}",
    },
    "volume_result": {
        "zh": "运单 {waybill} 的模拟测量尺寸为 {length}×{width}×{height} cm，体积 {volume} cm³，体积重约 {weight} kg。计费公式仍需以正式系统为准。",
        "vi": "Kích thước đo mô phỏng của vận đơn {waybill} là {length}×{width}×{height} cm, thể tích {volume} cm³ và khối lượng quy đổi khoảng {weight} kg. Công thức chính thức phải theo hệ thống nghiệp vụ.",
        "en": "The demo dimensions for {waybill} are {length}×{width}×{height} cm, volume {volume} cm³, and volumetric weight about {weight} kg. Production billing rules may differ.",
    },
    "ask_phone_last4": {
        "zh": "轨迹显示该运单已签收。为核验收件人身份，请提供收件手机号后四位。",
        "vi": "Tracking cho thấy bưu kiện đã được giao. Để xác minh người nhận, vui lòng cung cấp 4 số cuối của số điện thoại nhận hàng.",
        "en": "Tracking shows the parcel was delivered. Please provide the last four digits of the recipient phone number for verification.",
    },
    "not_delivered": {
        "zh": "我查到该运单目前为{status}，最新节点是{node}，并不是已签收状态，因此暂时不能按“签收未收到”处理。",
        "vi": "Vận đơn hiện ở trạng thái {status}, chưa phải đã giao nên không thể tiếp tục quy trình “đã giao nhưng chưa nhận”. Điểm mới nhất: {node}.",
        "en": "This shipment is currently {status}, not delivered, so the delivered-not-received flow cannot continue. Latest node: {node}.",
    },
    "identity_failed": {
        "zh": "手机号后四位校验未通过，暂不能继续提交。请核对后重试，或联系人工客服。",
        "vi": "Không xác minh được 4 số cuối điện thoại nên chưa thể tiếp tục. Vui lòng kiểm tra lại hoặc liên hệ nhân viên hỗ trợ.",
        "en": "The phone-last-four verification failed, so I cannot continue. Check the digits or contact a human agent.",
    },
    "confirm_delivered_complaint": {
        "zh": "身份校验通过。将为运单 {waybill} 提交“签收未收到”核实投诉；此操作会创建工单。请明确回复“确认”或“取消”。",
        "vi": "Xác minh thành công. Hệ thống sẽ tạo yêu cầu điều tra “đã giao nhưng chưa nhận” cho {waybill}. Vui lòng trả lời “xác nhận” hoặc “hủy”.",
        "en": "Verification passed. I am ready to create a delivered-not-received investigation for {waybill}. Reply “confirm” or “cancel”.",
    },
    "ask_complaint_description": {
        "zh": "请简要描述投诉或理赔问题，例如破损、遗失及发生情况。",
        "vi": "Vui lòng mô tả ngắn gọn vấn đề khiếu nại/bồi thường, chẳng hạn hư hỏng, thất lạc và tình huống xảy ra.",
        "en": "Please briefly describe the complaint or claim, such as damage, loss, and what happened.",
    },
    "confirm_complaint": {
        "zh": "将为运单 {waybill} 创建投诉/理赔预受理工单，最终结果以审核为准。请明确回复“确认”或“取消”。",
        "vi": "Hệ thống sẽ tạo phiếu tiếp nhận khiếu nại/bồi thường sơ bộ cho {waybill}; kết quả cuối cùng phụ thuộc xét duyệt. Vui lòng trả lời “xác nhận” hoặc “hủy”.",
        "en": "I am ready to create a complaint/claim pre-acceptance ticket for {waybill}; the final result is subject to review. Reply “confirm” or “cancel”.",
    },
    "complaint_created": {
        "zh": "已成功创建核实工单 {ticket_id}。客服预计会在 24 小时内核实，请保持电话畅通，实际结果以业务系统审核为准。",
        "vi": "Đã tạo phiếu xác minh {ticket_id}. Bộ phận hỗ trợ dự kiến xác minh trong 24 giờ; vui lòng giữ điện thoại liên lạc và lấy kết quả nghiệp vụ làm chuẩn.",
        "en": "Investigation ticket {ticket_id} was created. Customer service is expected to verify it within 24 hours; keep your phone available and rely on the final business review.",
    },
    "followup_unavailable": {
        "zh": "抱歉让您久等了。运单 {waybill} 目前为{status}，最新节点是{node}。{eta} 现有轨迹仍在正常更新，暂时还不能提交正式催件；如果超过 48 小时没有新轨迹，我可以继续帮您发起核实。",
        "vi": "Xin lỗi vì đã để bạn chờ. Vận đơn {waybill} hiện {status}, điểm mới nhất là {node}. {eta} Tracking vẫn đang cập nhật nên chưa thể gửi yêu cầu giục chính thức; nếu quá 48 giờ không có cập nhật mới, tôi có thể tiếp tục hỗ trợ xác minh.",
        "en": "Sorry for the wait. Waybill {waybill} is {status}, most recently at {node}. {eta} Tracking is still updating, so a formal follow-up cannot be submitted yet; if there is no new scan for 48 hours, I can help request verification.",
    },
    "confirm_followup": {
        "zh": "轨迹显示 {status}。将为运单 {waybill} 提交正式催件/核实请求。请明确回复“确认”或“取消”。",
        "vi": "Tracking cho thấy {status}. Hệ thống sẽ gửi yêu cầu giục giao/xác minh chính thức cho {waybill}. Vui lòng trả lời “xác nhận” hoặc “hủy”.",
        "en": "Tracking shows {status}. I am ready to submit a formal delivery follow-up for {waybill}. Reply “confirm” or “cancel”.",
    },
    "followup_created": {
        "zh": "催件请求已提交，工单号 {ticket_id}。网点预计会在 24 小时内核实，请保持电话畅通并留意后续轨迹。",
        "vi": "Đã gửi yêu cầu giục giao, mã phiếu {ticket_id}. Bưu cục dự kiến xác minh trong 24 giờ; vui lòng giữ liên lạc và theo dõi tracking.",
        "en": "The follow-up was submitted under ticket {ticket_id}. The outlet is expected to verify it within 24 hours; keep your phone available and watch for tracking updates.",
    },
    "address_unavailable": {
        "zh": "当前状态为 {status}，暂不支持直接改址。原因：{reason}",
        "vi": "Trạng thái hiện tại là {status}, chưa hỗ trợ đổi địa chỉ trực tiếp. Lý do: {reason}",
        "en": "The current status is {status}; direct address change is unavailable. Reason: {reason}",
    },
    "ask_new_address": {
        "zh": "当前状态允许尝试改址。请提供完整的新收件地址；不要在消息中提供支付或账号密码。",
        "vi": "Trạng thái hiện tại cho phép thử đổi địa chỉ. Vui lòng cung cấp địa chỉ nhận mới đầy đủ; không gửi thông tin thanh toán hoặc mật khẩu.",
        "en": "The current status allows an address-change attempt. Provide the complete new delivery address; do not include payment details or passwords.",
    },
    "confirm_address": {
        "zh": "将为运单 {waybill} 提交改址申请，目标地址：{address}。申请不保证成功，请明确回复“确认”或“取消”。",
        "vi": "Hệ thống sẽ gửi yêu cầu đổi địa chỉ cho {waybill} đến: {address}. Không đảm bảo yêu cầu sẽ thành công; vui lòng trả lời “xác nhận” hoặc “hủy”.",
        "en": "I am ready to request an address change for {waybill} to: {address}. Success is not guaranteed. Reply “confirm” or “cancel”.",
    },
    "address_changed": {
        "zh": "改址申请已受理，请求号 {request_id}。最终是否生效以网点处理结果为准。",
        "vi": "Yêu cầu đổi địa chỉ đã được tiếp nhận, mã {request_id}. Hiệu lực cuối cùng phụ thuộc bưu cục xử lý.",
        "en": "The address-change request was accepted under {request_id}. Final application depends on outlet processing.",
    },
    "ask_ticket_id": {
        "zh": "请提供要查询的工单号。",
        "vi": "Vui lòng cung cấp mã phiếu cần tra cứu.",
        "en": "Please provide the ticket ID you want to query.",
    },
    "ticket_status": {
        "zh": "工单 {ticket_id} 当前状态为 {status}。",
        "vi": "Phiếu {ticket_id} hiện ở trạng thái {status}.",
        "en": "Ticket {ticket_id} is currently {status}.",
    },
    "ticket_not_found": {
        "zh": "未查到工单 {ticket_id}。请核对工单号，或联系人工客服。",
        "vi": "Không tìm thấy phiếu {ticket_id}. Vui lòng kiểm tra lại hoặc liên hệ nhân viên hỗ trợ.",
        "en": "Ticket {ticket_id} was not found. Check the ID or contact a human agent.",
    },
    "clarify": {
        "zh": "请说明您要查件、查包裹体积、催派送、反馈签收未收到、修改地址、投诉/理赔，还是咨询物流规则。",
        "vi": "Vui lòng cho biết bạn muốn tra cứu vận đơn/thể tích, giục giao, báo đã giao nhưng chưa nhận, đổi địa chỉ, khiếu nại/bồi thường hay hỏi quy định logistics.",
        "en": "Tell me whether you want tracking, package volume, delivery follow-up, delivered-not-received help, an address change, a complaint/claim, or policy information.",
    },
    "cancelled": {
        "zh": "已取消当前处理流程。",
        "vi": "Đã hủy quy trình hiện tại.",
        "en": "The current flow has been cancelled.",
    },
    "action_rejected": {
        "zh": "已取消本次操作，没有提交任何业务变更。",
        "vi": "Đã hủy thao tác này; không có thay đổi nghiệp vụ nào được gửi.",
        "en": "The action was cancelled; no business change was submitted.",
    },
    "repeat_confirmation": {
        "zh": "我还没有提交这次操作。为避免误操作或重复建单，请明确回复“确认”执行，或回复“取消”。",
        "vi": "Tôi chưa gửi thao tác này. Để tránh thao tác nhầm hoặc tạo phiếu trùng, vui lòng trả lời “xác nhận” để thực hiện hoặc “hủy”.",
        "en": "I have not submitted the action yet. To avoid mistakes or duplicate tickets, reply “confirm” to proceed or “cancel”.",
    },
    "tool_failed": {
        "zh": "业务服务暂时不可用，本次没有生成任何业务结果。请稍后重试，或联系人工客服。参考码：{error_code}",
        "vi": "Dịch vụ nghiệp vụ tạm thời không khả dụng và không có kết quả nào được tạo. Vui lòng thử lại sau hoặc liên hệ nhân viên. Mã: {error_code}",
        "en": "The business service is temporarily unavailable and no result was produced. Retry later or contact a human agent. Reference: {error_code}",
    },
    "transfer": {
        "zh": "已为您转接人工队列，排队号 {queue_id}。请勿重复提交相同请求。",
        "vi": "Đã chuyển bạn vào hàng đợi hỗ trợ, mã {queue_id}. Vui lòng không gửi lặp lại cùng yêu cầu.",
        "en": "You have been queued for a human agent under {queue_id}. Please do not submit the same request repeatedly.",
    },
}


class ResponseService:
    def render(self, language: str, key: str, **values: Any) -> str:
        language = language if language in {"en", "vi", "zh"} else "en"
        template = TEMPLATES[key].get(language, TEMPLATES[key]["en"])
        return template.format(**values)

    def tracking_values(self, language: str, data: dict[str, Any]) -> dict[str, str]:
        language = language if language in {"en", "vi", "zh"} else "en"
        status_code = str(data.get("status") or "unknown")
        node_code = str(data.get("current_node") or "unknown")
        status = TRACKING_STATUS_LABELS[language].get(status_code, status_code)
        node = TRACKING_NODE_LABELS[language].get(node_code, node_code)
        eta = TRACKING_ETA_LABELS[language].get(status_code, str(data.get("eta") or ""))
        return {"status": status, "node": node, "eta": eta}

    def conversation_reply(
        self,
        language: str,
        message: str,
        *,
        waybill_history: list[str],
        last_valid_waybill: str | None,
        last_ticket_id: str | None,
    ) -> str:
        """Useful deterministic no-Tool dialogue when the model is slow or unavailable."""
        language = language if language in {"en", "vi", "zh"} else "en"
        lower = message.lower()
        asks_memory = any(
            marker in lower
            for marker in (
                "还记得",
                "几个单",
                "几个运单",
                "查了几个",
                "do you remember",
                "how many waybill",
                "bao nhiêu mã",
                "nhớ",
            )
        )
        if asks_memory:
            return self._identifier_history_reply(
                language, waybill_history, last_valid_waybill
            )

        praise = any(
            marker in lower
            for marker in (
                "谢谢",
                "感谢",
                "优秀",
                "很棒",
                "你很棒",
                "不错",
                "厉害",
                "good job",
                "great",
                "awesome",
                "thank",
                "cảm ơn",
                "cam on",
                "tốt",
            )
        )
        if praise:
            if language == "zh":
                suffix = (
                    f"如果您还想继续查询运单 {last_valid_waybill}，我可以接着帮您。"
                    if last_valid_waybill
                    else "如果还有物流问题，直接告诉我就可以。"
                )
                return "谢谢您的认可，能帮到您我也很开心！" + suffix
            if language == "vi":
                suffix = (
                    f" Nếu muốn tiếp tục theo dõi vận đơn {last_valid_waybill}, tôi vẫn có thể hỗ trợ."
                    if last_valid_waybill
                    else " Nếu còn vấn đề logistics nào khác, bạn cứ nói với tôi."
                )
                return "Cảm ơn bạn! Tôi rất vui vì đã giúp được bạn." + suffix
            suffix = (
                f" I can keep helping with waybill {last_valid_waybill} if you like."
                if last_valid_waybill
                else " Tell me if you need anything else with your shipment."
            )
            return "Thank you—I’m glad I could help!" + suffix

        if last_ticket_id:
            if language == "zh":
                return f"我在。最近的工单是 {last_ticket_id}；您可以直接问我工单进度或继续说明问题。"
            if language == "vi":
                return f"Tôi vẫn ở đây. Phiếu gần nhất là {last_ticket_id}; bạn có thể hỏi tiến độ hoặc nói rõ thêm vấn đề."
            return f"I’m here. Your latest ticket is {last_ticket_id}; ask me for its status or tell me what else you need."
        if last_valid_waybill:
            if language == "zh":
                return f"我在。您刚才查询的是运单 {last_valid_waybill}；如果您觉得进度慢，我可以继续说明当前状态或帮您进入催派流程。"
            if language == "vi":
                return f"Tôi vẫn ở đây. Vận đơn vừa tra là {last_valid_waybill}; nếu thấy chậm, tôi có thể giải thích trạng thái hoặc hỗ trợ quy trình giục giao."
            return f"I’m here. The waybill you just checked is {last_valid_waybill}; if it seems slow, I can explain the status or help with a delivery follow-up."
        if language == "zh":
            return "您好，我在。您可以直接告诉我想查快递、催派、改地址、投诉，还是咨询寄递规则。"
        if language == "vi":
            return "Xin chào, tôi đang ở đây. Bạn có thể hỏi về tracking, giục giao, đổi địa chỉ, khiếu nại hoặc quy định gửi hàng."
        return "Hello, I’m here. You can ask about tracking, delivery follow-up, address changes, complaints, or shipping rules."

    @staticmethod
    def _identifier_history_reply(
        language: str,
        waybill_history: list[str],
        last_valid_waybill: str | None,
    ) -> str:
        if not waybill_history:
            if language == "zh":
                return "这次会话里您还没有提供过运单号。"
            if language == "vi":
                return "Trong cuộc trò chuyện này, bạn chưa cung cấp mã vận đơn nào."
            return "You have not provided a waybill in this conversation yet."
        joined = "、".join(waybill_history)
        count = len(waybill_history)
        if language == "zh":
            latest = (
                f"最近一次有效查询的是 {last_valid_waybill}。"
                if last_valid_waybill
                else ""
            )
            return f"记得。您在本次会话里一共提供过 {count} 个不同的单号：{joined}。{latest}"
        if language == "vi":
            latest = (
                f" Mã hợp lệ gần nhất là {last_valid_waybill}."
                if last_valid_waybill
                else ""
            )
            return f"Tôi nhớ. Bạn đã cung cấp {count} mã khác nhau trong cuộc trò chuyện này: {joined}.{latest}"
        latest = (
            f" The most recent valid one is {last_valid_waybill}."
            if last_valid_waybill
            else ""
        )
        return f"Yes. You provided {count} different waybills in this conversation: {joined}.{latest}"
