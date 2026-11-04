import psycopg

from app.api.repository import try_claim
from tests.conftest import TEST_DSN, auth, concurrently, new_conversation


# ---- (a) two concurrent claims ----------------------------------------------

def test_two_concurrent_claims_exactly_one_wins(http, sql):
    for _ in range(25):  # repeat to shake out timing-dependent bugs
        cid = new_conversation(http)
        a, b = concurrently(2, lambda i: http.post(f"/conversations/{cid}/claim",
                                                   headers=auth(["alice", "bob"][i])))
        codes = sorted([a.status_code, b.status_code])
        assert codes == [200, 409], (a.text, b.text)
        winner, loser = (a, b) if a.status_code == 200 else (b, a)
        winner_name = winner.json()["assigned_to"]["name"]
        body = loser.json()
        assert body["error"] == "already_claimed"
        assert body["assigned_to"]["name"] == winner_name   # loser's screen shows who has it
        assert winner_name in body["detail"]
        db = sql("""SELECT a.name FROM conversations c JOIN agents a ON a.id = c.assigned_agent_id
                    WHERE c.id = %s""", cid)
        assert db[0]["name"] == winner_name


def test_many_concurrent_claims_one_winner(http):
    cid = new_conversation(http)
    rs = concurrently(8, lambda i: http.post(f"/conversations/{cid}/claim",
                                             headers=auth(["alice", "bob"][i % 2])))
    # Alice's and Bob's requests: the winner's own extra clicks are idempotent 200s,
    # every one of the other agent's clicks is a 409. Never two different owners.
    owners = {r.json()["assigned_to"]["name"] for r in rs}
    assert len(owners) == 1
    assert sum(r.status_code == 409 for r in rs) == 4


def test_claim_race_at_sql_level(http, sql):
    """Same guarantee without HTTP in the way: two raw connections, one CAS each."""
    cid = new_conversation(http)
    ids = {r["name"]: r["id"] for r in sql("SELECT name, id FROM agents")}
    ws = sql("SELECT workspace_id FROM conversations WHERE id = %s", cid)[0]["workspace_id"]

    def claim(i):
        with psycopg.connect(TEST_DSN, autocommit=True) as conn:
            return try_claim(conn, ws, cid, ids[["Alice", "Bob"][i]])

    assert sorted(concurrently(2, claim)) == [False, True]


def test_claiming_own_conversation_again_is_ok(http):
    cid = new_conversation(http)
    assert http.post(f"/conversations/{cid}/claim", headers=auth("alice")).status_code == 200
    r = http.post(f"/conversations/{cid}/claim", headers=auth("alice"))
    assert r.status_code == 200 and r.json()["assigned_to"]["name"] == "Alice"


def test_claim_after_someone_else_has_it_is_409(http):
    cid = new_conversation(http)
    http.post(f"/conversations/{cid}/claim", headers=auth("alice"))
    r = http.post(f"/conversations/{cid}/claim", headers=auth("bob"))
    assert r.status_code == 409 and r.json()["assigned_to"]["name"] == "Alice"


def test_claimed_conversation_moves_between_views(http):
    cid = new_conversation(http)
    http.post(f"/conversations/{cid}/claim", headers=auth("bob"))

    def ids(who, view):
        return {c["id"] for c in http.get(f"/conversations?view={view}", headers=auth(who)).json()}

    assert cid in ids("bob", "mine")
    assert cid not in ids("bob", "unassigned")
    assert cid not in ids("alice", "unassigned")
    assert cid not in ids("alice", "mine")


def test_new_message_on_claimed_conversation_keeps_assignee(http):
    from tests.conftest import payload, send, uid
    conv = uid("keep")
    cid = send(http, payload(conversation_id=conv)).json()["conversation_id"]
    http.post(f"/conversations/{cid}/claim", headers=auth("alice"))
    send(http, payload(conversation_id=conv, text="follow-up"))
    r = http.get(f"/conversations/{cid}", headers=auth("alice")).json()
    assert r["assigned_to"]["name"] == "Alice" and r["messages"][-1]["text"] == "follow-up"
