"""Tests run against a real Postgres (TEST_DATABASE_URL) and a real uvicorn server on a
random port, so concurrency tests exercise actual parallel connections and row locks."""
import os
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
import uvicorn
from psycopg.rows import dict_row

TEST_DSN = os.getenv("TEST_DATABASE_URL",
                     "postgresql://postgres:postgres@localhost:5432/inbox_test")
os.environ["DATABASE_URL"] = TEST_DSN
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")

from app import config  # noqa: E402
from app.db import pool as db  # noqa: E402
from scripts.setup_db import setup  # noqa: E402

config.DATABASE_URL = TEST_DSN
SECRET = config.WEBHOOK_SECRET

TOKENS = {"alice": "acme-alice-token", "bob": "acme-bob-token",
          "gina": "globex-gina-token", "gus": "globex-gus-token"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    setup(TEST_DSN)
    db.open_pool(TEST_DSN)
    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)
    db.close_pool()


@pytest.fixture(scope="session")
def http(base_url):
    with httpx.Client(base_url=base_url, timeout=10) as c:
        yield c


@pytest.fixture
def sql():
    with psycopg.connect(TEST_DSN, autocommit=True, row_factory=dict_row) as conn:
        yield lambda q, *a: conn.execute(q, a).fetchall()


def auth(who: str) -> dict:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


def uid(prefix="x") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def payload(workspace="acme", conversation_id=None, message_id=None, text="hello",
            customer_name="Cust", sent_at=None) -> dict:
    return dict(workspace=workspace, conversation_id=conversation_id or uid("conv"),
                message_id=message_id or uid("msg"), customer_name=customer_name, text=text,
                sent_at=(sent_at or datetime.now(timezone.utc)).isoformat())


def send(http, body, secret=SECRET):
    headers = {} if secret is None else {"X-Webhook-Secret": secret}
    return http.post("/webhooks/inbound", json=body, headers=headers)


def new_conversation(http, workspace="acme", **kw) -> str:
    r = send(http, payload(workspace=workspace, **kw))
    assert r.status_code == 200, r.text
    return r.json()["conversation_id"]


def concurrently(n, fn):
    """Run fn(i) in n threads released at the same instant by a barrier."""
    barrier = threading.Barrier(n)

    def go(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(go, range(n)))


def ago(seconds) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)
