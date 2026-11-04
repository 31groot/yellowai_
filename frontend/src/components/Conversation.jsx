import React, { useEffect, useRef, useState } from "react";
import { usePolling } from "../hooks/usePolling.js";
import { clock } from "../time.js";

export default function Conversation({ api, me, id }) {
  const { data: conv, error, refresh } = usePolling(() => api.get(id), [api, id]);
  const [notice, setNotice] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef(null);
  const count = conv?.messages.length;

  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [count]);

  if (error) return <section className="thread"><div className="error">{error.message}</div></section>;
  if (!conv) return <section className="thread muted">Loading…</section>;

  const assignee = conv.assigned_to;
  const mine = assignee?.id === me.id;

  async function claim() {
    setBusy(true); setNotice(null);
    try {
      await api.claim(id);
    } catch (e) {
      // 409 carries who won, so the loser sees it immediately.
      setNotice(e.status === 409 ? `Too late — ${e.body.assigned_to.name} already claimed this.` : e.message);
    } finally {
      setBusy(false);
      refresh();
    }
  }

  async function send(e) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true); setNotice(null);
    try {
      await api.reply(id, text);
      setText("");
    } catch (err) {
      setNotice(err.message);
    } finally {
      setBusy(false);
      refresh();
    }
  }

  return (
    <section className="thread">
      <div className="thread-head">
        <h2>{conv.customer_name}</h2>
        {assignee
          ? <span className={`badge ${mine ? "mine" : "other"}`}>{mine ? "Assigned to you" : `Assigned to ${assignee.name}`}</span>
          : <button onClick={claim} disabled={busy}>Claim</button>}
      </div>
      {notice && <div className="notice">{notice}</div>}
      <ol className="messages">
        {conv.messages.map((m) => (
          <li key={m.id} className={m.direction}>
            <div className="bubble">{m.text}</div>
            <div className="meta">{m.direction === "outbound" ? m.author?.name : conv.customer_name} · {clock(m.sent_at)}</div>
          </li>
        ))}
        <li ref={bottom} />
      </ol>
      <form className="composer" onSubmit={send}>
        <input value={text} onChange={(e) => setText(e.target.value)} disabled={!mine || busy}
          placeholder={mine ? "Type a reply…" : assignee ? `Only ${assignee.name} can reply` : "Claim to reply"} />
        <button disabled={!mine || busy || !text.trim()}>Send</button>
      </form>
    </section>
  );
}
