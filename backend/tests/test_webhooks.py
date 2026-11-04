from datetime import datetime, timezone

import pytest

from tests.conftest import (SECRET, ago, concurrently, new_conversation, payload, send, uid,
                            auth)


def msg_count(sql, workspace, external_message_id):
    return sql("""SELECT count(*) AS n FROM messages m JOIN workspaces w ON w.id = m.workspace_id
                  WHERE w.slug = %s AND m.external_message_id = %s""",
               workspace, external_message_id)[0]["n"]


# ---- secret -----------------------------------------------------------------

@pytest.mark.parametrize("secret", [None, "", "wrong", SECRET.upper(), f"x{SECRET}", SECRET + "x",
                                    SECRET[:-1]])
def test_bad_or_missing_secret_is_rejected_and_nothing_stored(http, sql, secret):
    body = payload()
    r = send(http, body, secret=secret)
    assert r.status_code == 401
    assert msg_count(sql, "acme", body["message_id"]) == 0
    assert sql("SELECT count(*) AS n FROM conversations WHERE external_id = %s",
               body["conversation_id"])[0]["n"] == 0


def test_rejected_attempts_are_logged(http, sql):
    body = payload()
    send(http, body, secret="nope")
    rows = sql("SELECT outcome FROM webhook_deliveries WHERE payload->>'message_id' = %s",
               body["message_id"])
    assert [r["outcome"] for r in rows] == ["rejected_auth"]


# ---- (b) duplicate message_id -----------------------------------------------

def test_same_message_id_twice_stores_one_message(http, sql):
    body = payload()
    r1, r2 = send(http, body), send(http, body)
    assert (r1.status_code, r2.status_code) == (200, 200)  # 200 so the provider stops retrying
    assert (r1.json()["status"], r2.json()["status"]) == ("stored", "duplicate")
    assert r1.json()["message_id"] == r2.json()["message_id"]
    assert msg_count(sql, "acme", body["message_id"]) == 1
    outcomes = sql("SELECT outcome FROM webhook_deliveries WHERE external_message_id = %s ORDER BY id",
                   body["message_id"])
    assert [o["outcome"] for o in outcomes] == ["stored", "duplicate"]


def test_same_message_id_posted_concurrently_stores_one(http, sql):
    body = payload()
    results = concurrently(10, lambda _: send(http, body))
    assert all(r.status_code == 200 for r in results)
    statuses = sorted(r.json()["status"] for r in results)
    assert statuses == ["duplicate"] * 9 + ["stored"]
    assert msg_count(sql, "acme", body["message_id"]) == 1


def test_retry_with_changed_body_keeps_first_version(http, sql):
    body = payload(text="original")
    send(http, body)
    r = send(http, {**body, "text": "edited on retry"})
    assert r.json()["status"] == "duplicate"
    texts = sql("SELECT text FROM messages WHERE external_message_id = %s", body["message_id"])
    assert [t["text"] for t in texts] == ["original"]


def test_duplicate_with_different_conversation_id_creates_nothing(http, sql):
    body = payload()
    send(http, body)
    stray_conv = uid("stray")
    r = send(http, {**body, "conversation_id": stray_conv})
    assert r.json()["status"] == "duplicate"
    assert sql("SELECT count(*) AS n FROM conversations WHERE external_id = %s",
               stray_conv)[0]["n"] == 0


def test_same_message_id_in_two_workspaces_is_two_messages(http, sql):
    mid = uid("shared")
    a = send(http, payload(workspace="acme", message_id=mid))
    g = send(http, payload(workspace="globex", message_id=mid))
    assert a.json()["status"] == g.json()["status"] == "stored"
    assert msg_count(sql, "acme", mid) == 1 and msg_count(sql, "globex", mid) == 1


# ---- conversations ----------------------------------------------------------

def test_new_conversation_is_unassigned(http):
    cid = new_conversation(http, customer_name="Newbie")
    r = http.get(f"/conversations/{cid}", headers=auth("alice"))
    assert r.json()["assigned_to"] is None
    assert cid in [c["id"] for c in http.get("/conversations?view=unassigned",
                                             headers=auth("alice")).json()]


def test_first_two_messages_of_new_conversation_arrive_concurrently(http, sql):
    conv = uid("race")
    results = concurrently(2, lambda i: send(http, payload(conversation_id=conv, text=f"m{i}")))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json()["conversation_id"] == results[1].json()["conversation_id"]
    rows = sql("""SELECT count(DISTINCT c.id) AS convs, count(m.id) AS msgs FROM conversations c
                  JOIN messages m ON m.conversation_id = c.id WHERE c.external_id = %s""", conv)
    assert rows[0] == {"convs": 1, "msgs": 2}


def test_same_conversation_id_in_two_workspaces_is_two_conversations(http):
    conv = uid("dup-conv")
    a = new_conversation(http, workspace="acme", conversation_id=conv)
    g = new_conversation(http, workspace="globex", conversation_id=conv)
    assert a != g


# ---- ordering ---------------------------------------------------------------

def test_out_of_order_delivery_is_shown_in_sent_order(http):
    conv = uid("ooo")
    t = {n: ago(100 - n * 10) for n in (1, 2, 3)}
    for n in (3, 1, 2):  # arrival order differs from send order
        send(http, payload(conversation_id=conv, text=f"t{n}", sent_at=t[n]))
    cid = send(http, payload(conversation_id=conv, message_id=uid(), text="t0", sent_at=ago(500))
               ).json()["conversation_id"]
    msgs = http.get(f"/conversations/{cid}", headers=auth("alice")).json()["messages"]
    assert [m["text"] for m in msgs] == ["t0", "t1", "t2", "t3"]
    summary = next(c for c in http.get("/conversations?view=unassigned", headers=auth("alice")).json()
                   if c["id"] == cid)
    assert summary["last_message"] == "t3"  # last by sent_at, not by arrival
    assert datetime.fromisoformat(summary["waiting_since"]) < t[1]


def test_identical_sent_at_falls_back_to_arrival_order(http):
    conv, same = uid("tie"), ago(5)
    for txt in ("first", "second"):
        cid = send(http, payload(conversation_id=conv, text=txt, sent_at=same)).json()["conversation_id"]
    msgs = http.get(f"/conversations/{cid}", headers=auth("alice")).json()["messages"]
    assert [m["text"] for m in msgs] == ["first", "second"]


# ---- validation -------------------------------------------------------------

@pytest.mark.parametrize("mutate", [
    lambda b: b.pop("message_id"),
    lambda b: b.pop("workspace"),
    lambda b: b.update(text=""),
    lambda b: b.update(text="   "),
    lambda b: b.update(message_id=""),
    lambda b: b.update(sent_at="not-a-date"),
    lambda b: b.update(sent_at="2026-01-01T10:00:00"),   # no timezone -> ambiguous
    lambda b: b.update(text="x" * 10_001),
])
def test_invalid_payload_is_422_and_not_stored(http, sql, mutate):
    body = payload()
    mutate(body)
    r = send(http, body)
    assert r.status_code == 422, r.text
    if body.get("message_id"):
        assert msg_count(sql, "acme", body["message_id"]) == 0


def test_non_json_body_is_422(http):
    r = http.post("/webhooks/inbound", content=b"{not json",
                  headers={"X-Webhook-Secret": SECRET, "content-type": "application/json"})
    assert r.status_code == 422


def test_unknown_workspace_is_422_and_logged(http, sql):
    body = payload(workspace="initech")
    assert send(http, body).status_code == 422
    assert sql("SELECT outcome FROM webhook_deliveries WHERE external_message_id = %s",
               body["message_id"])[0]["outcome"] == "unknown_workspace"


def test_sent_at_with_offset_is_normalised(http):
    conv = uid("tz")
    send(http, payload(conversation_id=conv, text="later", sent_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)))
    body = payload(conversation_id=conv, text="earlier")
    body["sent_at"] = "2026-01-01T15:00:00+05:30"   # = 09:30 UTC
    cid = send(http, body).json()["conversation_id"]
    msgs = http.get(f"/conversations/{cid}", headers=auth("alice")).json()["messages"]
    assert [m["text"] for m in msgs] == ["earlier", "later"]
