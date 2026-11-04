from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent import AgentRef


class MessageOut(BaseModel):
    id: UUID
    direction: Literal["inbound", "outbound"]
    text: str
    sent_at: datetime
    received_at: datetime
    author: AgentRef | None = None


class ConversationSummary(BaseModel):
    id: UUID
    customer_name: str
    assigned_to: AgentRef | None
    last_message: str | None
    last_message_at: datetime | None
    waiting_since: datetime | None  # latest customer message not yet answered; None if answered


class ConversationDetail(BaseModel):
    id: UUID
    customer_name: str
    assigned_to: AgentRef | None
    assigned_at: datetime | None
    created_at: datetime
    messages: list[MessageOut]


class ReplyIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=10_000)


class ClaimConflict(BaseModel):
    error: Literal["already_claimed"] = "already_claimed"
    detail: str
    assigned_to: AgentRef
    assigned_at: datetime
