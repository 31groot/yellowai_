"""Fire realistic webhook traffic at a running server: new conversations, out-of-order
delivery, and provider retries (duplicate message_ids).

Usage: python scripts/simulate.py [--url http://localhost:8000] [--every 4]
"""
import argparse
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config  # noqa: E402

NAMES = ["Asha", "Ben", "Chen", "Dara", "Eli", "Fatima", "Goran", "Hiro"]
LINES = ["Hello?", "My payment failed", "Where is my parcel?", "I need a refund",
         "The app keeps crashing", "Can I talk to a human?", "Thanks!", "Any update?"]


def post(client, url, body):
    r = client.post(f"{url}/webhooks/inbound", json=body,
                    headers={"X-Webhook-Secret": config.WEBHOOK_SECRET})
    print(f"{r.status_code} {r.json().get('status', r.text)[:40]:10} "
          f"{body['workspace']:6} {body['conversation_id']} {body['message_id']}: {body['text']}")


def burst(client, url):
    ws = random.choice(["acme", "globex"])
    conv = f"sim-{uuid.uuid4().hex[:6]}"
    name = random.choice(NAMES)
    base = datetime.now(timezone.utc)
    msgs = [dict(workspace=ws, conversation_id=conv, message_id=f"{conv}-{i}",
                 customer_name=name, text=random.choice(LINES),
                 sent_at=(base + timedelta(seconds=i)).isoformat()) for i in range(3)]
    order = msgs[:]
    random.shuffle(order)               # arrive out of order
    order.append(random.choice(msgs))   # and one provider retry
    for m in order:
        post(client, url, m)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--every", type=float, default=4.0, help="seconds between bursts; 0 = once")
    args = ap.parse_args()
    with httpx.Client() as client:
        while True:
            burst(client, args.url)
            if not args.every:
                break
            time.sleep(args.every)
