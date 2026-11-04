import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.conversations import router as conversations_router
from app.db import pool as db
from app.webhooks.router import router as webhooks_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if db.pool is None:
        db.open_pool()
    yield
    db.close_pool()


app = FastAPI(title="Agent Inbox", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/healthz")
def healthz():
    try:
        with db.get_pool().connection() as conn:
            conn.execute("SELECT 1")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"database unreachable: {e.__class__.__name__}")
    return {"status": "ok"}


# Routes live at the root (as in the spec) and under /api (what the React app calls).
for prefix in ("", "/api"):
    app.include_router(webhooks_router, prefix=prefix, include_in_schema=not prefix)
    app.include_router(conversations_router, prefix=prefix, include_in_schema=not prefix)

# Serve the built React app if present (`npm run build` in frontend/).
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
