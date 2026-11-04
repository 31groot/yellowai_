"""Create the schema and seed two workspaces with two agents each.

Usage: python scripts/setup_db.py [--dsn URL] [--demo]
  --demo  also inserts a few sample conversations via the same code path as the webhook.
"""
import argparse
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "app" / "db" / "schema.sql"

# Fixed demo tokens so the README and UI can reference them. Only sha256 hashes are stored.
SEED = {
    "acme":   ("Acme Corp",   [("Alice", "acme-alice-token"), ("Bob", "acme-bob-token")]),
    "globex": ("Globex Inc",  [("Gina", "globex-gina-token"), ("Gus", "globex-gus-token")]),
}


def setup(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(SCHEMA.read_text())
        for slug, (name, agents) in SEED.items():
            ws_id = conn.execute("INSERT INTO workspaces (slug, name) VALUES (%s, %s) RETURNING id",
                                 (slug, name)).fetchone()[0]
            for agent_name, token in agents:
                conn.execute("INSERT INTO agents (workspace_id, name, token_hash) VALUES (%s, %s, %s)",
                             (ws_id, agent_name, hashlib.sha256(token.encode()).hexdigest()))


def demo_data(dsn: str) -> None:
    from app.db import pool
    from app.schemas.webhook import InboundMessage
    from app.webhooks.service import handle_inbound

    now = datetime.now(timezone.utc)
    samples = [
        ("acme", "wa-1001", "Priya", ["Hi, my order hasn't arrived", "Order #4521"], 12),
        ("acme", "wa-1002", "Tom", ["Can I change my delivery address?"], 5),
        ("acme", "web-77", "Lena", ["Refund status please"], 30),
        ("globex", "wa-2001", "Hank", ["My invoice looks wrong"], 8),
        ("globex", "web-88", "Marge", ["How do I reset my password?"], 2),
    ]
    p = pool.open_pool(dsn)
    with p.connection() as conn:
        for ws, conv, name, texts, minutes_ago in samples:
            for i, text in enumerate(texts):
                handle_inbound(conn, InboundMessage(
                    workspace=ws, conversation_id=conv, message_id=f"{conv}-m{i}",
                    customer_name=name, text=text,
                    sent_at=now - timedelta(minutes=minutes_ago) + timedelta(seconds=20 * i)))
    pool.close_pool()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=config.DATABASE_URL)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    setup(args.dsn)
    print("Schema created. Agents (workspace / name / token):")
    for slug, (_, agents) in SEED.items():
        for agent_name, token in agents:
            print(f"  {slug:7} {agent_name:6} {token}")
    if args.demo:
        demo_data(args.dsn)
        print("Demo conversations inserted.")
