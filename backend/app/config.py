import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/inbox")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "dev-webhook-secret")
