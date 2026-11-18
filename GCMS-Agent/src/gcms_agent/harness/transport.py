"""Conservative transport classification; never infer zero usage from an error."""
import re


def connection_failure(detail):
    return bool(re.search(r"\b(?:ECONNREFUSED|ENOTFOUND|EAI_AGAIN)\b", detail or "", re.I))


def unused_connection_failure(receipt, usage):
    if not receipt or receipt.get("error_category") != "service_error":
        return False
    audit = receipt.get("audit")
    if not isinstance(audit, dict) or not connection_failure(receipt.get("detail")):
        return False
    fields = ("input_tokens", "cache_read_input_tokens", "output_tokens")
    return (usage.get("known") is True
            and all(type(usage.get(k)) in (int, float) and usage[k] == 0 for k in fields)
            and audit.get("tool_metrics") == [] and audit.get("trials") == []
            and audit.get("submission_attempt_count") == 0)
