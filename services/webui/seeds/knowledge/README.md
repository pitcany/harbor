# WebUI Knowledge seeds

This directory describes Knowledge collections that the WebUI loads for RAG.
Each collection lives in a subdirectory containing a `manifest.json` listing
its source files (paths on the host filesystem) and ingest hints.

## Layout

```
knowledge/
├── README.md                       # this file
└── <collection-id>/
    └── manifest.json               # collection metadata + source list
```

## How a collection becomes live in WebUI

The `manifest.json` is **descriptive**, not auto-applied — Harbor doesn't yet
have an automatic ingest step on cold boot. To actually load a collection
into WebUI:

1. **Verify the embedding backend is reachable.** WebUI's `RAG_EMBEDDING_ENGINE`
   (see `services/webui/override.env`) determines how files get vectorized.
   For `engine=ollama`, the host's ollama service must be running and have
   the model in `RAG_EMBEDDING_MODEL` pulled (`ollama pull nomic-embed-text`).
2. **Mint a WebUI API key.** Open WebUI → user menu → Account → API Keys → New.
3. **Run the ingest script** (see `scripts/seed-knowledge.sh`, TBD).
4. **Attach the collection** to one or more model presets by setting
   `meta.knowledge: [{...collection ref...}]` in `seeds/models.json`.

## Why source files are referenced, not copied

The manifest lists `host_path` rather than embedding the file contents:

- Avoids duplicating large docs (some are >80 KB) across two repos.
- Lets the source files evolve in their canonical home (`~/AI/docs/`,
  `~/AI/CLAUDE.md`) and get re-embedded on demand.
- Makes the manifest review-friendly (small, declarative).

The tradeoff: the manifest is only meaningful on a host where the listed
paths exist. That's fine for this single-machine setup; for portable
configurations, a future revision could check files into this directory
directly under `<collection-id>/files/`.

## Collections defined so far

| ID | Attach to preset | Source count | Notes |
|---|---|---|---|
| `ai-stack-runbook` | `llama-rag` | 11 | ~/AI operational docs + CLAUDE.md |
