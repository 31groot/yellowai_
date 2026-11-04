-- Agent inbox schema. Idempotent: drops and recreates everything.
DROP TABLE IF EXISTS webhook_deliveries, messages, conversations, agents, workspaces CASCADE;

CREATE TABLE workspaces (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE,
    name text NOT NULL
);

CREATE TABLE agents (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces(id),
    name         text NOT NULL,
    token_hash   text NOT NULL UNIQUE,            -- sha256(token); raw tokens are never stored
    UNIQUE (workspace_id, id)                      -- target for the composite FK below
);

CREATE TABLE conversations (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES workspaces(id),
    external_id       text NOT NULL,              -- provider's conversation_id
    customer_name     text NOT NULL,
    assigned_agent_id uuid,
    assigned_at       timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, external_id),
    -- The DB itself refuses an assignee from a different workspace.
    FOREIGN KEY (workspace_id, assigned_agent_id) REFERENCES agents (workspace_id, id)
);
CREATE INDEX conversations_queue_idx ON conversations (workspace_id, assigned_agent_id);

CREATE TABLE messages (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid NOT NULL REFERENCES workspaces(id),
    conversation_id     uuid NOT NULL REFERENCES conversations(id),
    direction           text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    external_message_id text,                     -- provider's message_id (NULL for outbound)
    author_agent_id     uuid REFERENCES agents(id),
    text                text NOT NULL,
    sent_at             timestamptz NOT NULL,     -- when the customer/agent sent it: the ordering key
    received_at         timestamptz NOT NULL DEFAULT now(),  -- when it reached us
    -- Idempotency for provider retries. NULLs (outbound) never collide.
    UNIQUE (workspace_id, external_message_id),
    CHECK ((direction = 'inbound') = (external_message_id IS NOT NULL)),
    CHECK ((direction = 'outbound') = (author_agent_id IS NOT NULL))
);
CREATE INDEX messages_thread_idx ON messages (conversation_id, sent_at, received_at, id);

-- One row per webhook delivery attempt, duplicates and rejects included.
CREATE TABLE webhook_deliveries (
    id                  bigserial PRIMARY KEY,
    received_at         timestamptz NOT NULL DEFAULT now(),
    workspace_slug      text,
    external_message_id text,
    message_id          uuid REFERENCES messages(id),   -- set when this delivery stored the message
    outcome             text NOT NULL CHECK (outcome IN
                          ('stored', 'duplicate', 'rejected_auth', 'invalid', 'unknown_workspace')),
    payload             jsonb
);
CREATE INDEX webhook_deliveries_outcome_idx ON webhook_deliveries (outcome, received_at);
