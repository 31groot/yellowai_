// Thin fetch wrapper. Errors carry the HTTP status and parsed body (e.g. the 409 payload).
export class ApiError extends Error {
  constructor(status, body) {
    super(body?.detail && typeof body.detail === "string" ? body.detail : `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

export function makeApi(token) {
  async function call(method, path, body) {
    const res = await fetch(`/api${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }
  return {
    me: () => call("GET", "/me"),
    list: (view) => call("GET", `/conversations?view=${view}`),
    get: (id) => call("GET", `/conversations/${id}`),
    claim: (id) => call("POST", `/conversations/${id}/claim`),
    reply: (id, text) => call("POST", `/conversations/${id}/messages`, { text }),
  };
}
