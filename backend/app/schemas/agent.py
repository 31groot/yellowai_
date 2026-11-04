from uuid import UUID

from pydantic import BaseModel


class AgentRef(BaseModel):
    id: UUID
    name: str


class AgentOut(AgentRef):
    workspace: str


class CurrentAgent(BaseModel):
    """Resolved from the bearer token; the only source of workspace identity."""

    id: UUID
    name: str
    workspace_id: UUID
    workspace_slug: str
