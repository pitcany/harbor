#!/usr/bin/env python3
"""Measure real tool-result sizes from stored Open WebUI chats.

Open WebUI persists every tool result as a citation source on the assistant
message (`sources[].document`, flagged `tool_result`), so the sizes are already
on disk. That makes this a reporter rather than an instrument: nothing is added
to the request path, and it back-fills history instead of waiting for new data.

The tool plane has no size bounding today. Before adding one, this answers the
question that decides whether it is needed at all, and for which tools: what do
real results actually weigh? The 480 KB figure in artifacts/regression_results.md
came from the fault mock (F05), not from any real server.

Read-only: opens the DB with mode=ro and reports **sizes only**, never result
content, so it is safe to run against chats containing private material.

Usage:
    python3 measure_tool_output_sizes.py [--db PATH] [--threshold BYTES] [--json]
"""

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict

DEFAULT_DB = "services/webui/webui.db"
# Rough context cost: ~4 bytes/token. 32 KB ≈ 8k tokens, i.e. the point where a
# single tool result starts meaningfully crowding a 32k-context local model.
DEFAULT_THRESHOLD = 32 * 1024


def iter_messages(chat):
    """Yield message dicts from both storage shapes Open WebUI uses."""
    for holder in (chat.get("messages"), (chat.get("history") or {}).get("messages")):
        if isinstance(holder, dict):
            holder = list(holder.values())
        if isinstance(holder, (list, tuple)):
            for m in holder:
                if isinstance(m, dict):
                    yield m


def collect(db_path):
    """Return {tool_name: [size, ...]} across every stored chat."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    sizes = defaultdict(list)
    try:
        rows = con.execute(
            "select chat from chat where chat like '%\"tool_result\"%'"
        )
        for (blob,) in rows:
            try:
                chat = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                continue  # a malformed chat blob should not abort the survey
            for msg in iter_messages(chat):
                for src in msg.get("sources") or []:
                    if not isinstance(src, dict) or not src.get("tool_result"):
                        continue
                    name = (src.get("source") or {}).get("name") or "<unnamed>"
                    for doc in src.get("document") or []:
                        sizes[name].append(len(str(doc)))
    finally:
        con.close()
    return sizes


def summarize(sizes, threshold):
    out = []
    for name, vals in sizes.items():
        vals = sorted(vals)
        out.append({
            "tool": name,
            "n": len(vals),
            "max": vals[-1],
            "median": int(statistics.median(vals)),
            "total": sum(vals),
            "over_threshold": sum(1 for v in vals if v >= threshold),
        })
    return sorted(out, key=lambda r: -r["max"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB, help="path to webui.db (read-only)")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help="byte size considered context-pressuring (default 32768)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    rows = summarize(collect(a.db), a.threshold)

    if a.json:
        print(json.dumps({"threshold": a.threshold, "tools": rows}, indent=2))
        return

    total = sum(r["n"] for r in rows)
    if not total:
        print("No stored tool results found. Tool results are only persisted for "
              "UI chats; API-driven calls leave nothing behind.")
        return

    print(f"{total} tool results across {len(rows)} tools "
          f"(threshold {a.threshold:,} bytes)\n")
    print(f"{'tool':44} {'n':>4} {'max':>10} {'median':>9} {'total':>12} {'over':>5}")
    for r in rows:
        print(f"{r['tool']:44} {r['n']:>4} {r['max']:>10,} {r['median']:>9,} "
              f"{r['total']:>12,} {r['over_threshold']:>5}")

    flagged = [r for r in rows if r["over_threshold"]]
    print()
    if flagged:
        print("Over threshold — candidates for bounding:")
        for r in flagged:
            print(f"  {r['tool']}: {r['over_threshold']}/{r['n']} results "
                  f"at up to {r['max']:,} bytes")
        print("\nSample sizes here are small; re-run after ordinary use before "
              "deciding. A tool absent from this table was simply never called "
              "in a stored chat — that is not evidence it is well behaved.")
    else:
        print("Nothing over threshold. On this evidence a bounding layer is not "
              "yet justified.")


if __name__ == "__main__":
    main()
