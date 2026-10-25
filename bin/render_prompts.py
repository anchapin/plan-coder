#!/usr/bin/env python3
"""Render the prompt templates in prompts/ into ready-to-install prompts.

Templates carry {{dotted.key}} tokens. Values come from the deployment
config (default search: $PLAN_CODER_CONFIG, config/deployment.local.json,
~/.plan-coder/deployment.json — see config/deployment.example.json).
Rendered output goes to local/ (gitignored): the actual deployment's
prompts are an instance artifact, never committed, while the templates
stay generic.

Each template file is an intro header (for human readers of the repo),
a line containing only `---`, then the prompt body. Only the body is
rendered. Two tokens are derived from the config rather than stored:
  {{repos.table}}           markdown table of the configured repos
  {{repos.split_sentence}}  "2 from repo-a, 1 from repo-b" from each
                            repo's issues_per_cycle
{{workers.provider_order}} joins the configured provider list with
commas, matching agent.py's --provider flag.

Usage: render_prompts.py [--config PATH] [--out DIR]
Exit code 2 with the unresolved token list if any token has no value —
a partially rendered prompt is never written.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

TOKEN = re.compile(r"\{\{([a-zA-Z0-9_.]+)\}\}")


def load_config(explicit: str | None) -> dict:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get("PLAN_CODER_CONFIG")
    if env_path:
        candidates.append(env_path)
    repo_root = os.path.dirname(os.path.dirname(
        os.path.realpath(os.path.abspath(__file__))))
    candidates.append(os.path.join(
        repo_root, "config", "deployment.local.json"))
    candidates.append(os.path.expanduser("~/.plan-coder/deployment.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("no deployment config found; copy "
                     "config/deployment.example.json to "
                     "config/deployment.local.json and fill it in")


def derived(cfg: dict) -> dict:
    repos = cfg.get("repos") or []
    table_lines = ["| Repo | Clone | Slug | Base |", "|---|---|---|---|"]
    for r in repos:
        table_lines.append(
            f"| {r.get('name','')} | `{r.get('clone','')}` | "
            f"`{r.get('slug','')}` | `{r.get('base','')}` |")
    split = ", ".join(
        f"{r.get('issues_per_cycle', 1)} from {r.get('name','')}"
        for r in repos)
    bullets = "\n".join(
        f"- {r.get('name','')} — {r.get('clone','')}, {r.get('slug','')}, "
        f"base {r.get('base','')}" for r in repos)
    skip_notes = " ".join(
        f"{r.get('name','').capitalize()}: skip {r['skip_note']}."
        for r in repos if r.get("skip_note"))
    brief_extras = " ".join(
        r["brief_extra"] for r in repos if r.get("brief_extra"))
    return {
        "repos.table": "\n".join(table_lines),
        "repos.bullets": bullets,
        "repos.skip_notes": skip_notes,
        "repos.brief_extras": brief_extras,
        "repos.split_sentence": split,
        "workers.provider_order": ",".join(
            (cfg.get("workers") or {}).get("provider_order") or []),
    }


def lookup(cfg: dict, derived_map: dict, token: str):
    if token in derived_map:
        return derived_map[token]
    node = cfg
    for key in token.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if isinstance(node, (dict, list)):
        return None
    return str(node)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    repo_root = os.path.dirname(os.path.dirname(
        os.path.realpath(os.path.abspath(__file__))))
    prompts_dir = os.path.join(repo_root, "prompts")
    out_dir = args.out or os.path.join(repo_root, "local")
    cfg = load_config(args.config)
    derived_map = derived(cfg)

    rendered = {}
    missing = []
    for fn in sorted(os.listdir(prompts_dir)):
        if not fn.endswith(".md"):
            continue
        text = open(os.path.join(prompts_dir, fn), encoding="utf-8").read()
        lines = text.splitlines()
        try:
            cut = lines.index("---")
        except ValueError:
            print(f"skip {fn}: no --- separator between intro and body")
            continue
        body = "\n".join(lines[cut + 1:]).strip()

        def sub(match: re.Match) -> str:
            token = match.group(1)
            value = lookup(cfg, derived_map, token)
            if value is None:
                missing.append(f"{fn}: {{{{{token}}}}}")
                return match.group(0)
            return value

        rendered[fn] = TOKEN.sub(sub, body)

    if missing:
        print("UNRESOLVED TOKENS (nothing written):")
        print("\n".join(sorted(set(missing))))
        return 2

    os.makedirs(out_dir, exist_ok=True)
    for fn, body in rendered.items():
        dest = os.path.join(out_dir, fn.replace(".md", ".rendered.md"))
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"rendered {fn} -> {dest} ({len(body)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
