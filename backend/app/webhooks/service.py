"""Inbound webhook handling: authenticate, dedupe on message_id, store, log every delivery."""
import hmac
import json
import logging

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


def log_delivery(conn: Connection, outcome: str, payload, *, workspace=None,
                 external_message_id=None, message_id=None) -> None:
    conn.execute(
        """INSERT INTO webhook_deliveries
               (workspace_slug, external_message_id, message_id, outcome, payload)
           VALUES (%s, %s, %s, %s, %s)""",
        (workspace, external_message_id, message_id, outcome, Jsonb(payload)),
    )
    log.info("webhook delivery outcome=%s workspace=%s message_id=%s",
             outcome, workspace, external_message_id)


def safe_json(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return {"_unparseable": raw[:2000].decode("utf-8", "replace")}


class UnknownWorkspace(Exception):
    pass


def store_inbound(conn: Connection, msg: InboundMessage) -> tuple[str, str, str]:
    """Store one inbound message exactly once.

    Returns (status, conversation_id, message_id) where status is 'stored' or 'duplicate'.
    A duplicate has zero side effects: the transaction that might have created a
    conversation for it is rolled back.
    """
    payload = msg.model_dump(mode="json")
    with conn.transaction():
        ws = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (msg.workspace,)).fetchone()
        if ws is None:
            raise UnknownWorkspace(msg.workspace)

        # Race-safe "create if missing": two first-messages of a new conversation arriving
        # together both end up with the same row. First writer's customer_name wins.
        conv = conn.execute(
            """INSERT INTO conversations (workspace_id, external_id, customer_name)
               VALUES (%s, %s, %s)
               ON CONFLICT (workspace_id, external_id)
               DO UPDATE SET customer_name = conversations.customer_name
               RETURNING id""",
            (ws["id"], msg.conversation_id, msg.customer_name),
        ).fetchone()

        # The UNIQUE(workspace_id, external_message_id) constraint is the dedupe.
        # A concurrent duplicate blocks here until the first commits, then does nothing.
        inserted = conn.execute(
            """INSERT INTO messages
                   (workspace_id, conversation_id, direction, external_message_id, text, sent_at)
               VALUES (%s, %s, 'inbound', %s, %s, %s)
               ON CONFLICT (workspace_id, external_message_id) DO NOTHING
               RETURNING id""",
            (ws["id"], conv["id"], msg.message_id, msg.text, msg.sent_at),
        ).fetchone()

        if inserted is None:
            # Undo anything this delivery did (e.g. a conversation created for a retried
            # message whose conversation_id changed) and report the original message.
            raise _Duplicate(ws["id"])

        conn.execute(
            """INSERT INTO webhook_deliveries
                   (workspace_slug, external_message_id, message_id, outcome, payload)
               VALUES (%s, %s, %s, 'stored', %s)""",
            (msg.workspace, msg.message_id, inserted["id"], Jsonb(payload)),
        )
    log.info("webhook delivery outcome=stored workspace=%s message_id=%s",
             msg.workspace, msg.message_id)
    return "stored", str(conv["id"]), str(inserted["id"])


class _Duplicate(Exception):
    def __init__(self, workspace_id):
        self.workspace_id = workspace_id


def handle_inbound(conn: Connection, msg: InboundMessage) -> tuple[str, str, str]:
    try:
        return store_inbound(conn, msg)
    except _Duplicate as dup:
        # The transaction above was rolled back by the exception. Look up the original.
        original = conn.execute(
            """SELECT id, conversation_id FROM messages
               WHERE workspace_id = %s AND external_message_id = %s""",
            (dup.workspace_id, msg.message_id),
        ).fetchone()
        log_delivery(conn, "duplicate", msg.model_dump(mode="json"), workspace=msg.workspace,
                     external_message_id=msg.message_id, message_id=original["id"])
        return "duplicate", str(original["conversation_id"]), str(original["id"])
