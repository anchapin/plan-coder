#!/usr/bin/env python3
"""Re-runnable census + detector replay for issue #34 (OS3 publication signals).

Read-only. Queries GitHub with `gh` against ``anchapin/fluxion`` and prints:

  1. the raw round census table (one row per OS3 round PR in the window), and
  2. the detector-replay table (TP/FP/FN for the body-search detector and for
     the narrow branch+commit-trailer detector).

No writes to GitHub, no writes to disk. Requires `gh` on PATH with a valid
token. In sandboxed environments the egress proxy must be exported first::

    export HTTP_PROXY=http://hatch-egress-proxy:3128
    export HTTPS_PROXY=http://hatch-egress-proxy:3128
    export ALL_PROXY=http://hatch-egress-proxy:3128
    export SSL_CERT_FILE=/run/hatch/egress-tls/ca-bundle.pem

Usage::

    python3 docs/spikes/os3_publication_census.py [--repo anchapin/fluxion]
                                                  [--since 2026-11-01]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import subprocess
import sys

# An OS3 attribution line: an AI-assist / attribution / co-author marker that
# also names OS3 (the fleet's wording varies ~40 ways, see the report).
OS3_LINE = re.compile(
    r"(?i)(\[\s*AI[- ]assisted\s*\]|tool attribution|tools:|attribution:"
    r"|co-authored-by|assisted-by).*\bOS3\b|\bOS3\b.*(agent|worker|loop round|physics loop)"
)
# A round number in a branch name, PR title or PR body ("loop r122", "round 141").
ROUND_NO = re.compile(r"(?i)\b(?:loop|round|r)\s*r?(\d{2,3})\b")

PR_FIELDS = (
    "number,title,headRefName,baseRefName,body,createdAt,mergedAt,closedAt,"
    "state,author,url,commits"
)


def gh(*args: str) -> str:
    out = subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    )
    return out.stdout


def gh_json(*args: str):
    return json.loads(gh(*args))


def window_prs(repo: str, since: str) -> list[dict]:
    """Every PR in the repo (all states) whose created_at >= since."""
    raw = gh(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repo}/pulls?state=all&per_page=100",
    )
    # `--slurp` wraps each page in an array: [[page1], [page2], ...].
    prs = []
    for page in json.loads(raw):
        prs.extend(page)
    keep = [p for p in prs if p["created_at"][:10] >= since]
    keep.sort(key=lambda p: p["created_at"])
    return keep


def detail(repo: str, number: int) -> dict:
    return gh_json("pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS)


def commit_text(pr: dict) -> str:
    return "\n".join(
        c["messageHeadline"] + "\n" + (c.get("messageBody") or "")
        for c in pr["commits"]
    )


def attribution_lines(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = line.strip().lstrip("*").strip()
        if OS3_LINE.search(line):
            out.append(line)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="anchapin/fluxion")
    ap.add_argument("--since", default="2026-11-01")
    args = ap.parse_args()

    pulls = window_prs(args.repo, args.since)
    print(f"# window PRs created since {args.since}: {len(pulls)}")

    rows = []
    for p in pulls:
        d = detail(args.repo, p["number"])
        body = d["body"] or ""
        commits = commit_text(d)
        dates = [c["committedDate"] for c in d["commits"] if c.get("committedDate")]
        rows.append(
            {
                "n": d["number"],
                "title": d["title"],
                "head": d["headRefName"],
                "base": d["baseRefName"],
                "state": d["state"],
                "created": d["createdAt"],
                "nc": len(d["commits"]),
                "body": attribution_lines(body),
                "commit": attribution_lines(commits),
                "first": min(dates) if dates else None,
                "last": max(dates) if dates else None,
                "rounds": sorted(
                    {int(x) for x in ROUND_NO.findall(d["title"] + "\n" + body)}
                ),
            }
        )

    print()
    print("| PR | head branch | base | trailer location | commits | first commit | last commit | span | state | evidence |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        loc = (
            "both"
            if r["body"] and r["commit"]
            else "commit only"
            if r["commit"]
            else "body only"
            if r["body"]
            else "none"
        )
        span = ""
        if r["first"] and r["last"]:
            a = dt.datetime.fromisoformat(r["first"].replace("Z", "+00:00"))
            b = dt.datetime.fromisoformat(r["last"].replace("Z", "+00:00"))
            d = b - a
            span = f"{d.days}d {d.seconds // 3600}h" if d.days else f"{d.seconds // 60}m {d.seconds % 60}s"
        ev = (r["commit"] or r["body"] or [""])[0][:70].replace("|", "/")
        print(
            f"| #{r['n']} | `{r['head']}` | {r['base']} | {loc} | {r['nc']} | "
            f"{r['first']} | {r['last']} | {span} | {r['state']} | {ev} |"
        )

    rounds = [r for r in rows if r["rounds"]]
    trailers = [r for r in rows if r["body"] or r["commit"]]
    print()
    print(f"round-numbered PRs: {len(rounds)}; with OS3 trailer: {len(trailers)}")
    print("trailer location:", collections.Counter(
        ("both" if r["body"] and r["commit"] else
         "commit only" if r["commit"] else
         "body only" if r["body"] else "none")
        for r in rows
    ))
    print("head families (rounds):", collections.Counter(r["head"].split("/")[0] for r in trailers))

    def replay(name, pred):
        tp = [r for r in rounds if pred(r)]
        fp = [r for r in rows if not r["rounds"] and pred(r)]
        fn = [r for r in rounds if not pred(r)]
        print(f"\n{name}: TP={len(tp)} FP={len(fp)} FN={len(fn)}")
        if fp:
            print("  FP:", [(r["n"], r["head"]) for r in fp])
        print("  FN:", [(r["n"], r["head"]) for r in fn])

    replay(
        "Detector A1 (repo-wide body/title search for OS3)",
        lambda r: bool(r["body"]),
    )
    replay(
        "Detector B1 (head matches physics/r<N>-* AND commit-message trailer)",
        lambda r: r["head"].startswith("physics/") and bool(r["commit"]),
    )
    replay(
        "Detector B2 (head matches physics/r<N>-* only)",
        lambda r: r["head"].startswith("physics/"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
