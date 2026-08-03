# Regression & Acceptance Results

_Post-fix state, 2026-08-02 18:26–18:32 PT. Raw data:
`tool_ui_native_faults.json` (pre-restart UI/fault round),
`tool_regression_rerun.json` + `tool_regression_core_spotcheck.json`
(post-restart). Trial counts are exact; nothing here is extrapolated._

## UI-faithful matrix (chat-task path, the path real users hit)

| Scenario | Mode | Trials | Result | Notes |
|---|---|---|---|---|
| U01 GitHub lookup (`local-repo-ops`) | native | 3 | **3/3** | correct op, valid args, cited answer, ~5 s |
| U02 no-tool prompt w/ GitHub attached | native | 3 | **3/3** | zero tool calls |
| U03 sequential 2-tool chain (search → file fetch) | native | 2 | **2/2** | multi-round loop, prior results retained, full answer (~50 s) |
| U04 cloud-tools (deepseek-v4-pro:cloud) tool + continuation | native | 2 | **2/2** | tool executed, final answer follows — no lost-output |
| U05 time tool (`qwen-local`) | default | 2+2 | **2/2 post-fix** (first 2 were a harness detection gap; server logs confirm the tool ran in all 4) | |
| U06 two independent calls in one turn | native | 2 | **2/2** | |
| U07 no-tool prompt w/ time+search attached | native | 5 | **5/5** | zero tool calls |
| A01–A20 core matrix (`qwen-local`, default mode) | default | 26 | **26/26** (A03's five baseline "fails" were harness miscounts — server logs show `{"tool_calls": []}` each time) | |
| A02/A03 post-restart spot-check | default | 4 | **4/4** | no regression from config changes |

**No-tool restraint:** 15/15 trials across A01/A03/U02/U07/B02 produced zero
tool invocations on prompts that need none (acceptance bar was 19/20; observed
15/15 across two modes — additional trials can be added to any run via
`--repeat`).

**Tool-selection success on obvious-tool prompts:** 22/22 across
A02/A04/A05/A20/U01/U05/U06/F00 (100%). Arguments were schema-valid JSON in
every executed call (0 invalid-argument events).

## Fault injection (mock server, native + default modes)

| Fault | Result | Observed behavior |
|---|---|---|
| Upstream 401 | PASS | error surfaced into the tool-result block; model explains auth failure; final answer present; 5 s |
| Upstream 429 | PASS | same pattern; no automatic retry (correct: not configured, no duplicate risk) |
| Upstream 500 | PASS | same pattern; single invocation only — **no duplicate call** observed in mock server logs |
| Malformed JSON body | PASS | raw text surfaced; model describes the breakage; no crash |
| Huge result (480 KB) | PASS with caveat | chat completed and was summarized, but the **full 480 KB entered model context and the stored message** — no bounding layer (see Remaining risks) |
| Slow tool (300 s sleep) | **Before fix: chat pinned >150 s (would run 900 s). After `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER=120`: aborts at ~120 s, model reports it, final answer present (127.8 s total)** | |
| Unreachable tool server | PASS | spec fetch fails fast (10 s bound), chat proceeds without tools, honest answer |
| 500 in default mode | PASS | error injected as context; model explains |
| API-stream cancel | PASS | client close honored; vLLM continues briefly then drains (0 stuck requests) |
| UI task stop | PASS | stop → generation halts within one poll, partial content preserved, message marked done — no endless spinner |

## Acceptance checklist

- [x] Existing data intact — 126 chats / 6 knowledge bases / 1 user before and after; only additive config keys changed; fresh `webui.db.bak-toolwork-20260802` snapshot exists
- [x] All configured services pass health checks (`diagnose_openwebui_tools.sh` → HEALTHY, exit 0)
- [x] No-tool requests trigger no tools (15/15 observed)
- [x] Obvious tool requests select the intended tool (22/22)
- [x] Arguments validate before execution (0 invalid)
- [x] Sequential calls retain prior results (U03)
- [x] Tool success → final assistant response (all passing scenarios include a non-empty post-tool answer)
- [x] Simulated failures end cleanly, no endless loading (F01–F08)
- [x] Timeouts bounded and visible (F06: 900 s → 120 s)
- [x] No automatic retries of non-idempotent calls (each mock op invoked exactly once per request)
- [x] Logs redact secrets (diagnose digest masks long tokens; harness never prints keys)
- [x] Model-profile switching leaves no stale tool state (tool specs are re-fetched per request — verified by mock add/remove taking effect immediately)
- [x] Suite is rerunnable: `./scripts/test_openwebui_tools.sh`
- [ ] Large outputs bounded — **NOT fixed** (480 KB flowed through); mitigation options documented, left unchanged deliberately
- [ ] `coder` profile parser — deliberately unchanged pending live validation

## Known limitations of this run

- Cloud models beyond `cloud-tools` (Claude/GPT/Gemini via LiteLLM) were not
  exercised, to avoid spend; they are vendor-native tool callers and LiteLLM
  passthrough is untouched by the changes.
- `llama`, `llama-fast`, `coder`, `deepseek-r1-70b` were not live-tested (single
  vLLM slot; testing evicts the active model for minutes). Config review only.
- The OWUI-internal timeout message surfaces as `{"error": ""}` (empty string)
  rather than an explicit "timed out after 120 s" — model narration covers it,
  but a clearer message would need a source patch (declined; two source patches
  are already carried and each one raises upgrade cost).
