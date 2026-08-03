# Config Inventory (redacted)

_All secrets replaced with `<REDACTED>`. Sources: tracked repo files, live container env, DB `config` table (2026-08-02)._

## Files that shape the WebUI

| File | Role | Git state |
|---|---|---|
| `services/compose.webui.yml` | container def, mounts, entrypoint | modified (adds `retrieval_utils_patched.py` mount) |
| `services/webui/webui.env` | env: engine URLs, TOOL_SERVER_CONNECTIONS (legacy seed), vector DB | modified (added :8093/:8094 to `HARBOR_OPENAI_URLS`) |
| `services/webui/override.env` | untracked local env (secrets) — mode 600 | untracked |
| `services/webui/configs/config.override.json` | JSON merged into DB config at boot | modified (embedder → gpu, search engine → brave) |
| `services/webui/seeds/models.json` + `seed_models.py` | workspace model presets seeded at boot | tracked + many local .bak |
| `services/mcpo/configs/mcpo.override.json` | mcpo MCP server definitions | modified (arxiv `[pro]` extra; formatting) |
| `services/webui/start_webui.sh` | entrypoint (merger, seeds, patches) | tracked |
| `services/webui/chroma_patched.py`, `retrieval_utils_patched.py` | source patches bind-mounted over 0.9.6 | tracked / **untracked** |

## Key runtime env (harbor.webui container)

```
GLOBAL_LOG_LEVEL=DEBUG            # noisy; aiosqlite DEBUG floods logs
AIOHTTP_CLIENT_TIMEOUT=900        # chat requests; sized for vLLM profile swaps
ENABLE_API_KEYS=True
VECTOR_DB=qdrant / QDRANT_URI=http://qdrant:6333
CONTENT_EXTRACTION_ENGINE=docling / DOCLING_SERVER_URL=http://docling:5001
OLLAMA_BASE_URLS=http://172.17.0.1:11434
HARBOR_OPENAI_URLS=http://172.18.0.1:4000/v1;http://172.17.0.1:8090/v1;   # 8090 dead
  http://172.17.0.1:8091/v1;http://172.17.0.1:8093/v1;http://172.17.0.1:8094/v1
HARBOR_OPENAI_KEYS=<REDACTED x5>
```

`webui.env` also carries a legacy `TOOL_SERVER_CONNECTIONS` JSON (8 entries, superseded by the DB config's 17) — **it embeds one live bearer key in a git-tracked file** (`af99…` for `172.18.0.1:8210`). Flagged for rotation/removal; the DB config references the same credential as `${HARBOR_WOLFRAM_KEY}` from untracked env instead.

## Effective DB config (`config` table, v1, updated 2026-08-01)

- `openai.api_base_urls`: boost, pipelines, litellm:4000, shims 8090/8091/8093/8094 (see architecture map; 2 dead)
- `rag`: ollama embedder `qwen3-embedding-4b-gpu`, hybrid off, reranker bge-v2-m3, top_k 50→10, qdrant
- `rag.web.search`: engine `brave`, count 8, `bypass_embedding_and_retrieval=false`, keys `<REDACTED>`
- `rag.web.loader`: external → `owui-crawl4ai-adapter:8000/load`
- `tool_server.connections`: 17 entries (16 enabled; `git` disabled) — names, routing descriptions and read-only scoping as in architecture map; bearer keys `<REDACTED>` (several intentionally empty)
- `code_execution` / `code_interpreter`: jupyter `owui-jupyter:8888`, token `<REDACTED>`, timeout 60
- `ui.default_models`: `llama-applied` (stale id)
- `task.model`: `qwen-local` (default + external); title & follow-up generation disabled
- `audio`: speaches STT/TTS (not in tool scope)

## Model-server configs

- `~/AI/services/vllm-tp.env` (active profile `qwen`): `Qwen/Qwen3.6-27B-FP8`, TP=2, 131072 ctx, fp8 KV, `VLLM_TOOL_PARSER=qwen3_xml`, `--reasoning-parser qwen3`; launcher adds `--enable-auto-tool-choice`.
- Other profiles: `.llama` (llama-3.3-70b AWQ), `.llama-fast` (spec-dec), `.deepseek-r1` (tool-calling off by design), `.coder`, `.baseline`, `.ornith`.
- `~/AI/llm-router/config/router.yaml`: profile swap orchestration, health wait 300 s.
- `~/AI/llm-router/litellm/config.yaml`: model fleet; `drop_params: true` global; Claude 5 models drop sampling params; keys via env `<REDACTED>`.

## mcpo servers (`services/mcpo/configs/mcpo.override.json`)

fetch (uvx mcp-server-fetch) · time (LA tz) · sequential-thinking · memory · filesystem(/workspace/AI) · search (ddg) · git · searxng (`http://harbor.searxng:8080`) · playwright (headless chromium, isolated) · arxiv (`arxiv-mcp-server[pro]`, storage /app/data/arxiv) · huggingface · duckdb (`:memory:`, home /workspace/AI, read-write)

## Backups present (pre-existing, from prior manual ops)

Numerous timestamped `.bak*` files alongside `webui.env`, `override.env`, `models.json`, `vllm-tp.env`, `litellm/config.yaml`; `webui.db.bak-*` full DB copies (June–July 2026); `vector_db.backup-*`.
