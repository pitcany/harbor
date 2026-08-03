# Model × Tool-Calling Compatibility

_Empirical where the model could be tested without disrupting the stack (the
single vLLM slot means untested profiles would need a multi-minute GPU swap);
config-derived otherwise. 2026-08-02._

## Classification

| Model (OWUI id) | Backend | Server-side parser | Verdict | Evidence |
|---|---|---|---|---|
| `qwen-local` / `qwen` (Qwen3.6-27B-FP8) | vLLM (`qwen3_xml` + `--enable-auto-tool-choice`) | yes | **Strong native tool caller** — prefer `function_calling: native` | 20+ trials: correct selection, valid JSON args, multi-tool, multi-round, restraint on no-tool prompts; native ≈5 s vs ≈20 s in default mode |
| `local-repo-ops` (profile → qwen-local) | vLLM | yes | **Native (already configured)** | U01/U02/U03: GitHub search + file fetch chains work end-to-end |
| `llama` / `llama-fast` (Llama-3.3-70B AWQ) | vLLM (`llama3_json`) | yes | **Native capable — untested live** (not resident); parser correctly configured | config review only |
| `coder` (Qwen3-Coder-Next-80B AWQ) | vLLM (`hermes` parser) | suspect | **Probable defect**: Qwen3-Coder emits its own XML tool format; the `hermes` parser expects `<tool_call>` JSON. vLLM 0.20.0 ships a dedicated `qwen3_coder` parser. Validate at next swap (see below) | config review; profile comment itself says "emits XML" while pinning hermes |
| `deepseek-r1-70b` | vLLM (no tool parser — intentional) | no | **Not dependable for autonomous tool use.** Native-mode tools against it will 400; default mode works (prompt-based). Keep tools detached; use as reasoning-only | config + vLLM semantics |
| `cloud-tools` (deepseek-v4-pro:cloud) | Ollama cloud | n/a | Native per profile — see regression report U04 for the continuation-after-tool result | tested |
| math shims (`*-math-rag` via :8091/:8093/:8094) | shim + RAG | n/a | **No tools by design**; `function_calling: "none"` in presets is a no-op alias of `default` in 0.9.6 (code only tests `== "native"`); harmless since no tools are attached | code review `middleware.py:2474ff` |
| Claude / GPT / Gemini / Venice via LiteLLM | cloud | n/a | Strong native callers (vendor-side); LiteLLM `drop_params` configured. Untested here to avoid spend; treat as native | config review |

## Notes

- **Default (compat) mode** uses the task model (`qwen-local`) to pre-select
  tools via a JSON prompt. It is reliable in our matrix (13/13 scenario types)
  but adds ~7–15 s per request and injects tool results as `<context>` rather
  than proper tool messages. Use it for backends without a tool parser.
- **Native mode** on qwen-local is faster, supports multi-round chains
  (model → tool → model → tool → answer), and showed perfect restraint in
  no-tool prompts (U02/U07). Recommended for all vLLM-qwen-backed profiles.
- **Hallucinated tool calls**: in native mode with a tool-hinting prompt but no
  matching tool attached, qwen3.6 fabricates a plausible tool name. Open WebUI
  surfaces `Error: Tool "<name>" not found.` and the model recovers with an
  honest answer. Mitigation: keep per-profile tool sets accurate; don't rely on
  prompt text to gate tools.
- **System-tool injection**: UI chats add ~19 built-in tools (notes, tasks,
  automations, calendar, chat search, knowledge, timestamps) to every native
  request on top of user-selected servers. With several tool servers enabled
  simultaneously this is the main context-pressure driver (mcp-github alone is
  14 ops / ~27 KB spec).

## Validating the `coder` parser change (when convenient)

```bash
# 1. swap (evicts the current model for several minutes):
llmctl vllm coder
# 2. probe a native tool call directly:
curl -s http://127.0.0.1:8003/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "qwen3-coder-next-80b",
  "messages": [{"role":"user","content":"What time is it in Tokyo? Use the tool."}],
  "tools": [{"type":"function","function":{"name":"get_current_time","description":"Get time in a timezone","parameters":{"type":"object","properties":{"timezone":{"type":"string"}},"required":["timezone"]}}}]
}' | jq '.choices[0].message.tool_calls'
# null / text-wrapped pseudo-calls => parser mismatch confirmed; then edit
# ~/AI/services/vllm-tp.env.coder: VLLM_TOOL_PARSER=qwen3_coder and re-swap.
```
