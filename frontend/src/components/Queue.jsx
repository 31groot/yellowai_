import React, { useEffect, useState } from "react";
import { usePolling } from "../hooks/usePolling.js";
import { since } from "../time.js";

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

function List({ api, view, title, selectedId, onSelect, now }) {
  const { data, error } = usePolling(() => api.list(view), [api, view]);
  return (
    <div className="queue-section">
      <h3>{title} {data && <span className="count">{data.length}</span>}</h3>
      {error && <div className="error">{error.message}</div>}
      {!data && !error && <div className="muted">Loading…</div>}
      {data?.length === 0 && <div className="muted">Nothing here</div>}
      <ul>
        {data?.map((c) => (
          <li key={c.id} className={c.id === selectedId ? "active" : ""} onClick={() => onSelect(c.id)}>
            <div className="row">
              <span className="name">{c.customer_name}</span>
              {c.waiting_since
                ? <span className="wait" title="Waiting since the oldest unanswered customer message">⏱ {since(c.waiting_since, now)}</span>
                : <span className="muted">replied</span>}
            </div>
            <div className="preview">{c.last_message}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function Queue(props) {
  const now = useNow();
  return (
    <aside className="queue">
      <List {...props} now={now} view="unassigned" title="Unassigned" />
      <List {...props} now={now} view="mine" title="Mine" />
    </aside>
  );
}
