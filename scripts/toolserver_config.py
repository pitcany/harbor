#!/usr/bin/env python3
"""Add/remove the fault-injection mock tool servers in Open WebUI config.

Every mutation first writes an exact backup of the current tool-server list to
services/webui/toolservers.bak-<ts>.json (mode 600, git-ignored dir pattern).

Usage:
  python3 toolserver_config.py add-mocks     # register mock-fault + mock-dead
  python3 toolserver_config.py remove-mocks  # remove any mock-* entries
  python3 toolserver_config.py restore <backup.json>
  python3 toolserver_config.py show          # redacted listing
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

OWUI_URL = os.environ.get("OWUI_URL", "http://127.0.0.1:33801")
BACKUP_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "webui")

MOCKS = [
    {
        "url": "http://172.17.0.1:8123",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": "",
        "info": {"id": "mock-fault", "name": "Mock Fault Injection",
                 "description": "Testing-only tools that deliberately fail (401/429/500/slow/huge/malformed). Never use unless explicitly asked to call a mock tool."},
        "config": {"enable": True, "access_grants": []},
    },
    {
        "url": "http://172.17.0.1:8125",
        "path": "openapi.json",
        "auth_type": "bearer",
        "key": "",
        "info": {"id": "mock-dead", "name": "Mock Dead Server",
                 "description": "Testing-only: a tool server that is unreachable."},
        "config": {"enable": True, "access_grants": []},
    },
]


def get_api_key():
    key = os.environ.get("OWUI_API_KEY")
    if key:
        return key
    code = ("import sqlite3;"
            "con=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);"
            "print(con.execute('select key from api_key limit 1').fetchone()[0])")
    return subprocess.run(["docker", "exec", "harbor.webui", "python3", "-c", code],
                          capture_output=True, text=True, timeout=30).stdout.strip()


def api(method, path, key, body=None):
    req = urllib.request.Request(
        f"{OWUI_URL}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def backup(connections):
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"toolservers.bak-{ts}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(connections, f, indent=1)
    print(f"backup written: {path}")
    return path


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    key = get_api_key()
    current = api("GET", "/api/v1/configs/tool_servers", key)["TOOL_SERVER_CONNECTIONS"]

    if cmd == "show":
        for c in current:
            print(f"- {c.get('info', {}).get('id', '?'):22s} {c['url']:40s} "
                  f"enabled={c.get('config', {}).get('enable')} key={'<set>' if c.get('key') else '(none)'}")
        return

    backup(current)
    if cmd == "add-mocks":
        ids = {c.get("info", {}).get("id") for c in current}
        new = current + [m for m in MOCKS if m["info"]["id"] not in ids]
    elif cmd == "remove-mocks":
        new = [c for c in current if not str(c.get("info", {}).get("id", "")).startswith("mock-")]
    elif cmd == "restore":
        with open(sys.argv[2]) as f:
            new = json.load(f)
    else:
        sys.exit(f"unknown command: {cmd}")

    api("POST", "/api/v1/configs/tool_servers", key, {"TOOL_SERVER_CONNECTIONS": new})
    print(f"tool server connections now: {len(new)}")


if __name__ == "__main__":
    main()
