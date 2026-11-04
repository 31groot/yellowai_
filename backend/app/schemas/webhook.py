from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


NonEmpty = Field(min_length=1, max_length=200)


class InboundMessage(BaseModel):
    """Payload the channel provider POSTs to /webhooks/inbound."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    workspace: str = NonEmpty
    conversation_id: str = NonEmpty
    message_id: str = NonEmpty
    customer_name: str = NonEmpty
    text: str = Field(min_length=1, max_length=10_000)
    sent_at: AwareDatetime  # must carry a timezone; naive timestamps are ambiguous


class WebhookAck(BaseModel):
    status: Literal["stored", "duplicate"]
    conversation_id: str  # our internal id
    message_id: str       # our internal id

    @classmethod
    def build(cls, status: str, conversation_id, message_id) -> "WebhookAck":
        return cls(status=status, conversation_id=str(conversation_id), message_id=str(message_id))

