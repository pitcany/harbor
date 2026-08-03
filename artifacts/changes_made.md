# Changes Made — 2026-08-02

Every change is backed by evidence in `root_cause_analysis.md`, backed up
(suffix `bak-toolwork-20260802*`), and reversible per `rollback_plan.md`.

## System state changes

1. **Rebuilt `harbor-boost` image and recreated the container with
   `--renew-anon-volumes`.** Restores the boost backend (was crash-looping on
   `import shortuuid` for weeks; the anonymous `/boost/.venv` volume had masked
   an earlier rebuild). Verified: `webui → boost:8000/health = 200`, model-list
   errors gone.
2. **Recreated `harbor.webui`** to apply the config changes below. Data is
   bind-mounted; nothing was lost (verified chat/knowledge counts).

## Config file changes

3. `services/webui/webui.env`
   - `HARBOR_OPENAI_URLS`: removed dead `http://172.17.0.1:8090/v1` (retired
     shim; nothing listens; was erroring on every model refresh).
   - Added `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER=120` — tool calls previously
     inherited the 900 s global timeout; a hung tool pinned the chat for 15
     minutes. Verified by fault test F06 (before: >150 s and counting; after:
     bounded abort at ~120 s with a user-visible explanation).
   - Blanked the live Wolfram bearer key that sat in this git-tracked file
     (runtime uses `${HARBOR_WOLFRAM_KEY}` from untracked `override.env`;
     the tracked copy was redundant). **Rotation still recommended** — the key
     is in git history.
4. `services/webui/configs/config.override.json`
   - `ui.default_models`: `llama-applied` (nonexistent id) → `local-applied`.
5. `services/compose.webui.yml`
   - Added bounded logging (`json-file`, 50 MB × 3). The container logs at
     DEBUG (kept — it is currently the only visibility into default-mode tool
     selection) and previously grew without limit.

## New tooling (no runtime coupling)

6. `scripts/owui_tool_harness.py` — behavioral test harness driving the real
   chat path (API mode + UI-faithful `ui_mode` with chat creation/polling and
   automatic chat cleanup); ~20 failure classes; per-trial event timelines;
   secrets never logged.
7. `scripts/scenarios_baseline.json`, `scripts/scenarios_ui_native.json` —
   38 scenarios / ~70 trials covering the required matrix.
8. `scripts/mock_tool_server.py` — fault-injection tool server
   (401/429/500/slow/huge/malformed/echo).
9. `scripts/toolserver_config.py` — add/remove/restore tool-server
   connections with automatic exact backups.
10. `scripts/diagnose_openwebui_tools.sh` — read-only health report
    (exit 0/1/2), redacted.
11. `scripts/test_openwebui_tools.sh` — rerunnable regression suite
    (registers mocks for the run, always removes them).

## Backups created

- `services/webui/webui.db.bak-toolwork-20260802` (online SQLite backup, pre-change)
- `services/webui/webui.env.bak-toolwork-20260802-181800`
- `services/webui/configs/config.override.json.bak-toolwork-20260802-181800`
- `services/webui/toolservers.bak-20260802-181337.json` (+ later snapshots per mutation)
- `~/AI/services/vllm-tp.env.coder.bak-toolwork-20260802-181800` (file ultimately left unchanged)

## Considered and deliberately NOT changed

- `vllm-tp.env.coder` tool parser (`hermes` → `qwen3_coder`): probable defect,
  but validation requires evicting the resident model; the pin may be
  deliberate. Validation procedure documented.
- `function_calling: "none"` in math presets: no-op alias of `default` in
  0.9.6; harmless since those presets attach no tools.
- `GLOBAL_LOG_LEVEL=DEBUG`: kept (sole source of default-mode tool telemetry;
  0.9.6 has no per-module levels). Log growth now bounded instead.
- Open WebUI version (0.9.6 → 0.11.x exists): no upgrade — tool use works on
  0.9.6, and two source patches (`chroma_patched.py`,
  `retrieval_utils_patched.py`) are pinned to this version; an upgrade is a
  separate engagement.
- Tool-result size bounding (480 KB flowed through unbounded in F05): options
  are proxy-side truncation on :8212, or `rag.web.search`-style summarization;
  both change tool semantics — left for an explicit decision.
- No retry logic added anywhere: current behavior (single attempt, surfaced
  error) is safe for non-idempotent tools; retries without idempotency keys
  were explicitly avoided.
