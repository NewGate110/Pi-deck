"""Generate the local configuration once. Run on the Pi during installation."""
import getpass
import argparse
import json
import os
from pathlib import Path
import secrets
from werkzeug.security import generate_password_hash

parser = argparse.ArgumentParser(description="Create private Pi Deck configuration; never overwrites existing files.")
parser.add_argument("--config", default="config.json", help="Output path (parent directory must exist)")
parser.add_argument("--username", default="admin")
args = parser.parse_args()
if not args.username.strip():
    parser.error("username must not be empty")
path = Path(args.config)
if path.exists():
    raise SystemExit("config.json already exists; refusing to overwrite it.")
password = getpass.getpass("New dashboard password (at least 12 characters): ")
if len(password) < 12 or password != getpass.getpass("Repeat password: "):
    raise SystemExit("Passwords must match and contain at least 12 characters.")
config = json.loads(Path(__file__).with_name("config.example.json").read_text())
config.update(secret_key=secrets.token_hex(32), password_hash=generate_password_hash(password),
              username=args.username.strip(), bots=[])
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as stream:
    json.dump(config, stream, indent=2)
print(f"Created {path}. Sign in and use Add bot to register your bots.")
