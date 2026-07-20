import hashlib

from .models import ExecutionContext


def semantic_owner(context: ExecutionContext) -> str:
    return context.principal_key


def event_owner(context: ExecutionContext) -> str:
    return context.group_scope_key or context.principal_key


def scoped_event_id(context: ExecutionContext, external_id: str) -> str:
    if not external_id or not external_id.strip():
        raise ValueError("external_id must not be empty")
    owner = event_owner(context)
    payload = f"{owner}\0{external_id.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
