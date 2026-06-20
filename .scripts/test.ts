/// <reference lib="deno.ns" />

// Entry point for `harbor dev test`.
//
// The orchestrator lives at tests/run.ts — this file is just the dispatch
// hook wired into run_harbor_dev() in harbor.sh (which runs .scripts/<name>.ts).
// Keeping the implementation in tests/ co-locates it with the containers,
// suites, fixtures, and artifacts it drives.

await import("../tests/run.ts");
