"""Timestamps are stamped by the app, never by the DB, and each inbound message maps
exactly to the webhook delivery that stored it."""
import time

from tests.conftest import auth, concurrently, new_conversation, payload, send


def test_db_generates_no_timestamps(sql):
    rows = sql("""SELECT table_name, column_name, column_default FROM information_schema.columns
                  WHERE table_schema = 'public' AND column_default ILIKE ANY
                        (ARRAY['%%now()%%', '%%current_timestamp%%', '%%clock_timestamp%%'])""")
    assert rows == []


def test_message_maps_to_its_delivery_with_identical_received_at(http, sql):
    body = payload()
    msg_id = send(http, body).json()["message_id"]
    row = sql("""SELECT m.received_at AS m_at, d.received_at AS d_at, d.outcome,
                        d.message_id = m.id AS back_link, d.payload->>'text' AS text
                 FROM messages m JOIN webhook_deliveries d ON d.id = m.webhook_delivery_id
                 WHERE m.id = %s""", msg_id)[0]
    assert row["m_at"] == row["d_at"]
    assert row["outcome"] == "stored" and row["back_link"] and row["text"] == body["text"]


def test_duplicate_deliveries_map_to_the_original_message(http, sql):
    body = payload()
    first = send(http, body).json()["message_id"]
    time.sleep(0.01)
    send(http, body)
    rows = sql("""SELECT outcome, message_id::text AS mid, received_at FROM webhook_deliveries
                  WHERE external_message_id = %s ORDER BY id""", body["message_id"])
    assert [(r["outcome"], r["mid"]) for r in rows] == [("stored", first), ("duplicate", first)]
    assert rows[1]["received_at"] > rows[0]["received_at"]  # each delivery keeps its own arrival time
    m = sql("SELECT received_at FROM messages WHERE id = %s", first)[0]
    assert m["received_at"] == rows[0]["received_at"]       # the message keeps the first arrival


def test_concurrent_duplicates_leave_exactly_one_stored_delivery(http, sql):
    body = payload()
    concurrently(6, lambda _: send(http, body))
    rows = sql("""SELECT outcome, count(*) AS n FROM webhook_deliveries
                  WHERE external_message_id = %s GROUP BY 1""", body["message_id"])
    assert {r["outcome"]: r["n"] for r in rows} == {"stored": 1, "duplicate": 5}


def test_every_inbound_message_has_a_delivery_and_outbound_has_none(http, sql):
    cid = new_conversation(http)
    http.post(f"/conversations/{cid}/claim", headers=auth("alice"))
    r = http.post(f"/conversations/{cid}/messages", json={"text": "hi"}, headers=auth("alice")).json()
    out = sql("SELECT webhook_delivery_id, sent_at = received_at AS same FROM messages WHERE id = %s", r["id"])[0]
    assert out == {"webhook_delivery_id": None, "same": True}
    orphans = sql("""SELECT count(*) AS n FROM messages m LEFT JOIN webhook_deliveries d
                     ON d.id = m.webhook_delivery_id
                     WHERE m.direction = 'inbound' AND (d.id IS NULL OR d.received_at <> m.received_at)""")
    assert orphans[0]["n"] == 0


def test_claim_records_assigned_at(http):
    cid = new_conversation(http)
    r = http.post(f"/conversations/{cid}/claim", headers=auth("bob")).json()
    assert r["assigned_at"] is not None
