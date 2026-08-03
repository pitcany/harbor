#!/usr/bin/env python3
"""Single-stream decode benchmark for the local vLLM endpoint.

Speculative decoding is a single-stream latency optimisation, so it is measured
with one request at a time, greedy decoding, and a fixed output length. Reports
median output tokens/sec across trials, plus vLLM's speculative-decode counters
when the served profile has a drafter configured.

Usage: python3 bench_decode.py [--trials 3] [--max-tokens 300]
"""

import argparse
import json
import statistics
import time
import urllib.request

PROMPTS = {
    "prose": ("Explain how a bicycle derailleur shifts gears, step by step, "
              "in clear prose for a curious teenager."),
    "code": ("Write a Python function that parses an ISO-8601 duration string "
             "into a timedelta. Include type hints and a docstring."),
}


def get(url):
    return urllib.request.urlopen(url, timeout=20).read().decode()


def spec_metrics(base):
    """Return vLLM speculative-decoding counters, if the profile exposes them."""
    out = {}
    try:
        for line in get(f"{base}/metrics").splitlines():
            if line.startswith("#") or "spec_decode" not in line:
                continue
            name, _, val = line.rpartition(" ")
            key = name.split("{")[0].replace("vllm:spec_decode_", "")
            out[key] = out.get(key, 0.0) + float(val)
    except Exception:
        pass
    return out


def run(base, model, max_tokens, prompt):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(f"{base}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    elapsed = time.monotonic() - t0
    out_tokens = d["usage"]["completion_tokens"]
    text = d["choices"][0]["message"]["content"]
    return out_tokens / elapsed, out_tokens, elapsed, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8003")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=300)
    ap.add_argument("--workload", choices=sorted(PROMPTS), default="prose")
    a = ap.parse_args()

    model = json.loads(get(f"{a.base}/v1/models"))["data"][0]["id"]
    print(f"model: {model}  workload: {a.workload}")
    before = spec_metrics(a.base)

    rates, sample = [], ""
    for i in range(a.trials):
        rate, toks, secs, text = run(a.base, model, a.max_tokens, PROMPTS[a.workload])
        rates.append(rate)
        sample = text
        print(f"  trial {i+1}: {rate:6.2f} tok/s  ({toks} tokens in {secs:.1f}s)")

    print(f"median: {statistics.median(rates):.2f} tok/s")

    after = spec_metrics(a.base)
    delta = {k: after[k] - before.get(k, 0.0) for k in after}
    if delta:
        print("speculative-decode counters (delta over this run):")
        for k, v in sorted(delta.items()):
            print(f"  {k}: {v:g}")
        drafted = delta.get("num_draft_tokens_total", 0)
        accepted = delta.get("num_accepted_tokens_total", 0)
        drafts = delta.get("num_drafts_total", 0)
        if drafted:
            print(f"  -> token acceptance: {accepted / drafted:.1%} "
                  f"({accepted:g}/{drafted:g})")
        if drafts:
            print(f"  -> accepted per draft step: {accepted / drafts:.2f} "
                  f"of {drafted / drafts:.0f} proposed")
    else:
        print("speculative-decode counters: none exposed (no drafter on this profile)")

    print(f"\noutput sanity (first 160 chars):\n  {sample[:160].strip()!r}")


if __name__ == "__main__":
    main()
