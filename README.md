# Agent Inbox

A stripped-down, multi-tenant support inbox. Messages arrive by webhook, agents claim conversations and reply, and the UI polls for updates.

- **Backend:** FastAPI + PostgreSQL (psycopg 3, raw SQL)
- **Frontend:** React (Vite)
- **Tests:** pytest against a real Postgres and a real uvicorn server

```
backend/app/
  schemas/      Pydantic models (webhook payload, conversations, agents)
  webhooks/     POST /webhooks/inbound: secret check, dedupe, store, delivery log
  api/          auth dependency, conversation routes, repository (all SQL)
  db/           connection pool + schema.sql
backend/scripts/  setup_db.py (schema + seed), simulate.py (traffic generator)
backend/tests/    guarantee + edge-case tests
frontend/src/     React UI (Queue, Conversation, usePolling)
```

## Run it

Requirements: Python 3.11+, Node 18+, PostgreSQL 14+.

```bash
cp .env.example .env          # DATABASE_URL, TEST_DATABASE_URL, WEBHOOK_SECRET
make setup                    # venv, pip, npm, databases, schema + seed
make api                      # http://localhost:8000  (API docs at /docs)
make ui                       # http://localhost:5173  (in a second terminal)
make simulate                 # optional: stream webhook traffic (third terminal)
```

To skip the Vite dev server, run `make build` once. FastAPI then serves the built UI at http://localhost:8000.

If your Postgres isn't the Debian `pg_ctlcluster` kind, create the two databases yourself (`inbox` and `inbox_test`) and point `.env` at them.

## Seed

`make seed` (which runs `scripts/setup_db.py --demo`) **drops and recreates** the schema. It then creates:

| workspace | agent | token |
|---|---|---|
| acme | Alice | `acme-alice-token` |
| acme | Bob | `acme-bob-token` |
| globex | Gina | `globex-gina-token` |
| globex | Gus | `globex-gus-token` |

Only the SHA-256 of each token is stored. `--demo` also adds a few sample conversations, using the same code path as the webhook. In the UI, pick an agent from the dropdown. You can also open `/?token=acme-bob-token` in a second window so two agents run side by side.

```bash
curl -X POST localhost:8000/webhooks/inbound -H 'X-Webhook-Secret: dev-webhook-secret' \
  -H 'Content-Type: application/json' \
  -d '{"workspace":"acme","conversation_id":"wa-1","message_id":"m-1","customer_name":"Priya","text":"hi","sent_at":"2026-11-04T10:00:00Z"}'
curl localhost:8000/conversations?view=unassigned -H 'Authorization: Bearer acme-alice-token'
curl -X POST localhost:8000/conversations/<id>/claim -H 'Authorization: Bearer acme-alice-token'
curl -X POST localhost:8000/conversations/<id>/messages -H 'Authorization: Bearer acme-alice-token' \
  -H 'Content-Type: application/json' -d '{"text":"On it"}'
```

## API

This is the suggested API, with three additions:

| | |
|---|---|
| `POST /webhooks/inbound` | Requires `X-Webhook-Secret`. Responses: `200 {"status":"stored"\|"duplicate"}`, `401` bad secret, `422` invalid payload or unknown workspace. |
| `GET /conversations?view=unassigned\|mine` | Returns customer, last message (by `sent_at`), `waiting_since`, and assignee. |
| `GET /conversations/:id` | Returns the thread ordered by `sent_at`. |
| `POST /conversations/:id/claim` | `200` if you won or already own it. `409 {"error":"already_claimed","assigned_to":{…}}` otherwise. |
| `POST /conversations/:id/messages` | `201` for the assignee. `403` for anyone else in the same workspace. |
| `GET /me` *(added)* | Lets the UI show who the token belongs to. |
| `GET /healthz` *(added)* | DB liveness, for load balancers and monitoring. |
| `/api/*` *(added)* | Same routes under a prefix, so the UI can be served from the same origin. |

Two status-code choices are deliberate:
- **A duplicate webhook returns 200, not 409.** A non-2xx response makes the provider keep retrying something we already have.
- **Another workspace's conversation returns 404, not 403**, and so does a malformed ID. The response is byte-for-byte the same as for an ID that doesn't exist, so a caller can't probe for IDs.

## How the rules are enforced

1. **Exactly-once webhook storage.** The provider's `message_id` is the idempotency key, enforced by `UNIQUE(workspace_id, external_message_id)` and `INSERT … ON CONFLICT DO NOTHING`.
   - A concurrent retry blocks on the unique index until the first insert commits, then inserts nothing.
   - The key is not a content hash, so two genuine "ok" messages are both kept.
   - A duplicate has no side effects: its transaction is rolled back, so a retry with a changed `conversation_id` doesn't leave an empty conversation behind.
   - A new conversation is created by an upsert on `(workspace_id, external_id)`, which is safe when two first-messages arrive together.
   - The secret is compared with `hmac.compare_digest`, which takes the same time however much of the secret matches.

2. **Tenant isolation.**
   - The workspace comes only from the bearer token, never from the URL or body.
   - Every query filters by `workspace_id`.
   - As a backstop, a composite foreign key `(workspace_id, assigned_agent_id) → agents(workspace_id, id)` makes the database refuse an assignee from another workspace, even if a query has a bug.

3. **Claim races.** The claim is one compare-and-set statement:
   ```sql
   UPDATE conversations SET assigned_agent_id = :me, assigned_at = :now  -- :now from the app
   WHERE id = :id AND workspace_id = :ws AND assigned_agent_id IS NULL RETURNING id
   ```
   - The second concurrent UPDATE waits on the row lock. After the first commits, it re-checks `assigned_agent_id IS NULL` against the new row and matches nothing.
   - **Timestamps don't decide the winner.** Two requests can carry the same microsecond, and app servers' clocks drift by more than 1 ms. Also, "read, then write" is itself a race.
   - `assigned_at` is recorded for display and audit only.
   - The loser gets a 409 with the winner's name, and the UI shows "Too late — Alice already claimed this."

4. **Only the assignee replies.** The ownership check and the insert are a single `INSERT … SELECT … WHERE assigned_agent_id = :me`, so there's no gap between checking and writing.

5. **Ordering.** Threads sort by `sent_at, received_at, id`. That's the customer's send time, with arrival order only as a tie-break. The queue's "last message" is also chosen by `sent_at`. `waiting_since` is the oldest customer message sent after the last agent reply.

6. **Time is stamped by the app, never the database.** The schema has no `now()` or default timestamps, and a test checks this.
   - The webhook handler stamps `received_at` once, when the request arrives.
   - That one value is written to both the message and the `webhook_deliveries` row that stored it, and the two rows point at each other: `messages.webhook_delivery_id` ↔ `webhook_deliveries.message_id`.
   - Duplicate deliveries also point at the original message, each with its own arrival time.
   - `sent_at` is the customer's time from the provider and is never overwritten.
   ```sql
   -- every delivery of a message, in arrival order
   SELECT d.id, d.outcome, d.received_at, m.text, m.sent_at
   FROM webhook_deliveries d JOIN messages m ON m.id = d.message_id
   WHERE m.external_message_id = 'm-1' ORDER BY d.received_at;
   ```

## Tests

```bash
make test        # 66 tests, ~2s, against inbox_test (schema is recreated each run)
```

The three required proofs:

- **(a) Two concurrent claims → exactly one succeeds:** `tests/test_claims.py::test_two_concurrent_claims_exactly_one_wins`.
  - Two threads are released by a barrier, and this repeats 25 times.
  - It asserts one 200 and one 409, that the 409 names the winner, and that the DB agrees.
  - A second version runs the race directly on SQL with two raw connections.
  - The test was checked against a naive read-then-write claim and caught it (both requests got 200).
- **(b) Same `message_id` twice → one stored message:** `tests/test_webhooks.py::test_same_message_id_twice_stores_one_message`, plus a variant that posts the same ID 10 times concurrently.
- **(c) An acme agent gets nothing back for a globex conversation:** `tests/test_isolation.py::test_acme_agent_gets_nothing_for_globex_conversation`.
  - GET, claim and reply all return 404 with a body identical to a random ID's.
  - The conversation is absent from both queue views.
  - The DB is unchanged.

Edge cases also covered:
- missing, wrong, wrong-case or truncated secrets
- a retry with an edited body (the first version is kept)
- the same `message_id` or `conversation_id` in two workspaces (kept separate)
- two first-messages of a brand-new conversation racing
- out-of-order arrival, and equal `sent_at` values
- `sent_at` with a timezone offset; naive timestamps are rejected
- unknown workspace and malformed bodies
- malformed IDs and SQL-ish IDs (404, never 500)
- every bad `Authorization` shape
- the DB rejecting a cross-workspace assignee
- each message mapped to its delivery with an identical `received_at`; duplicates point at the original
- re-claiming your own conversation, and replies from a non-assignee or on an unassigned conversation

## How you'd tell it's broken

**Liveness.** `GET /healthz` returns 503 if Postgres is unreachable.

**The webhook delivery log.** Every delivery is a row in `webhook_deliveries` with an `outcome` and the raw payload:
```sql
SELECT outcome, count(*) FROM webhook_deliveries
WHERE received_at > now() - interval '15 minutes' GROUP BY 1;
```
- **`rejected_auth` spiking** means the secret was rotated on one side only, or someone is probing the endpoint.
- **`invalid` or `unknown_workspace` rising** means the provider changed its payload, or a workspace was set up wrong.
- **`stored` drops to zero while the provider says it is sending** means the webhook isn't reaching us (DNS, ingress, or a timeout). Expect a burst of `duplicate` once the provider retries.
- **A high ratio of `duplicate` to `stored`** means we're responding too slowly and the provider is timing out and retrying. Check request latency.

**Invariants that should always return 0 rows:**
```sql
-- assignee from another workspace (the FK should make this impossible)
SELECT c.id FROM conversations c JOIN agents a ON a.id = c.assigned_agent_id
WHERE a.workspace_id <> c.workspace_id;
-- a message filed under another workspace's conversation
SELECT m.id FROM messages m JOIN conversations c ON c.id = m.conversation_id
WHERE m.workspace_id <> c.workspace_id;
-- outbound reply by someone who isn't the assignee
SELECT m.id FROM messages m JOIN conversations c ON c.id = m.conversation_id
WHERE m.direction = 'outbound' AND m.author_agent_id <> c.assigned_agent_id;
-- inbound message not matching the delivery that stored it
SELECT m.id FROM messages m LEFT JOIN webhook_deliveries d ON d.id = m.webhook_delivery_id
WHERE m.direction = 'inbound' AND (d.id IS NULL OR d.received_at <> m.received_at);
-- conversations with no messages (an aborted webhook leaked a row)
SELECT c.id FROM conversations c WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id);
```

**User-visible symptoms:**
- The queue's "waiting" times keep growing while the provider is sending: inbound is broken.
- An agent sees "already claimed" on things nobody else touched: claims are broken.
- Messages out of order: someone sorted by `received_at`, or the provider sends `sent_at` in the wrong timezone. Check `received_at - sent_at` on recent rows.

**Logs.** Each delivery logs `webhook delivery outcome=… workspace=… message_id=…`, and uvicorn logs every 409.

## What I'd do next

- **HMAC over the raw body** (e.g. `X-Signature: sha256=…` plus a timestamp to block replays) instead of a static shared secret. That also proves the body wasn't tampered with.
- **Server-Sent Events** or `?since=` incremental polling instead of re-fetching whole lists every 3s.
- **Unassign / transfer**, and a queue that orders by service-level target.
- **Outbound `sent_at` is the app server's clock.** If the provider's clock runs ahead of ours, a reply can sort before the message it answers. A real system would deliver outbound messages through the provider and use its timestamp.
