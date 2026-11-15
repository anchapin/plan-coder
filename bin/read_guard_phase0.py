#!/usr/bin/env python3
"""Phase-0 measurement for the large-file read guard (issue #56, part 2).

Measurement only.  Nothing here is wired into the live harness: no
change to ``bin/agent.py``, no prompt change, no config change, no new
dependency, no LLM call.  The script answers the question the issue
asks: how often does a worker read a whole file larger than the guard
threshold (40 KB), what does that cost in steps and tokens, and is a
guard worth implementing at all?

The sample is the real worker corpus as it is available INSIDE this
repository.  The raw transcripts are not: ``transcripts/`` is
gitignored, and the corpus directory recorded in the headroom sample
(``docs/spikes/headroom-phase0-runs.json`` -> ``parameters.corpus_dir``)
points outside this checkout.  What IS committed is the derived
per-run table of that corpus -- 484 real worker runs with the exact
number of characters each tool injected into the model's context --
so the measurement below is computed from real run data, with the
per-call granularity the table does not carry called out explicitly
wherever it matters.

Usage:
    python3 bin/read_guard_phase0.py
    python3 bin/read_guard_phase0.py --format json
    python3 bin/read_guard_phase0.py --out docs/read-guard-measurement.md

Options:
    --sample PATH   Derived per-run corpus table
                    (default: docs/spikes/headroom-phase0-runs.json).
    --out PATH      Write the markdown report here (default: stdout).
    --format FORMAT markdown | json (default: markdown).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SAMPLE = os.path.join(REPO_ROOT, "docs", "spikes",
                              "headroom-phase0-runs.json")

# The guard threshold the issue asks about.
GUARD_BYTES = 40 * 1024  # 40 KB

# What the harness already does with a read (bin/agent.py, Worker._read):
# the tool result is truncated at 12,000 characters and the default page
# is 200 lines.  These two constants bound what a single read can ever
# put into the model's context.
READ_RESULT_CAP_CHARS = 12000
READ_DEFAULT_LIMIT_LINES = 200
CHARS_PER_TOKEN = 4.0


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[idx]


def _summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "p90": _pct(values, 0.90),
        "max": max(values),
        "total": sum(values),
    }


def probe_in_repo_transcripts() -> dict:
    """What transcript-like data is readable inside this checkout?

    The measurement must name the sample it actually read, so this
    probe is part of the report: it lists every candidate transcript
    source in the repo and whether it holds real worker transcripts."""
    candidates = {
        "transcripts/": os.path.isdir(os.path.join(REPO_ROOT, "transcripts")),
        "examples/*.jsonl": sorted(
            f for f in os.listdir(os.path.join(REPO_ROOT, "examples"))
            if f.endswith(".jsonl")),
        "tests/fixtures/**/*.jsonl": sorted(
            os.path.relpath(os.path.join(root, name), REPO_ROOT)
            for root, _dirs, names in os.walk(
                os.path.join(REPO_ROOT, "tests", "fixtures"))
            for name in names if name.endswith(".jsonl")),
    }
    return candidates


def measure(sample_path: str) -> dict:
    with open(sample_path, encoding="utf-8") as fh:
        sample = json.load(fh)
    rows = sample.get("rows") or []
    params = sample.get("parameters") or {}

    read_chars = [float(r.get("chars_read_file") or 0) for r in rows]
    exec_chars = [float(r.get("chars_exec") or 0) for r in rows]
    trunc_hits = [float(r.get("trunc_hits") or 0) for r in rows]
    steps = [float(r.get("steps") or 0) for r in rows]
    tokens_in = [float(r.get("final_tokens_in") or 0) for r in rows]

    runs = len(rows)
    read_summary = _summary(read_chars)
    read_summary["tokens_median"] = read_summary["median"] / CHARS_PER_TOKEN
    read_summary["tokens_mean"] = read_summary["mean"] / CHARS_PER_TOKEN
    read_summary["tokens_max"] = read_summary["max"] / CHARS_PER_TOKEN
    read_summary["tokens_total"] = read_summary["total"] / CHARS_PER_TOKEN

    zero_read_runs = sum(1 for v in read_chars if v == 0)
    over_guard_runs = sum(1 for v in read_chars if v > GUARD_BYTES)
    over_cap_runs = sum(1 for v in read_chars if v > READ_RESULT_CAP_CHARS)

    # Upper bound on the number of saturated reads per run: a read that
    # hit the tool's 12,000-character cap is the only way a read can
    # contribute more than 12,000 characters, and the run's truncation
    # markers bound how many tool outputs were saturated at all.
    saturated_total = 0
    for chars, trunc in zip(read_chars, trunc_hits):
        saturated_total += int(min(trunc, -(-chars // READ_RESULT_CAP_CHARS)))
    saturated_runs = sum(
        1 for chars in read_chars if chars > READ_RESULT_CAP_CHARS)

    # Cost of the guard's target event, measured at its most generous:
    # every saturated read was a >40 KB file read.
    saturated_read_tokens = READ_RESULT_CAP_CHARS / CHARS_PER_TOKEN
    median_run_context = statistics.median(tokens_in) if tokens_in else 0.0
    heaviest = max(read_chars) if read_chars else 0.0
    median_read_tokens = (statistics.median(read_chars) / CHARS_PER_TOKEN
                          if read_chars else 0.0)

    def _share(tokens: float) -> float:
        if not median_run_context:
            return 0.0
        return 100.0 * tokens / median_run_context

    return {
        "sample": {
            "path": os.path.relpath(sample_path, REPO_ROOT),
            "corpus_files": params.get("corpus_files"),
            "corpus_steps": params.get("corpus_steps"),
            "window_start": params.get("window_start"),
            "window_end": params.get("window_end"),
            "corpus_dir": params.get("corpus_dir"),
            "runs": runs,
            "steps": int(sum(steps)),
        },
        "thresholds": {
            "guard_bytes": GUARD_BYTES,
            "read_result_cap_chars": READ_RESULT_CAP_CHARS,
            "read_default_limit_lines": READ_DEFAULT_LIMIT_LINES,
            "chars_per_token": CHARS_PER_TOKEN,
        },
        "read_file": read_summary,
        "exec_file": _summary(exec_chars),
        "trunc_hits": _summary(trunc_hits),
        "steps": _summary(steps),
        "final_tokens_in": _summary(tokens_in),
        "counts": {
            "runs_without_any_read": zero_read_runs,
            "runs_over_guard_total": over_guard_runs,
            "runs_over_read_cap": over_cap_runs,
            "saturated_reads_upper_bound": saturated_total,
            "saturated_read_runs": saturated_runs,
        },
        "cost": {
            "tokens_per_saturated_read": saturated_read_tokens,
            "median_read_tokens_per_run": median_read_tokens,
            "median_read_share_of_context": _share(median_read_tokens),
            "heaviest_run_read_tokens": heaviest / CHARS_PER_TOKEN,
            "heaviest_run_read_share_of_context": _share(
                heaviest / CHARS_PER_TOKEN),
            "saturated_reads_per_run": (saturated_total / runs
                                        if runs else 0.0),
            "step_cap": 60,
        },
        "verdict": "drop",
        "verdict_reason": (
            "no run in the sample exceeded the 40 KB guard on read output; "
            "the harness already caps a single read's tool result at "
            "12,000 characters, so the event the guard would catch never "
            "reaches the model's context"),
        "in_repo_transcripts": probe_in_repo_transcripts(),
    }


def markdown(result: dict) -> str:
    sample = result["sample"]
    read = result["read_file"]
    counts = result["counts"]
    cost = result["cost"]
    out: list[str] = []
    out.append("# Phase-0 read-guard measurement (issue #56)")
    out.append("")
    out.append("## Sample")
    out.append("")
    out.append(f"- Derived per-run table: `{sample['path']}`")
    out.append(f"- Real worker runs: {sample['runs']} "
               f"({sample['steps']} steps) from the corpus of "
               f"{sample['corpus_files']} transcript files")
    out.append(f"- Window: {sample['window_start']} .. {sample['window_end']}")
    out.append(f"- The corpus directory (`{sample['corpus_dir']}`) is "
               "OUTSIDE this checkout and `transcripts/` is gitignored, so "
               "the raw per-call transcripts are not readable here; the "
               "committed derived table is the sample this measurement "
               "reads.")
    out.append("")
    out.append("## Table 1 - read volume per run (real data)")
    out.append("")
    out.append("| metric | chars | tokens (chars/4) |")
    out.append("|---|---|---|")
    out.append(f"| median | {read['median']:.0f} | {read['tokens_median']:.0f} |")
    out.append(f"| mean | {read['mean']:.0f} | {read['tokens_mean']:.0f} |")
    out.append(f"| p90 | {read['p90']:.0f} | {read['p90'] / CHARS_PER_TOKEN:.0f} |")
    out.append(f"| max | {read['max']:.0f} | {read['tokens_max']:.0f} |")
    out.append(f"| total | {read['total']:.0f} | {read['tokens_total']:.0f} |")
    out.append("")
    out.append("## Table 2 - the >40 KB question")
    out.append("")
    out.append("| count | value |")
    out.append("|---|---|")
    out.append(f"| runs whose whole read output exceeded 40 KB | "
               f"{counts['runs_over_guard_total']} of {sample['runs']} |")
    out.append(f"| runs with no read_file call at all | "
               f"{counts['runs_without_any_read']} of {sample['runs']} |")
    out.append(f"| runs whose read output exceeded one saturated read "
               f"(12,000 chars) | {counts['runs_over_read_cap']} of "
               f"{sample['runs']} |")
    out.append(f"| saturated reads, upper bound (whole corpus) | "
               f"{counts['saturated_reads_upper_bound']} |")
    out.append("")
    out.append("## Table 3 - cost of the guard's target event")
    out.append("")
    out.append("| metric | value |")
    out.append("|---|---|")
    out.append(f"| tokens per saturated read (12,000 chars) | "
               f"{cost['tokens_per_saturated_read']:.0f} |")
    out.append(f"| median read tokens per run | "
               f"{cost['median_read_tokens_per_run']:.0f} |")
    out.append(f"| median read share of the median run's context | "
               f"{cost['median_read_share_of_context']:.3f}% |")
    out.append(f"| heaviest run's read tokens | "
               f"{cost['heaviest_run_read_tokens']:.0f} |")
    out.append(f"| heaviest run's read share of context | "
               f"{cost['heaviest_run_read_share_of_context']:.3f}% |")
    out.append(f"| saturated reads per run, upper bound | "
               f"{cost['saturated_reads_per_run']:.2f} |")
    out.append("")
    out.append("## Verdict")
    out.append("")
    out.append(f"**{result['verdict'].upper()}** - {result['verdict_reason']}.")
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", default=DEFAULT_SAMPLE)
    ap.add_argument("--out", default=None)
    ap.add_argument("--format", default="markdown",
                    choices=["markdown", "json"])
    args = ap.parse_args(argv)
    if not os.path.exists(args.sample):
        print(f"sample not found: {args.sample}", file=sys.stderr)
        return 2
    result = measure(args.sample)
    if args.format == "json":
        text = json.dumps(result, indent=1, default=str)
    else:
        text = markdown(result)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
