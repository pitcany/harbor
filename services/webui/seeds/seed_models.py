#!/usr/bin/env python3
"""
Seed Open WebUI model presets from a JSON file.

Reads /app/backend/data/seeds/models.json (an array of preset definitions)
and upserts each into the `model` table of /app/backend/data/webui.db.

The seed file is the source of truth: every boot, listed presets are
overwritten with the file's contents. Presets not in the file are left
untouched. Delete a preset by removing it from the file AND from the DB
manually (or set is_active=false in the file to keep it but disable).

Schema (per row in models.json):
{
  "id": str,                 # webui model id, e.g. "llama-tools"
  "base_model_id": str,      # the upstream model to wrap, e.g. "llama-3.3-70b-fp8"
  "name": str,               # display name
  "meta": dict,              # webui meta JSON (description, capabilities, system, ...)
  "params": dict,            # webui params JSON (function_calling, temperature, system, ...)
  "is_active": bool          # default true
}
"""
import json
import os
import sqlite3
import sys
import time

DB = "/app/backend/data/webui.db"
SEED = "/app/backend/data/seeds/models.json"


def main() -> int:
    if not os.path.exists(SEED):
        print(f"[seed_models] no seed file at {SEED}, skipping")
        return 0
    if not os.path.exists(DB):
        print(f"[seed_models] db not found at {DB}, skipping (first boot?)")
        return 0
    try:
        presets = json.load(open(SEED))
    except json.JSONDecodeError as e:
        print(f"[seed_models] ERROR: seed file is invalid JSON: {e}", file=sys.stderr)
        return 1
    if not isinstance(presets, list):
        print("[seed_models] ERROR: seed file must be a JSON array", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB)
    # Pick any user_id for ownership; admin user is canonical.
    admin = conn.execute("SELECT id FROM user WHERE role='admin' LIMIT 1").fetchone()
    if not admin:
        print("[seed_models] no admin user yet, skipping (first boot?)")
        return 0
    admin_uid = admin[0]
    now = int(time.time())

    upserted = 0
    for p in presets:
        mid = p["id"]
        is_active = 1 if p.get("is_active", True) else 0
        meta_json = json.dumps(p.get("meta", {}))
        params_json = json.dumps(p.get("params", {}))

        existing = conn.execute(
            "SELECT 1 FROM model WHERE id = ?", (mid,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE model SET base_model_id=?, name=?, meta=?, params=?, "
                "updated_at=?, is_active=? WHERE id=?",
                (p["base_model_id"], p["name"], meta_json, params_json,
                 now, is_active, mid),
            )
            print(f"[seed_models] updated preset: {mid}")
        else:
            conn.execute(
                "INSERT INTO model "
                "(id, user_id, base_model_id, name, meta, params, "
                "created_at, updated_at, is_active) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (mid, admin_uid, p["base_model_id"], p["name"],
                 meta_json, params_json, now, now, is_active),
            )
            print(f"[seed_models] inserted preset: {mid}")
        upserted += 1
    conn.commit()
    print(f"[seed_models] {upserted} preset(s) applied from {SEED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
