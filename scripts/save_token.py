"""Read token from /tmp/farther_token.json and save to .env."""
import json
import os

data = json.load(open("/tmp/farther_token.json"))
body = json.loads(data["body"])
token = "Bearer " + body["access_token"]

env_file = ".env"
lines = (
    [l for l in open(env_file).read().splitlines() if not l.startswith("FARTHER_AUTH_TOKEN")]
    if os.path.exists(env_file)
    else []
)
lines.append("FARTHER_AUTH_TOKEN=" + token)
open(env_file, "w").write("\n".join(lines) + "\n")
print(f"Token saved to .env (expires in {body['expires_in'] // 86400} days)")
