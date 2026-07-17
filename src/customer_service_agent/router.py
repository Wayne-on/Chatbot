from __future__ import annotations

import re
import unicodedata

from customer_service_agent.schemas import Intent, RouteDecision, SceneStatus
from customer_service_agent.state import ConversationState

WAYBILL_PATTERN = re.compile(r"(?<![A-Z0-9])(JT\d{8,13}|\d{8,15})(?![A-Z0-9])", re.I)
WAYBILL_CANDIDATE_PATTERN = re.compile(
    r"(?<![A-Z0-9])(JT[\s-]*\d{6,20}|\d{6,20})(?![A-Z0-9])", re.I
)
TICKET_PATTERN = re.compile(r"\b(?:MOCK|CMP|TKT)[A-Z0-9-]{6,32}\b", re.I)


def normalize_waybill(value: str) -> str | None:
    candidate = re.sub(r"[\s-]", "", value).upper()
    if re.fullmatch(r"JT\d{8,13}|\d{8,15}", candidate):
        return candidate
    return None


def extract_waybill(message: str) -> str | None:
    compact = re.sub(r"(?<=JT)[\s-]+", "", message.upper())
    match = WAYBILL_PATTERN.search(compact)
    return normalize_waybill(match.group(1)) if match else None


def extract_invalid_waybill(message: str) -> str | None:
    compact = re.sub(r"(?<=JT)[\s-]+", "", message.upper())
    match = WAYBILL_CANDIDATE_PATTERN.search(compact)
    if not match:
        return None
    candidate = re.sub(r"[\s-]", "", match.group(1)).upper()
    return candidate if normalize_waybill(candidate) is None else None


def _fold(message: str) -> str:
    normalized = unicodedata.normalize("NFKD", message.lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def detect_language(message: str, requested: str | None, previous: str = "en") -> str:
    if requested:
        code = requested.lower().split("-")[0]
        if code in {"en", "vi", "zh"}:
            return code
    if re.search(r"[\u3400-\u9fff]", message):
        return "zh"
    folded = _fold(message)
    vi_markers = (
        "buu kien",
        "don hang",
        "ma van don",
        "giao hang",
        "dia chi",
        "khieu nai",
        "boi thuong",
        "chua nhan",
        "cam gui",
    )
    if any(marker in folded for marker in vi_markers) or re.search(r"[ăâđêôơưĂÂĐÊÔƠƯ]", message):
        return "vi"
    if re.fullmatch(r"[\sA-Za-z0-9+-]+", message) and extract_waybill(message):
        return previous if previous in {"en", "vi", "zh"} else "en"
    return "en"


INTENT_KEYWORDS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (
        Intent.DELIVERED_NOT_RECEIVED,
        (
            "签收但未收到",
            "签收了但没收到",
            "显示签收",
            "未收到",
            "delivered but",
            "not received",
            "signed but",
            "da giao nhung",
            "chua nhan duoc",
        ),
    ),
    (
        Intent.CHANGE_ADDRESS,
        (
            "改地址",
            "修改地址",
            "换地址",
            "预约派送",
            "change address",
            "reschedule",
            "doi dia chi",
            "doi ngay giao",
        ),
    ),
    (
        Intent.PACKAGE_VOLUME,
        ("体积", "尺寸", "长宽高", "volume", "dimensions", "size", "kich thuoc"),
    ),
    (
        Intent.QUERY_COMPLAINT,
        (
            "查询工单",
            "工单进度",
            "工单多久",
            "工单什么时候",
            "多久能处理",
            "处理得怎么样",
            "处理怎么样",
            "投诉进度",
            "ticket status",
            "ticket update",
            "complaint status",
            "tinh trang khieu nai",
            "tiến độ khiếu nại",
        ),
    ),
    (
        Intent.COMPLAINT,
        (
            "投诉",
            "理赔",
            "赔偿",
            "破损",
            "丢失",
            "遗失",
            "complaint",
            "claim",
            "compensation",
            "damaged",
            "lost",
            "khieu nai",
            "boi thuong",
            "hu hong",
            "that lac",
        ),
    ),
    (
        Intent.DELIVERY_FOLLOWUP,
        (
            "催件",
            "催派送",
            "一直没更新",
            "物流没更新",
            "延误",
            "这么慢",
            "太慢",
            "怎么还没",
            "怎么没送",
            "还没人送",
            "为什么还没",
            "能快些吗",
            "能快一点",
            "能不能快",
            "快些送",
            "快点送",
            "再快些",
            "urge",
            "delayed",
            "no update",
            "so slow",
            "taking so long",
            "why hasn't",
            "giuc giao",
            "lau khong",
            "qua cham",
            "quá chậm",
        ),
    ),
    (
        Intent.FAQ,
        (
            "禁寄",
            "能不能寄",
            "时效",
            "多久能到",
            "prohibited",
            "restricted",
            "can i ship",
            "delivery time",
            "cam gui",
            "co gui duoc",
            "bao nhieu ngay",
        ),
    ),
    (
        Intent.TRACKING,
        (
            "查快递",
            "查一下快递",
            "查询快递",
            "查另一个快递",
            "包裹在哪",
            "到哪里了",
            "查件",
            "轨迹",
            "track",
            "where is",
            "where's my",
            "tracking",
            "tra cuu",
            "o dau",
            "don hang cua toi",
        ),
    ),
)

SOCIAL_MARKERS = (
    "你好",
    "您好",
    "谢谢",
    "感谢",
    "优秀",
    "很棒",
    "你很棒",
    "不错",
    "厉害",
    "还记得",
    "几个单",
    "几个运单",
    "查了几个",
    "hello",
    "good job",
    "thank you",
    "do you remember",
    "how many waybill",
    "xin chao",
    "xin chào",
    "cam on",
    "cảm ơn",
)

SOCIAL_INTERJECTIONS = {
    "额",
    "呃",
    "嗯",
    "哦",
    "噢",
    "啊",
    "好吧",
    "行吧",
    "hmm",
    "uh",
    "oh",
    "great",
    "awesome",
    "ờ",
    "ừ",
}


class Router:
    """Deterministic first-pass router; all extracted values are validated later by Tools."""

    def route(
        self,
        message: str,
        *,
        requested_language: str | None,
        state: ConversationState,
    ) -> RouteDecision:
        folded = _fold(message)
        stripped = message.strip()
        language = detect_language(message, requested_language, state.language)
        waybill = extract_waybill(message)
        invalid_waybill = extract_invalid_waybill(message)

        rejection_values = {
            "不确认",
            "不要",
            "否",
            "no",
            "nope",
            "khong",
            "không",
        }
        confirmation_values = {
            "确认",
            "确定",
            "同意",
            "是",
            "好的",
            "可以",
            "confirm",
            "confirmed",
            "yes",
            "ok",
            "okay",
            "dong y",
            "đồng ý",
            "xac nhan",
            "xác nhận",
        }
        rejection = stripped.lower() in rejection_values or folded in rejection_values
        confirmation = not rejection and (
            stripped.lower() in confirmation_values or folded in confirmation_values
        )

        cancel_markers = ("算了", "取消", "不用了", "cancel", "never mind", "thoi", "huy")
        human_markers = ("人工", "真人", "客服人员", "human agent", "representative", "nhan vien")
        modify_markers = ("改成", "更正", "刚才错", "previous was wrong", "correct it", "sua lai")
        modifies_existing = any(marker in folded or marker in message for marker in modify_markers)

        explicit_intent: Intent | None = None
        for intent, keywords in INTENT_KEYWORDS:
            if any(keyword in message.lower() or keyword in folded for keyword in keywords):
                explicit_intent = intent
                break
        if explicit_intent is None and (
            stripped.lower() in SOCIAL_INTERJECTIONS
            or folded in SOCIAL_INTERJECTIONS
            or any(marker in message.lower() or marker in folded for marker in SOCIAL_MARKERS)
        ):
            explicit_intent = Intent.CONVERSATION

        phone_last4 = None
        if state.current_step == "waiting_phone_last4" or (
            modifies_existing and state.current_intent == Intent.DELIVERED_NOT_RECEIVED
        ):
            match = re.search(r"(?<!\d)(\d{4})(?!\d)", stripped)
            if match:
                phone_last4 = match.group(1)

        ticket_match = TICKET_PATTERN.search(stripped.upper())
        new_address = None
        address_match = re.search(
            r"(?:新地址(?:是|为)?|改到|change (?:it )?to|new address(?: is)?|dia chi moi(?: la)?)\s*[:：]?\s*(.{8,})",
            stripped,
            re.I,
        )
        if address_match:
            new_address = address_match.group(1).strip()
        elif state.current_step == "waiting_new_address" and len(stripped) >= 8:
            new_address = stripped
        elif modifies_existing and state.current_intent == Intent.CHANGE_ADDRESS:
            changed = re.split(r"改成|change (?:it )?to|sua lai", stripped, maxsplit=1, flags=re.I)
            if len(changed) == 2 and len(changed[1].strip()) >= 8:
                new_address = changed[1].strip()

        intent = explicit_intent
        if intent is None and state.active:
            intent = state.current_intent
        elif intent is None and waybill and state.current_intent:
            intent = state.current_intent
        elif (
            intent is None
            and state.scene_status == SceneStatus.COMPLETED
            and state.current_intent
            and self._looks_like_contextual_followup(stripped, folded)
        ):
            intent = state.current_intent

        return RouteDecision(
            intent=intent,
            language=language,
            waybill_no=waybill,
            invalid_waybill_no=invalid_waybill,
            phone_last4=phone_last4,
            ticket_id=ticket_match.group(0) if ticket_match else None,
            new_address=new_address,
            cancel_requested=any(
                marker in folded or marker in message for marker in cancel_markers
            ),
            confirmation=confirmation,
            rejection=rejection,
            human_requested=any(marker in folded or marker in message for marker in human_markers),
            modifies_existing=modifies_existing,
            explicit_intent=explicit_intent is not None,
        )

    @staticmethod
    def _looks_like_contextual_followup(message: str, folded: str) -> bool:
        compact = re.sub(r"\s", "", message)
        if compact and re.fullmatch(r"[?？!！。.]+", compact):
            return True
        markers = (
            "怎么",
            "为什么",
            "然后呢",
            "什么意思",
            "怎么办",
            "why",
            "what now",
            "what does",
            "how come",
            "sao",
            "tai sao",
            "tại sao",
        )
        return len(message) <= 40 and any(
            marker in folded or marker in message for marker in markers
        )
