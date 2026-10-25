#!/usr/bin/env python3
"""Render the cron prompt templates and prepare them for installation.

The three scheduled prompts (backlog supervisor, PR shepherd, harness
tune) are generated artifacts: their source is prompts/*.md plus
config/deployment.local.json, and the scheduler's saved bodies must be
byte-identical to the rendered output. This script renders them into
local/ (gitignored) and writes local/cron-manifest.json with each
body's sha256, cron id, and source template.

It deliberately does NOT talk to any scheduler API: installing a body
is an agent action (cron.update with the rendered file's exact
content), and the manifest hash is how the installer verifies the
saved body matches what was rendered. Workflow:

  1. python3 bin/install_crons.py        # render + manifest
  2. install each body whose hash differs from the saved one
  3. re-read each saved body and compare its sha256 to the manifest

Exit code 2 (nothing written) if any template token is unresolved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.realpath(os.path.abspath(__file__))))
from render_prompts import TOKEN, derived, load_config, lookup  # noqa: E402

CRON_IDS = {
    "backlog-supervisor.md": "backlog-supervisor",
    "pr-shepherd.md": "pr-shepherd",
    "harness-tune.md": "harness-tune",
}


def render_all(cfg: dict) -> tuple[dict[str, str], list[str]]:
    repo_root = os.path.dirname(os.path.dirname(
        os.path.realpath(os.path.abspath(__file__))))
    prompts_dir = os.path.join(repo_root, "prompts")
    derived_map = derived(cfg)
    rendered: dict[str, str] = {}
    missing: list[str] = []
    for fn in sorted(CRON_IDS):
        text = open(os.path.join(prompts_dir, fn), encoding="utf-8").read()
        lines = text.splitlines()
        try:
            cut = lines.index("---")
        except ValueError:
            missing.append(f"{fn}: no --- separator")
            continue
        body = "\n".join(lines[cut + 1:]).strip()

        def sub(match):
            token = match.group(1)
            value = lookup(cfg, derived_map, token)
            if value is None:
                missing.append(f"{fn}: {{{{{token}}}}}")
                return match.group(0)
            return value

        rendered[fn] = TOKEN.sub(sub, body)
    return rendered, missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    repo_root = os.path.dirname(os.path.dirname(
        os.path.realpath(os.path.abspath(__file__))))
    out_dir = args.out or os.path.join(repo_root, "local")
    cfg = load_config(args.config)
    rendered, missing = render_all(cfg)
    if missing:
        print("UNRESOLVED (nothing written):")
        print("\n".join(sorted(set(missing))))
        return 2

    os.makedirs(out_dir, exist_ok=True)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": "config/deployment.local.json",
        "crons": {},
    }
    crons_cfg = cfg.get("crons") or {}
    chat_id = (cfg.get("delivery") or {}).get("chat_id")
    for fn, body in rendered.items():
        dest = os.path.join(out_dir, fn.replace(".md", ".rendered.md"))
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(body)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        cron_id = CRON_IDS[fn]
        meta = crons_cfg.get(cron_id) or {}
        manifest["crons"][cron_id] = {
            "template": f"prompts/{fn}",
            "rendered": dest,
            "sha256": digest,
            "chars": len(body),
            "title": meta.get("title"),
            "schedule": meta.get("schedule"),
            "owner": meta.get("owner"),
            "timeout_secs": meta.get("timeout_secs"),
            "delivery": [{"chat_id": chat_id}] if chat_id else [],
        }
        print(f"{CRON_IDS[fn]}: {dest} sha256={digest[:16]}… ({len(body)} chars)")
    with open(os.path.join(out_dir, "cron-manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print("manifest ->", os.path.join(out_dir, "cron-manifest.json"))
    print("Install each body with your scheduler's update tool using the "
          "rendered file's exact content, then verify the saved body's "
          "sha256 against the manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
