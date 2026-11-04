"""All conversation SQL. Every query is scoped by workspace_id taken from the token."""
from uuid import UUID

from psycopg import Connection

_SUMMARY_SQL = """
SELECT c.id, c.customer_name,
       a.id AS agent_id, a.name AS agent_name,
       lm.text AS last_message, lm.sent_at AS last_message_at,
       wt.waiting_since
FROM conversations c
LEFT JOIN agents a ON a.id = c.assigned_agent_id
-- Last message by when it was SENT, not when the webhook happened to arrive.
LEFT JOIN LATERAL (
    SELECT m.text, m.sent_at FROM messages m
    WHERE m.conversation_id = c.id
    ORDER BY m.sent_at DESC, m.received_at DESC, m.id DESC LIMIT 1
) lm ON true
-- Waiting since the oldest customer message sent after the last agent reply.
LEFT JOIN LATERAL (
    SELECT min(m.sent_at) AS waiting_since FROM messages m
    WHERE m.conversation_id = c.id AND m.direction = 'inbound'
      AND m.sent_at > COALESCE((SELECT max(o.sent_at) FROM messages o
                                WHERE o.conversation_id = c.id AND o.direction = 'outbound'),
                               '-infinity')
) wt ON true
WHERE c.workspace_id = %(ws)s AND {filter}
ORDER BY wt.waiting_since ASC NULLS LAST, lm.sent_at DESC NULLS LAST
"""


def parse_id(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def list_conversations(conn: Connection, ws: UUID, me: UUID, view: str) -> list[dict]:
    filt = "c.assigned_agent_id IS NULL" if view == "unassigned" else "c.assigned_agent_id = %(me)s"
    return conn.execute(_SUMMARY_SQL.format(filter=filt), {"ws": ws, "me": me}).fetchall()


def get_conversation(conn: Connection, ws: UUID, conv_id: UUID) -> dict | None:
    return conn.execute(
        """SELECT c.id, c.customer_name, c.assigned_agent_id, c.assigned_at, c.created_at,
                  a.name AS agent_name
           FROM conversations c LEFT JOIN agents a ON a.id = c.assigned_agent_id
           WHERE c.id = %s AND c.workspace_id = %s""",
        (conv_id, ws),
    ).fetchone()


def get_messages(conn: Connection, ws: UUID, conv_id: UUID) -> list[dict]:
    return conn.execute(
        """SELECT m.id, m.direction, m.text, m.sent_at, m.received_at,
                  m.author_agent_id, a.name AS author_name
           FROM messages m LEFT JOIN agents a ON a.id = m.author_agent_id
           WHERE m.conversation_id = %s AND m.workspace_id = %s
           ORDER BY m.sent_at, m.received_at, m.id""",
        (conv_id, ws),
    ).fetchall()


def try_claim(conn: Connection, ws: UUID, conv_id: UUID, me: UUID) -> bool:
    """Atomic compare-and-set. Postgres row locking decides the winner, not timestamps:
    a concurrent second UPDATE waits for the first to commit, re-checks
    `assigned_agent_id IS NULL` against the new row version, and matches 0 rows."""
    row = conn.execute(
        """UPDATE conversations SET assigned_agent_id = %s, assigned_at = now()
           WHERE id = %s AND workspace_id = %s AND assigned_agent_id IS NULL
           RETURNING id""",
        (me, conv_id, ws),
    ).fetchone()
    return row is not None


def insert_reply(conn: Connection, ws: UUID, conv_id: UUID, me: UUID, text: str) -> dict | None:
    """Insert only if *I* am the assignee; the check and the write are one statement."""
    return conn.execute(
        """INSERT INTO messages (workspace_id, conversation_id, direction, author_agent_id, text, sent_at)
           SELECT c.workspace_id, c.id, 'outbound', %s, %s, now()
           FROM conversations c
           WHERE c.id = %s AND c.workspace_id = %s AND c.assigned_agent_id = %s
           RETURNING id, direction, text, sent_at, received_at""",
        (me, text, conv_id, ws, me),
    ).fetchone()
