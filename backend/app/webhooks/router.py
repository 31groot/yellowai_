from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.db.pool import get_pool
from app.schemas.webhook import InboundMessage, WebhookAck
from app.webhooks import service

router = APIRouter(tags=["webhooks"])


def _process(raw: bytes, secret: str | None, received_at: datetime) -> WebhookAck:
    with get_pool().connection() as conn:
        if not service.secret_ok(secret):
            # Logged so a rotated or misconfigured secret shows up in ops.
            service.log_delivery(conn, "rejected_auth", service.safe_json(raw), received_at)
            raise HTTPException(401, "invalid or missing X-Webhook-Secret")
        try:
            msg = InboundMessage.model_validate_json(raw)
        except ValidationError as e:
            service.log_delivery(conn, "invalid", service.safe_json(raw), received_at)
            raise HTTPException(422, e.errors(include_url=False, include_context=False, include_input=False))
        try:
            status, conv_id, msg_id = service.handle_inbound(conn, msg, received_at)
        except service.UnknownWorkspace:
            service.log_delivery(conn, "unknown_workspace", msg.model_dump(mode="json"), received_at,
                                 workspace=msg.workspace, external_message_id=msg.message_id)
            raise HTTPException(422, f"unknown workspace '{msg.workspace}'")
        # 200 for duplicates too: the provider must stop retrying.
        return WebhookAck.build(status, conv_id, msg_id)


@router.post("/webhooks/inbound", response_model=WebhookAck)
async def inbound_webhook(request: Request, x_webhook_secret: str | None = Header(default=None)):
    # Stamped once, on arrival, by the app; every row this request writes uses this value.
    received_at = datetime.now(timezone.utc)
    # Raw body is read first so the secret is checked before we trust/parse anything.
    raw = await request.body()
    return await run_in_threadpool(_process, raw, x_webhook_secret, received_at)
