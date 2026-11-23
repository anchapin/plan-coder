"""Tests for the agent.py provider failover on read timeouts.

Regression coverage for the home-energy-twin #12/#28 abort mode
(2026-11-22): a worker run died when one provider's model call kept
hitting the socket read timeout, because chat() only failed over on
quota exhaustion (_ProviderExhausted) while exhausted transient
retries raised a plain RuntimeError that aborted the whole run.

Hermetic: urlopen is mocked, auth is stubbed, sleep is a no-op.
Run: python3 -m unittest tests.test_agent_timeout_failover
"""
from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
import agent  # noqa: E402

PROVIDER_A = {"chat_url": "http://a.invalid/chat", "env_key": "A_KEY",
              "credential": "a", "hosts": ["a.invalid"]}
PROVIDER_B = {"chat_url": "http://b.invalid/chat", "env_key": "B_KEY",
              "credential": "b", "hosts": ["b.invalid"]}
CHAIN = [("a", PROVIDER_A, "model-a"), ("b", PROVIDER_B, "model-b")]


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()


class TimeoutFailoverTest(unittest.TestCase):
    def setUp(self):
        self.attempts = {"a": 0, "b": 0}
        patchers = [
            mock.patch.object(agent, "_apply_auth", return_value="env"),
            mock.patch.object(agent.time, "sleep", lambda _s: None),
            mock.patch("urllib.request.urlopen", side_effect=self._open),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.behavior = {"a": "timeout", "b": "ok"}

    def _open(self, req, timeout=None):
        which = "a" if req.full_url.startswith("http://a.") else "b"
        self.attempts[which] += 1
        mode = self.behavior[which]
        if mode == "ok":
            return _Resp()
        if mode == "timeout":
            raise TimeoutError("The read operation timed out")
        if mode == "reset":
            raise ConnectionResetError("connection reset by peer")
        if mode == "401":
            raise urllib.error.HTTPError(
                req.full_url, 401, "unauthorized", {}, None)
        if mode == "429":
            raise urllib.error.HTTPError(
                req.full_url, 429, "rate limited", {}, None)
        raise AssertionError(f"unknown behavior {mode}")

    def test_read_timeout_fails_over_after_two_attempts(self):
        data, name, _model = agent.chat(CHAIN, [], [])
        self.assertEqual(name, "b")
        self.assertEqual(data["choices"][0]["message"]["content"], "ok")
        # Two timeouts on a, not six: the failover is fast.
        self.assertEqual(self.attempts, {"a": 2, "b": 1})

    def test_chat_once_timeout_raises_unavailable(self):
        with self.assertRaises(agent._ProviderUnavailable):
            agent.chat_once(PROVIDER_A, "model-a", [], [])
        self.assertEqual(self.attempts["a"], 2)

    def test_transient_error_retries_six_then_unavailable(self):
        self.behavior["a"] = "reset"
        with self.assertRaises(agent._ProviderUnavailable):
            agent.chat_once(PROVIDER_A, "model-a", [], [])
        self.assertEqual(self.attempts["a"], 6)

    def test_all_providers_unavailable_raises(self):
        self.behavior["b"] = "timeout"
        with self.assertRaises(RuntimeError) as ctx:
            agent.chat(CHAIN, [], [])
        self.assertIn("all providers exhausted", str(ctx.exception))
        self.assertEqual(self.attempts, {"a": 2, "b": 2})

    def test_http_401_fails_fast_without_failover(self):
        self.behavior["a"] = "401"
        with self.assertRaises(RuntimeError) as ctx:
            agent.chat(CHAIN, [], [])
        self.assertIn("HTTP 401", str(ctx.exception))
        # One attempt on a, none on b: a broken setup must not burn the
        # fallback provider's quota.
        self.assertEqual(self.attempts, {"a": 1, "b": 0})

    def test_429_still_fails_over(self):
        self.behavior["a"] = "429"
        data, name, _model = agent.chat(CHAIN, [], [])
        self.assertEqual(name, "b")
        self.assertEqual(self.attempts, {"a": 6, "b": 1})


if __name__ == "__main__":
    unittest.main()
