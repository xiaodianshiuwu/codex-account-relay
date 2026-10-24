from __future__ import annotations

import unittest

from codex_relay.threads import get_thread, handoff_metrics, list_threads, rollout_metrics
from codex_relay.util import RelayError

from tests.helpers import new_store, write_rollout, write_state_db


class ThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = new_store()
        self.rollout = self.store.paths.codex_home / "sessions" / "rollout-test.jsonl"
        write_rollout(self.rollout)
        write_state_db(
            self.store.paths,
            [
                {
                    "id": "thread-1",
                    "rollout_path": str(self.rollout),
                    "title": "Thread One",
                    "creator_account_id": "acct-a",
                    "updated_at": 1700000000,
                }
            ],
        )

    def test_list_and_get_thread(self) -> None:
        threads = list_threads(self.store.paths)
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0].id, "thread-1")
        self.assertEqual(get_thread(self.store.paths, "thread-1").title, "Thread One")

    def test_rollout_metrics(self) -> None:
        metrics = rollout_metrics(self.rollout)
        self.assertEqual(metrics.creator_account_id, "acct-a")
        self.assertEqual(metrics.reasoning_items, 1)
        self.assertEqual(metrics.reasoning_encrypted_items, 1)
        self.assertEqual(metrics.reasoning_encrypted_bytes, 1000)
        self.assertEqual(len(metrics.compactions), 1)
        self.assertEqual(len(metrics.turns), 2)
        first, second = metrics.turns
        self.assertEqual(first.first_cached, 9000)
        self.assertEqual(second.first_cached, 0)
        self.assertEqual(second.last_cached, 21000)
        self.assertAlmostEqual(second.first_cache_ratio, 0.0)
        self.assertGreater(second.last_cache_ratio, 95.0)
        totals = metrics.as_dict()["totals"]
        self.assertEqual(totals["requests"], 3)
        self.assertEqual(totals["input_tokens"], 52000)
        self.assertEqual(totals["cached_input_tokens"], 30000)
        self.assertEqual(totals["output_tokens"], 350)
        self.assertEqual(totals["reasoning_output_tokens"], 55)
        self.assertEqual(totals["total_tokens"], 52350)
        self.assertGreater(totals["cache_ratio"], 50)

    def test_handoff_metrics(self) -> None:
        metrics = rollout_metrics(self.rollout)
        handoff = handoff_metrics(metrics, "2026-01-01T00:30:00Z")
        self.assertIsNotNone(handoff)
        self.assertEqual(handoff["before"]["turn_id"], "turn-1")
        self.assertEqual(handoff["after"]["turn_id"], "turn-2")
        self.assertEqual(handoff["after"]["first_cached_tokens"], 0)
        self.assertGreater(handoff["after"]["last_cache_ratio"], 95)

    def test_missing_rollout_is_reported(self) -> None:
        with self.assertRaises(RelayError):
            rollout_metrics(self.store.paths.codex_home / "missing.jsonl")


if __name__ == "__main__":
    unittest.main()
