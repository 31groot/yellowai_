import uuid

import pytest

from tests.conftest import auth, new_conversation


# ---- (c) acme agent gets nothing back for a globex conversation --------------

def test_acme_agent_gets_nothing_for_globex_conversation(http, sql):
    gid = new_conversation(http, workspace="globex", text="globex secret")
    before = sql("SELECT assigned_agent_id FROM conversations WHERE id = %s", gid)
    msgs_before = sql("SELECT count(*) AS n FROM messages WHERE conversation_id = %s", gid)

    for who in ("alice", "bob"):
        get = http.get(f"/conversations/{gid}", headers=auth(who))
        claim = http.post(f"/conversations/{gid}/claim", headers=auth(who))
        reply = http.post(f"/conversations/{gid}/messages", json={"text": "hi"}, headers=auth(who))
        for r in (get, claim, reply):
            assert r.status_code == 404, r.text
            assert "globex secret" not in r.text
            assert r.json() == {"detail": "conversation not found"}  # same as a random id
        for view in ("unassigned", "mine"):
            listed = http.get(f"/conversations?view={view}", headers=auth(who)).json()
            assert gid not in {c["id"] for c in listed}

    assert sql("SELECT assigned_agent_id FROM conversations WHERE id = %s", gid) == before
    assert sql("SELECT count(*) AS n FROM messages WHERE conversation_id = %s", gid) == msgs_before


def test_globex_agent_cannot_touch_acme_conversation_even_when_claimed(http):
    aid = new_conversation(http, workspace="acme")
    http.post(f"/conversations/{aid}/claim", headers=auth("alice"))
    assert http.get(f"/conversations/{aid}", headers=auth("gina")).status_code == 404
    assert http.post(f"/conversations/{aid}/messages", json={"text": "x"},
                     headers=auth("gina")).status_code == 404


def test_cross_tenant_response_is_indistinguishable_from_missing(http):
    gid = new_conversation(http, workspace="globex")
    real = http.get(f"/conversations/{gid}", headers=auth("alice"))
    fake = http.get(f"/conversations/{uuid.uuid4()}", headers=auth("alice"))
    assert (real.status_code, real.json()) == (fake.status_code, fake.json())


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "1", "' OR 1=1 --", "%00"])
def test_malformed_ids_are_404_not_500(http, bad_id):
    assert http.get(f"/conversations/{bad_id}", headers=auth("alice")).status_code == 404
    assert http.post(f"/conversations/{bad_id}/claim", headers=auth("alice")).status_code == 404


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer acme-alice-token-x"},
    {"Authorization": "Bearer nope"},
    {"Authorization": "Basic acme-alice-token"},
    {"Authorization": "acme-alice-token"},
])
def test_missing_or_bad_token_is_401(http, headers):
    assert http.get("/conversations", headers=headers).status_code == 401
    assert http.get("/me", headers=headers).status_code == 401


def test_queue_only_lists_own_workspace(http, sql):
    for who, ws in (("alice", "acme"), ("gina", "globex")):
        listed = {c["id"] for c in http.get("/conversations?view=unassigned", headers=auth(who)).json()}
        own = {str(r["id"]) for r in sql("""SELECT c.id FROM conversations c JOIN workspaces w
                                            ON w.id = c.workspace_id WHERE w.slug = %s
                                            AND c.assigned_agent_id IS NULL""", ws)}
        assert listed == own


def test_db_rejects_cross_workspace_assignee(http, sql):
    """Defence in depth: even a buggy query can't assign a globex agent to acme work."""
    import psycopg
    aid = new_conversation(http, workspace="acme")
    gina = sql("SELECT id FROM agents WHERE name = 'Gina'")[0]["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        sql("UPDATE conversations SET assigned_agent_id = %s WHERE id = %s", gina, aid)


def test_me_reports_workspace_from_token(http):
    assert http.get("/me", headers=auth("gus")).json()["workspace"] == "globex"
