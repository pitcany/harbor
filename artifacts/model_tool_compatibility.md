# Model × Tool-Calling Compatibility

_Empirical where the model could be tested without disrupting the stack (the
single vLLM slot means untested profiles would need a multi-minute GPU swap);
config-derived otherwise. 2026-08-02._

## Classification

| Model (OWUI id) | Backend | Server-side parser | Verdict | Evidence |
|---|---|---|---|---|
| `qwen-local` / `qwen` (Qwen3.6-27B-FP8) | vLLM (`qwen3_xml` + `--enable-auto-tool-choice`) | yes | **Strong native tool caller** — prefer `function_calling: native` | 20+ trials: correct selection, valid JSON args, multi-tool, multi-round, restraint on no-tool prompts; native ≈5 s vs ≈20 s in default mode |
| `local-repo-ops` (profile → qwen-local) | vLLM | yes | **Native (already configured)** | U01/U02/U03: GitHub search + file fetch chains work end-to-end |
| `llama` / `llama-fast` (Llama-3.3-70B AWQ) | vLLM (`llama3_json`) | yes | **Parser correct, but NOT dependable for autonomous tool use** — see the pathology note below | live probe 2026-08-02: parser 3/3; behaviour unacceptable on 6/6 no-tool prompts |
| `coder` (Qwen3-Coder-Next-80B AWQ) | vLLM (`qwen3_coder`, **fixed** 2026-08-02) | yes | **Was defective, now correct.** `hermes` could not read Qwen3-Coder's XML dialect, so calls leaked into `message.content` as text | live probe: `hermes` 0/3, `qwen3_coder` 3/3 with valid args |
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

## Llama-3.3 tool-mode pathology (measured 2026-08-02)

The `llama3_json` parser is correct — 3/3 structured tool calls with valid
arguments. The **model's behaviour once tools are attached** is the problem.
All at temperature 0, single `get_current_time` tool offered, 2 trials each:

| Prompt | With tools attached | Without tools |
|---|---|---|
| "Why is the sky blue?" | **calls `get_current_time{"timezone":"America/New_York"}`** (2/2) | — |
| "Write a haiku about autumn leaves." | **refuses**: "requires a function that generates a haiku" (2/2) | writes the haiku fine |
| "What is 17 * 23?" | **refuses**: "exceeds the limitations of the functions" (2/2) | — |
| "What time is it in Tokyo?" | correct call, correct args (2/2) | — |

So attaching a single tool makes Llama-3.3-70B (a) fire an unrelated tool on a
plain knowledge question and (b) refuse tasks it answers perfectly well with no
tools attached. It behaves as if the tool list were an exhaustive definition of
what it is allowed to do. Deterministic, not sampling noise.

**Recommendation:** do not attach tools to `llama` / `llama-fast` profiles for
general chat. Either keep them tool-free (they are good plain-chat models), or
restrict them to prompts that genuinely need the attached tool. `qwen-local`
showed perfect restraint on the same class of prompts (15/15) and is the right
choice for any tool-enabled profile.

`llama-fast` shares the identical model and parser, differing only by
`VLLM_SPEC_CONFIG` (Llama-3.2-1B drafter, 4 speculative tokens), so the parser
result — and the tool-mode pathology above — carry over unchanged.

## `llama-fast` speculative decoding (validated 2026-08-02)

Single-stream, greedy, 300 output tokens, median of 3 trials, measured against
plain `llama` on the same prompts (`scripts/bench_decode.py`):

| Workload | `llama` | `llama-fast` | Speedup | Token acceptance |
|---|---|---|---|---|
| Prose (explain a derailleur) | 52.26 tok/s | 57.80 tok/s | **1.11×** | 43.8% (1.75 of 4 per draft) |
| Code (parse ISO-8601 duration) | 52.29 tok/s | 80.21 tok/s | **1.53×** | 70.3% (2.81 of 4 per draft) |

The profile's claimed "~1.3–1.7× single-stream gen speed" **holds for code and
does not hold for prose**. That is the expected shape: a 1B drafter predicts
structured, low-entropy text well and free-form prose poorly, and the speedup
tracks acceptance rate almost exactly.

Correctness verified: at temperature 0 the first 160 characters of output were
byte-identical to plain `llama` on both workloads, which is what speculative
decoding guarantees — it is a latency optimisation, not a different sampler.

Operationally sound: the llm-router swap to `llama-fast` completed in 148 s,
the drafter loaded, and vLLM exposed `spec_decode` counters throughout. Worth
using for code generation; not worth the profile swap for general chat.

## Re-checking any profile's parser

```bash
llmctl vllm <alias>                                    # evicts the resident model
python3 ~/.harbor/scripts/probe_tool_parser.py --trials 3
```

Exit 0 means structured `tool_calls`; exit 1 means the call leaked into
`message.content` as text, i.e. the parser does not match the model. Neither
vLLM nor Open WebUI reports this as an error, which is why it needs probing.
