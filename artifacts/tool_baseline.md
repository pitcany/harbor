# Tool-Use Baseline (before fixes)

_Run: 2026-08-02 17:54–18:08 PT, via `scripts/owui_tool_harness.py` against the live
`/api/chat/completions` path. Raw data: `artifacts/tool_baseline.json` (39 trials).
A second, corrected round (UI-faithful native mode + fault injection) is reported in
`artifacts/regression_results.md`._

## Model under test: `qwen-local` (Qwen3.6-27B-FP8 on vLLM, via LiteLLM), default/compat function-calling

| Scenario | Result | Notes |
|---|---|---|
| A01 no-tool conversation | 2/2 | no tool calls, clean answers, 7–18 s |
| A02 obvious tool (time) | 3/3 | correct tool, valid `{"timezone": "Asia/Tokyo"}` args every time |
| A03 tools attached, none needed | 5/5* | model returned `{"tool_calls": []}` every trial — correct restraint. (*Recorded as FAIL by a harness bug, fixed: the empty selection log line was miscounted as a call. Re-verified from server logs: zero tools executed, haiku answered directly.) |
| A04 required string arg (fetch) | 2/2 | correct URL arg |
| A05 optional/enum args (convert_time) | 2/2 | correct source/target timezones |
| A06 two different tools in one request | 2/2 | time + ddg search both called, both results in answer |
| A08 multiple independent calls | 2/2 | two `get_current_time` calls (Tokyo + London) |
| A09 empty search result | 1/1 | honest "nothing found" answer |
| A10 invalid input (bogus timezone) | 1/1 | tool errored, model explained the timezone is not real |
| A11 unknown tool id attached | 1/1 | gracefully ignored, direct answer |
| A16 large result (Wikipedia fetch) | 1/1 | fetch tool paginates (`start_index`), bounded context |
| A18 built-in web_search feature | 1/1 | Brave search → RAG → cited answer, 86 s |
| A20 tools introduced late in a long chat | 2/2 | correct call despite 7-turn unrelated history |

Latency: median ≈ 20 s end-to-end for one tool round (≈7 s of it is the extra
tool-selection call in default mode).

## Model: `local-repo-ops` (native FC) and `cloud-tools` — first-round results were invalid as run

B01–B03/E01 initially ran through the **bare API** path, which (correctly, per
OpenAI semantics) returns `tool_calls` to the caller without executing them —
Open WebUI's server-side native tool loop only engages for UI-style requests
(chat_id + session_id + message id). Additionally, model-preset `toolIds` are
attached **client-side by the UI**, not server-side, so these runs offered the
model no GitHub tools at all. Two real findings fell out of this:

1. **F-B01 (confirmed, model behavior):** with a tool-hinting prompt and no
   matching tool attached, qwen3.6-27b in native mode fabricates a plausible
   tool call (`search_repositories`) rather than declining; Open WebUI answers
   `Error: Tool "search_repositories" not found.` and the model then recovers
   with an honest "I don't have GitHub tools" answer. Bounded, non-hanging, but
   noisy.
2. **F-B02 (confirmed, platform behavior):** every UI native-mode request
   injects ~19 built-in system tools (notes, tasks, automations, calendar,
   chat search, timestamps, knowledge) in addition to whatever the user
   selected — permanent schema overhead in every native chat.

The corrected UI-faithful runs (U01–U07) are in the regression report.

## Model: `auto-math-neighbor` (RAG shim, no tools)

D01: 1/1 — RAG-grounded math answer with citations, no tool leakage. 84 s
(shim + rerank + 27B generation).

## Failure classes exercised at baseline

Covered here: no-tool restraint, wrong/absent tool detection, invalid args,
empty results, invalid input, unknown tool id, large result, late tools.
Covered in the fault round (regression report): upstream 401/429/500,
malformed JSON, unreachable server, slow tool/timeout, huge payload,
cancellation (API + UI task stop), duplicate/idempotency observations.

## Environment defects found independent of scenarios

- `harbor.boost` backend crash-looping (`ModuleNotFoundError: shortuuid`) —
  stale image vs bind-mounted source. **Fixed during baseline** (rebuild +
  `--renew-anon-volumes` recreate; the anonymous `/boost/.venv` volume was
  masking the rebuilt venv).
- Dead backend `http://172.17.0.1:8090/v1` in `HARBOR_OPENAI_URLS` — connection
  error logged on every model-list refresh.
- `ui.default_models = "llama-applied"` — model id no longer exists.
- Tool-call HTTP timeout = 900 s (inherited from `AIOHTTP_CLIENT_TIMEOUT`); a
  hung tool server pins a chat for 15 minutes.
- `GLOBAL_LOG_LEVEL=DEBUG` + unbounded json-file docker logging.
- One live bearer key in git-tracked `services/webui/webui.env`.
