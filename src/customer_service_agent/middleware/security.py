import hashlib
import re


def hash_subject(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def mask_user_id(value: str) -> str:
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


def redact_sensitive_text(value: str) -> str:
    value = re.sub(r"(?<!\d)(\+?\d[\d\s-]{7,16}\d)(?!\d)", "[PHONE]", value)
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]", value)
    return value
