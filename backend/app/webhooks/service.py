"""Inbound webhook handling: authenticate, dedupe on message_id, store, log every delivery.

Time: the router stamps `received_at` once, when the request arrives, and passes it down.
The database never generates timestamps, so a stored message and the delivery row that
created it carry the exact same received_at and point at each other
(messages.webhook_delivery_id <-> webhook_deliveries.message_id).
"""
import hmac
import json
import logging
import uuid
from datetime import datetime

from psycopg import Connection
from psycopg.types.json import Jsonb

from app import config
from app.schemas.webhook import InboundMessage

log = logging.getLogger("inbox.webhooks")


def secret_ok(provided: str | None) -> bool:
    if provided is None:
        return False
    # Constant-time compare: response timing must not reveal how much of the secret matched.
    return hmac.compare_digest(provided.encode(), config.WEBHOOK_SECRET.encode())


def log_delivery(conn: Connection, outcome: str, payload, received_at: datetime, *,
                 workspace=None, external_message_id=None, message_id=None) -> int:
    row = conn.execute(
        """INSERT INTO webhook_deliveries
               (received_at, workspace_slug, external_message_id, message_id, outcome, payload)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (received_at, workspace, external_message_id, message_id, outcome, Jsonb(payload)),
    ).fetchone()
    log.info("webhook delivery id=%s outcome=%s workspace=%s message_id=%s received_at=%s",
             row["id"], outcome, workspace, external_message_id, received_at.isoformat())
    return row["id"]


def safe_json(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return {"_unparseable": raw[:2000].decode("utf-8", "replace")}


class UnknownWorkspace(Exception):
    pass


class _Duplicate(Exception):
    def __init__(self, workspace_id):
        self.workspace_id = workspace_id


def _store(conn: Connection, msg: InboundMessage, received_at: datetime) -> tuple[str, str]:
    payload = msg.model_dump(mode="json")
    message_id = uuid.uuid4()  # generated here so the delivery row can reference it up front
    with conn.transaction():
        ws = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (msg.workspace,)).fetchone()
        if ws is None:
            raise UnknownWorkspace(msg.workspace)

        # Race-safe "create if missing": two first-messages of a new conversation arriving
        # together both end up with the same row. First writer's name and created_at win.
        conv = conn.execute(
            """INSERT INTO conversations (workspace_id, external_id, customer_name, created_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (workspace_id, external_id)
               DO UPDATE SET customer_name = conversations.customer_name
               RETURNING id""",
            (ws["id"], msg.conversation_id, msg.customer_name, received_at),
        ).fetchone()

        # Delivery first (its FK to messages is deferred to commit), then the message
        # pointing back at it. Both carry the same received_at.
        delivery_id = log_delivery(conn, "stored", payload, received_at, workspace=msg.workspace,
                                   external_message_id=msg.message_id, message_id=message_id)

        # UNIQUE(workspace_id, external_message_id) is the dedupe. A concurrent duplicate
        # blocks here until the first commits, then inserts nothing.
        inserted = conn.execute(
            """INSERT INTO messages (id, workspace_id, conversation_id, direction,
                                     external_message_id, text, sent_at, received_at,
                                     webhook_delivery_id)
               VALUES (%s, %s, %s, 'inbound', %s, %s, %s, %s, %s)
               ON CONFLICT (workspace_id, external_message_id) DO NOTHING
               RETURNING id""",
            (message_id, ws["id"], conv["id"], msg.message_id, msg.text, msg.sent_at,
             received_at, delivery_id),
        ).fetchone()

        if inserted is None:
            # Raising rolls back everything above (conversation upsert, 'stored' delivery row),
            # so a duplicate leaves no side effects.
            raise _Duplicate(ws["id"])
    return str(conv["id"]), str(message_id)


def handle_inbound(conn: Connection, msg: InboundMessage,
                   received_at: datetime) -> tuple[str, str, str]:
    """Store one inbound message exactly once.
    Returns (status, conversation_id, message_id), status being 'stored' or 'duplicate'."""
    try:
        conv_id, msg_id = _store(conn, msg, received_at)
        return "stored", conv_id, msg_id
    except _Duplicate as dup:
        original = conn.execute(
            """SELECT id, conversation_id FROM messages
               WHERE workspace_id = %s AND external_message_id = %s""",
            (dup.workspace_id, msg.message_id),
        ).fetchone()
        log_delivery(conn, "duplicate", msg.model_dump(mode="json"), received_at,
                     workspace=msg.workspace, external_message_id=msg.message_id,
                     message_id=original["id"])
        return "duplicate", str(original["conversation_id"]), str(original["id"])
