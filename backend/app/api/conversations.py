from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api import repository as repo
from app.api.deps import current_agent
from app.db.pool import get_pool
from app.schemas.agent import AgentOut, AgentRef, CurrentAgent
from app.schemas.conversation import (ClaimConflict, ConversationDetail, ConversationSummary,
                                      MessageOut, ReplyIn)

router = APIRouter(tags=["conversations"])

NOT_FOUND = "conversation not found"  # same message whether it doesn't exist or is another tenant's


def _ref(agent_id, name) -> AgentRef | None:
    return AgentRef(id=agent_id, name=name) if agent_id else None


def _detail(conn, agent: CurrentAgent, conv_id) -> ConversationDetail:
    c = repo.get_conversation(conn, agent.workspace_id, conv_id)
    if c is None:
        raise HTTPException(404, NOT_FOUND)
    msgs = repo.get_messages(conn, agent.workspace_id, conv_id)
    return ConversationDetail(
        id=c["id"], customer_name=c["customer_name"],
        assigned_to=_ref(c["assigned_agent_id"], c["agent_name"]),
        assigned_at=c["assigned_at"], created_at=c["created_at"],
        messages=[MessageOut(id=m["id"], direction=m["direction"], text=m["text"],
                             sent_at=m["sent_at"], received_at=m["received_at"],
                             author=_ref(m["author_agent_id"], m["author_name"])) for m in msgs],
    )


def _id_or_404(raw: str):
    conv_id = repo.parse_id(raw)
    if conv_id is None:  # malformed id is indistinguishable from a missing one
        raise HTTPException(404, NOT_FOUND)
    return conv_id


@router.get("/me", response_model=AgentOut)
def me(agent: CurrentAgent = Depends(current_agent)):
    return AgentOut(id=agent.id, name=agent.name, workspace=agent.workspace_slug)


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(view: Literal["unassigned", "mine"] = "unassigned",
                       agent: CurrentAgent = Depends(current_agent)):
    with get_pool().connection() as conn:
        rows = repo.list_conversations(conn, agent.workspace_id, agent.id, view)
    return [ConversationSummary(
        id=r["id"], customer_name=r["customer_name"],
        assigned_to=_ref(r["agent_id"], r["agent_name"]),
        last_message=r["last_message"], last_message_at=r["last_message_at"],
        waiting_since=r["waiting_since"]) for r in rows]


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: str, agent: CurrentAgent = Depends(current_agent)):
    cid = _id_or_404(conv_id)
    with get_pool().connection() as conn:
        return _detail(conn, agent, cid)


@router.post("/conversations/{conv_id}/claim", response_model=ConversationDetail,
             responses={409: {"model": ClaimConflict}})
def claim(conv_id: str, agent: CurrentAgent = Depends(current_agent)):
    cid = _id_or_404(conv_id)
    with get_pool().connection() as conn:
        won = repo.try_claim(conn, agent.workspace_id, cid, agent.id, datetime.now(timezone.utc))
        detail = _detail(conn, agent, cid)  # 404 if not in this workspace
    if won or (detail.assigned_to and detail.assigned_to.id == agent.id):
        return detail  # claiming something you already own is a no-op success
    body = ClaimConflict(
        detail=f"Already claimed by {detail.assigned_to.name}",
        assigned_to=detail.assigned_to, assigned_at=detail.assigned_at)
    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))


@router.post("/conversations/{conv_id}/messages", response_model=MessageOut, status_code=201)
def reply(conv_id: str, body: ReplyIn, agent: CurrentAgent = Depends(current_agent)):
    cid = _id_or_404(conv_id)
    with get_pool().connection() as conn:
        row = repo.insert_reply(conn, agent.workspace_id, cid, agent.id, body.text,
                               datetime.now(timezone.utc))
        if row is None:
            c = repo.get_conversation(conn, agent.workspace_id, cid)
            if c is None:
                raise HTTPException(404, NOT_FOUND)
            who = c["agent_name"] or "nobody (claim it first)"
            raise HTTPException(403, f"only the assigned agent can reply; assigned to {who}")
    return MessageOut(id=row["id"], direction=row["direction"], text=row["text"],
                      sent_at=row["sent_at"], received_at=row["received_at"],
                      author=AgentRef(id=agent.id, name=agent.name))
