import pytest

from tests.conftest import auth, new_conversation


def test_assignee_can_reply_and_it_is_stored_outbound(http, sql):
    cid = new_conversation(http)
    http.post(f"/conversations/{cid}/claim", headers=auth("alice"))
    r = http.post(f"/conversations/{cid}/messages", json={"text": "On it!"}, headers=auth("alice"))
    assert r.status_code == 201
    assert r.json()["direction"] == "outbound" and r.json()["author"]["name"] == "Alice"
    row = sql("SELECT direction, author_agent_id IS NOT NULL AS has_author FROM messages WHERE id = %s",
              r.json()["id"])[0]
    assert row == {"direction": "outbound", "has_author": True}
    thread = http.get(f"/conversations/{cid}", headers=auth("alice")).json()["messages"]
    assert thread[-1]["text"] == "On it!"


def test_other_agent_in_same_workspace_cannot_reply(http):
    cid = new_conversation(http)
    http.post(f"/conversations/{cid}/claim", headers=auth("alice"))
    r = http.post(f"/conversations/{cid}/messages", json={"text": "me too"}, headers=auth("bob"))
    assert r.status_code == 403 and "Alice" in r.json()["detail"]


def test_cannot_reply_to_unassigned(http):
    cid = new_conversation(http)
    r = http.post(f"/conversations/{cid}/messages", json={"text": "hi"}, headers=auth("alice"))
    assert r.status_code == 403


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {}, {"text": "x" * 10_001}])
def test_invalid_reply_is_422(http, body):
    cid = new_conversation(http)
    http.post(f"/conversations/{cid}/claim", headers=auth("alice"))
    assert http.post(f"/conversations/{cid}/messages", json=body,
                     headers=auth("alice")).status_code == 422


def test_reply_clears_waiting_since(http):
    cid = new_conversation(http)
    http.post(f"/conversations/{cid}/claim", headers=auth("bob"))
    http.post(f"/conversations/{cid}/messages", json={"text": "hello"}, headers=auth("bob"))
    mine = next(c for c in http.get("/conversations?view=mine", headers=auth("bob")).json()
                if c["id"] == cid)
    assert mine["waiting_since"] is None and mine["last_message"] == "hello"
