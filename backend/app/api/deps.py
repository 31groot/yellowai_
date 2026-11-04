import hashlib

from fastapi import Header, HTTPException

from app.db.pool import get_pool
from app.schemas.agent import CurrentAgent


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_agent(authorization: str | None = Header(default=None)) -> CurrentAgent:
    """Bearer token -> agent. The workspace comes from here and nowhere else."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    with get_pool().connection() as conn:
        row = conn.execute(
            """SELECT a.id, a.name, a.workspace_id, w.slug AS workspace_slug
               FROM agents a JOIN workspaces w ON w.id = a.workspace_id
               WHERE a.token_hash = %s""",
            (hash_token(token.strip()),),
        ).fetchone()
    if row is None:
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
    return CurrentAgent(**row)
