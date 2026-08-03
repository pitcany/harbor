# Operator Runbook — Open WebUI Tool Use

## Daily / on-suspicion commands

```bash
cd ~/.harbor

# Full read-only health report (containers, backends, tool servers, config sanity).
# Exit 0 healthy / 1 warnings / 2 failures.
./scripts/diagnose_openwebui_tools.sh

# Behavioral regression suite (drives real chat completions; local models only
# except two short cloud-tools calls). ~30-45 min full, ~10 min quick.
./scripts/test_openwebui_tools.sh          # full: core + UI-native + fault injection
./scripts/test_openwebui_tools.sh --quick  # core scenarios, single trial each
```

The regression suite registers two mock tool servers (`mock-fault`, `mock-dead`)
in the WebUI config for the duration of the run and removes them afterwards.
If a run is killed hard, clean up with:

```bash
python3 scripts/toolserver_config.py remove-mocks
```

## Service management

```bash
./harbor.sh ps                      # containers
./harbor.sh up webui                # (re)create webui after config edits
docker logs harbor.webui --since 10m 2>&1 | grep '| ERROR ' | grep -v aiosqlite
systemctl status vllm-tp ollama     # local inference units
llmctl vllm <preset>                # swap the vLLM model (evicts current!)
```

After editing `services/webui/webui.env` or
`services/webui/configs/config.override.json`, run `./harbor.sh up webui` —
the entrypoint re-merges JSON config into the DB and re-seeds model presets
on every boot.

## Where things live

| Thing | Location |
|---|---|
| Effective runtime config | DB `config` table (merged from `services/webui/configs/*.json` at boot) |
| Model presets (source of truth) | `services/webui/seeds/models.json` (re-seeded every boot) |
| Tool server list | DB config `tool_server.connections`; inspect with `python3 scripts/toolserver_config.py show` |
| Tool-call timeout | `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER` in `services/webui/webui.env` (120 s) |
| vLLM profiles (incl. tool parsers) | `~/AI/services/vllm-tp.env.*` |
| LiteLLM fleet | `~/AI/llm-router/litellm/config.yaml` |
| Test harness | `scripts/owui_tool_harness.py` + `scripts/scenarios_*.json` |
| Fault mock server | `scripts/mock_tool_server.py` (binds 172.17.0.1:8123) |

## Interpreting harness output

Each trial prints `PASS/FAIL (<class>)`. Classes:
`no_tool_requested`, `wrong_tool`, `invalid_args`, `unexpected_tool_use`,
`tool_error_surfaced`, `tool_error_no_answer`, `no_final_answer`, `timeout`,
`http_error`, `ok_cancelled`, `ok`. Full per-trial timelines (request → first
byte → tool call → done) are in the JSON results under `timeline`.

## Adding a tool server (checklist)

1. Serve `openapi.json` reachable from the `harbor.webui` container
   (container DNS like `mcpo:8000`, or `172.18.0.1:<port>` / `172.17.0.1:<port>`
   for host services — never `localhost`).
2. Add the connection in `services/webui/configs/config.override.json`
   (`tool_server.connections`) with a distinct `info.id` and a description that
   says when to use AND when not to use it; secrets as `${ENV_VAR}`
   placeholders resolved from untracked `override.env`.
3. `./harbor.sh up webui`, then `./scripts/diagnose_openwebui_tools.sh` — the
   new server must appear with its op count.
4. Add a scenario to `scripts/scenarios_baseline.json` exercising one read-only
   op, and run `./scripts/test_openwebui_tools.sh --quick`.

## Known operational gotchas

- **Do not enable tools in a `llama` / `llama-fast` chat.** The parser is fine,
  but with any tool attached Llama-3.3-70B fires unrelated tools on plain
  questions and *refuses* tasks it handles perfectly with no tools ("requires a
  function that generates a haiku"). No preset attaches tools to these models —
  it only happens if you toggle a tool on manually in the chat. Measurements in
  `model_tool_compatibility.md`. Use `qwen-local` for anything tool-enabled.
- Selecting a *cold* local model (llama / coder / deepseek-r1-70b) triggers a
  vLLM profile swap: multi-minute first response while the 70–80B loads. This
  is by design (llm-router); don't kill the request.
- `harbor.boost` bind-mounts `services/boost/src` but installs deps at image
  build; after a repo update that adds imports, rebuild AND recreate with
  fresh anonymous volumes:
  `./harbor.sh build boost && $(./harbor.sh cmd boost) up -d --force-recreate --renew-anon-volumes boost`
- Deleting the WebUI container log resets `docker logs` history; the json-file
  log is capped (50 MB × 3) since 2026-08-02.
- `webui.db` backups: `webui.db.bak-toolwork-20260802` is the most recent
  known-good full snapshot (taken online via the SQLite backup API).
