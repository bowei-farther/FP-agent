"""Check if the FARTHER_AUTH_TOKEN in .env is still valid (>1 day remaining).

Exit 0 — token is valid, skip fetch.
Exit 1 — token missing, expired, or expiring soon, fetch needed.
"""
import base64
import json
import os
import sys
import time

env_file = ".env"
token = None

if os.path.exists(env_file):
    for line in open(env_file).read().splitlines():
        if line.startswith("FARTHER_AUTH_TOKEN="):
            token = line.split("=", 1)[1].removeprefix("Bearer ").strip()
            break

if not token:
    print("No token found — fetching...")
    sys.exit(1)

try:
    pad = token.split(".")[1] + "=="
    exp = json.loads(base64.urlsafe_b64decode(pad))["exp"]
    remaining = exp - time.time()
    days = int(remaining // 86400)
    hours = int(remaining % 86400 // 3600)
    if remaining > 86400:
        print(f"Token valid, expires in {days}d {hours}h — skipping fetch")
        sys.exit(0)
    else:
        print(f"Token expiring in {days}d {hours}h — refreshing...")
        sys.exit(1)
except Exception as e:
    print(f"Token check failed ({e}) — fetching...")
    sys.exit(1)
