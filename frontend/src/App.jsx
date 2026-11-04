import React, { useEffect, useMemo, useState } from "react";
import { makeApi } from "./api.js";
import Queue from "./components/Queue.jsx";
import Conversation from "./components/Conversation.jsx";

// No login screen (per spec): pick a seeded agent, or paste any token.
const DEMO_AGENTS = [
  ["acme-alice-token", "Alice (acme)"],
  ["acme-bob-token", "Bob (acme)"],
  ["globex-gina-token", "Gina (globex)"],
  ["globex-gus-token", "Gus (globex)"],
];

const load = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } };

export default function App() {
  // Token is per tab via ?token= so two tabs can be two agents; falls back to localStorage.
  const initial = new URLSearchParams(location.search).get("token") || load("token", DEMO_AGENTS[0][0]);
  const [token, setToken] = useState(initial);
  const [me, setMe] = useState(null);
  const [authError, setAuthError] = useState(null);
  const [selected, setSelected] = useState(null);
  const api = useMemo(() => makeApi(token), [token]);

  useEffect(() => {
    save("token", token);
    setSelected(null);
    setMe(null);
    setAuthError(null);
    api.me().then(setMe, (e) => setAuthError(e.message));
  }, [api, token]);

  return (
    <div className="app">
      <header>
        <strong>Agent Inbox</strong>
        <label>
          Signed in as{" "}
          <select value={token} onChange={(e) => setToken(e.target.value)}>
            {DEMO_AGENTS.map(([t, label]) => <option key={t} value={t}>{label}</option>)}
            {!DEMO_AGENTS.some(([t]) => t === token) && <option value={token}>custom token</option>}
          </select>
        </label>
        {me && <span className="muted">{me.name} · {me.workspace}</span>}
        {authError && <span className="error">{authError}</span>}
      </header>
      {me && (
        <main>
          <Queue api={api} selectedId={selected} onSelect={setSelected} />
          {selected
            ? <Conversation key={selected} api={api} me={me} id={selected} />
            : <section className="thread empty">Pick a conversation</section>}
        </main>
      )}
    </div>
  );
}
