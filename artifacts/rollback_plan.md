# Rollback Plan

Every change made in the 2026-08-02 tool-use engagement is individually
reversible. Backups use suffix `bak-toolwork-20260802*`.

## 1. webui env / config changes

Files changed (tracked in git — `git diff` shows exact deltas):

- `services/webui/webui.env` — removed dead `:8090` backend, blanked one
  leaked bearer key, added `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER=120`
- `services/webui/configs/config.override.json` — `ui.default_models`:
  `llama-applied` → `local-applied`
- `services/compose.webui.yml` — added bounded json-file logging (50m × 3)

Rollback:

```bash
cd ~/.harbor
cp -p services/webui/webui.env.bak-toolwork-20260802-181800 services/webui/webui.env
cp -p services/webui/configs/config.override.json.bak-toolwork-20260802-181800 \
      services/webui/configs/config.override.json
git checkout -- services/compose.webui.yml     # or git diff to review first
./harbor.sh up webui
```

(`git checkout` of all three files also works since the pre-change state was
the uncommitted working tree captured in the `.bak` copies; prefer the `.bak`
copies which are exact.)

## 2. boost image rebuild

Old image behavior (crash-looping) has no value; but to revert the recreate:

```bash
$(./harbor.sh cmd boost) up -d --force-recreate boost
```

Rebuilding from any older commit of `services/boost/` restores prior code.

## 3. Tool-server connection list (mock servers)

The regression suite adds/removes `mock-fault` and `mock-dead` automatically.
Manual restore of the exact pre-engagement list:

```bash
python3 scripts/toolserver_config.py restore services/webui/toolservers.bak-20260802-181337.json
```

## 4. Database

No schema or data migrations were performed. Config-table writes were limited
to the `tool_server.connections` key (see 3). Full known-good snapshot if ever
needed:

```bash
./harbor.sh down webui   # stop writer first
cp -p services/webui/webui.db.bak-toolwork-20260802 services/webui/webui.db
rm -f services/webui/webui.db-wal services/webui/webui.db-shm
./harbor.sh up webui
```

**Warning:** restoring the DB snapshot discards chats/settings created after
2026-08-02 18:04 PT. Use only for corruption recovery.

## 5. vLLM profile files

`~/AI/services/vllm-tp.env.coder` — **not changed** (parser left at `hermes`
pending live validation; a dated `.bak-toolwork` copy exists anyway).

## 6. New files (safe to delete outright)

`scripts/owui_tool_harness.py`, `scripts/mock_tool_server.py`,
`scripts/toolserver_config.py`, `scripts/diagnose_openwebui_tools.sh`,
`scripts/test_openwebui_tools.sh`, `scripts/scenarios_*.json`, `artifacts/*`.
None are referenced by the running system.
