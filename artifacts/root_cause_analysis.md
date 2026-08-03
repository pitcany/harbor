# Root Cause Analysis — Open WebUI Tool Use

_2026-08-02. Confidence labels: **confirmed** (reproduced + explained),
**probable** (strong evidence, not live-reproduced), **weakness** (works, but
fragile or costly)._

## 1. harbor.boost backend down for weeks — confirmed, fixed

**Symptom:** `Cannot connect to host boost:8000` on every model-list refresh;
any boost-augmented model would fail.
**Root cause chain:** `services/boost/src` is bind-mounted into the container,
but Python deps are installed at image build. The repo gained
`import shortuuid` (pinned in `pyproject.toml`) after the image was built
(2026-05-01), so the app crash-looped on import. A first rebuild+recreate did
NOT fix it because compose declares an anonymous volume `- /boost/.venv` which
docker carries over on recreate, masking the rebuilt venv.
**Fix:** `harbor build boost` + `up -d --force-recreate --renew-anon-volumes`.
**Lesson encoded in runbook:** any boost dep bump needs the `--renew-anon-volumes` recreate.

## 2. Dead backend `172.17.0.1:8090` in the OpenAI URL list — confirmed, fixed

Leftover from the retired math-RAG shim generation (shims now live on
8091/8093/8094). Every model refresh logged a connection error and paid a
connect-timeout. Removed from `HARBOR_OPENAI_URLS` in `webui.env`.

## 3. Unbounded tool-call timeout — confirmed (code + fault test), fixed

`AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER` is unset ⇒ falls back to
`AIOHTTP_CLIENT_TIMEOUT=900` (deliberately huge to survive vLLM profile swaps).
Consequence: a hung tool server pins the chat and the UI spinner for 15
minutes. Set to 120 s in `webui.env` — long enough for playwright/crawl4ai and
arxiv downloads, bounded enough to fail visibly. (The 900 s model-request
timeout is intentionally preserved: cold vLLM swaps need it.)

## 4. Stale default model id — confirmed, fixed

`ui.default_models = "llama-applied"` survived a preset rename; new chats fell
back to whatever the client last used. Set to `local-applied`.

## 5. Native mode "does nothing" via bare API — confirmed, by-design, documented

`/api/chat/completions` without `chat_id`+`session_id`+message id skips the
server-side tool loop and proxies `tool_calls` to the caller (standard OpenAI
contract). The UI path executes tools. Consequence for operators: API smoke
tests that "return 200" prove nothing about UI tool execution — the regression
suite therefore drives the full chat-task path (`ui_mode`).

## 6. Model-preset toolIds are client-applied — confirmed, documented

`meta.toolIds` on a preset (e.g. `local-repo-ops` → `server:mcp-github`) is
merged into requests by the *frontend*. Server-side API calls must pass
`tool_ids` explicitly. Harness updated; noted in runbook.

## 7. Hallucinated tool calls when tools are missing — confirmed, model behavior

Native mode, prompt says "use your GitHub tool", no GitHub tools attached:
qwen3.6-27b fabricates `search_repositories` instead of declining. OWUI
surfaces `Error: Tool ... not found` and the model recovers honestly. Bounded;
mitigation is accurate per-profile tool sets (and the surfaced error is
arguably the right UX).

## 8. `coder` profile tool parser mismatch — probable, deliberately left

`vllm-tp.env.coder` pins `VLLM_TOOL_PARSER=hermes` for Qwen3-Coder-Next-80B
while its own comment says the model emits XML tool calls; vLLM 0.20.0 ships a
dedicated `qwen3_coder` parser. Not changed: validating requires evicting the
active 27B for a multi-minute swap, and the pin may have been intentional.
Validation procedure in `model_tool_compatibility.md`.

## 9. System-tool schema pressure — weakness, documented

Every UI chat injects ~19 built-in tools (notes/tasks/automations/calendar/
chat-search/knowledge/timestamps) into native requests in addition to selected
tool servers (116 ops / ~214 KB raw spec if all 16 servers were enabled at
once). With a 131 k-token context this is tolerable for qwen but is the main
context-pressure and tool-routing-noise driver. Options (not applied): disable
unused system features per user/group permissions; enable fewer tool servers
per chat.

## 10. Observability — weakness, partially addressed

`GLOBAL_LOG_LEVEL=DEBUG` floods logs with aiosqlite noise (real errors buried);
0.9.6 has no per-module levels (`SRC_LOG_LEVELS` is a dead legacy var), and the
DEBUG stream is currently the only place default-mode tool selection is
visible — so the level was kept, but the json-file log is now bounded
(50 MB × 3) and `diagnose_openwebui_tools.sh` extracts a deduplicated,
redacted error digest.

## 11. Leaked credential in tracked file — confirmed, scrubbed; rotation recommended

`services/webui/webui.env` (git-tracked) carried a bearer token for the Wolfram
mcpo (`172.18.0.1:8210`) in `TOOL_SERVER_CONNECTIONS`. The runtime config takes
the value from untracked `override.env` (`${HARBOR_WOLFRAM_KEY}`), so the
tracked copy was redundant. Blanked, and **the credential has since been
rotated** (2026-08-02): a new token was issued, `~/.config/wolfram-mcpo.key`
and `override.env` updated, and `wolfram-mcpo` + `webui` restarted. Verified
by probing the endpoint — the superseded token is refused with 403 and the
replacement authenticates, so the value in git history is inert.

That endpoint binds the Docker bridge address and is not internet-facing, which
bounded the exposure, but the token was confirmed live before rotation.

## 12. Fault behavior (401/429/500/malformed/huge/slow/unreachable/cancel)

See `artifacts/regression_results.md` — filled from the fault-injection run.
