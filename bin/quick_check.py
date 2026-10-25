#!/usr/bin/env python3
"""Minimal quota check: sends a tiny completion on a provider and prints
the reply plus token usage. Used to verify a plan's quota is back."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent import PROVIDERS, chat

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="zai", choices=list(PROVIDERS))
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    provider = PROVIDERS[args.provider]
    model = args.model or provider["default_model"]
    messages = [
        {"role": "user", "content": "Reply with exactly: ok"}
    ]
    data, _, _ = chat([(args.provider, provider, model)], messages, [])
    msg = data["choices"][0]["message"]
    usage = data.get("usage", {})
    print("reply:", (msg.get("content") or "").strip())
    print("usage:", json.dumps(usage))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
