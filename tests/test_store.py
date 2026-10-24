from __future__ import annotations

import os
import stat
import sys
import unittest

from codex_relay.authfile import read_auth, write_auth
from codex_relay.store import Store
from codex_relay.util import RelayError

from tests.helpers import make_auth, new_store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: Store = new_store()

    def test_add_and_resolve_profile(self) -> None:
        profile = self.store.add_profile("one", make_auth("acct-1", "one@example.com"))
        self.assertEqual(profile["alias"], "one")
        self.assertEqual(self.store.resolve("one")["id"], profile["id"])
        self.assertEqual(self.store.resolve("one@example.com")["id"], profile["id"])
        self.assertEqual(self.store.resolve("acct-1")["id"], profile["id"])

    def test_duplicate_account_updates_instead_of_duplicating(self) -> None:
        self.store.add_profile("one", make_auth("acct-1", "one@example.com"))
        updated = make_auth("acct-1", "one@example.com", refresh_token="new-refresh")
        profile = self.store.add_profile("two", updated)
        self.assertEqual(profile["alias"], "one")
        self.assertEqual(len(self.store.profiles()), 1)
        self.assertEqual(read_auth(self.store.secret_path(profile["id"]))["tokens"]["refresh_token"], "new-refresh")

    def test_alias_conflict_is_rejected(self) -> None:
        self.store.add_profile("one", make_auth("acct-1"))
        with self.assertRaises(RelayError):
            self.store.add_profile("one", make_auth("acct-2", "two@example.com"))

    def test_snapshot_current_adopts_unmanaged_auth(self) -> None:
        auth = make_auth("acct-9", "nine@example.com")
        write_auth(self.store.paths.auth_path, auth)
        profile = self.store.snapshot_current(adopt=True)
        self.assertIsNotNone(profile)
        self.assertTrue(profile["auto_adopted"])
        self.assertEqual(len(self.store.profiles()), 1)

    def test_persist_profile_auth_updates_active_auth_file(self) -> None:
        auth = make_auth("acct-1")
        profile = self.store.add_profile("one", auth)
        write_auth(self.store.paths.auth_path, auth)
        updated = make_auth("acct-1", refresh_token="rotated")
        self.store.persist_profile_auth(profile, updated)
        self.assertEqual(read_auth(self.store.paths.auth_path)["tokens"]["refresh_token"], "rotated")
        self.assertEqual(
            read_auth(self.store.secret_path(profile["id"]))["tokens"]["refresh_token"],
            "rotated",
        )

    @unittest.skipIf(sys.platform == "win32", "Windows uses ACLs instead of POSIX mode bits")
    def test_secret_files_are_private(self) -> None:
        profile = self.store.add_profile("one", make_auth("acct-1"))
        secret = self.store.secret_path(profile["id"])
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode) & 0o077, 0)
        self.assertEqual(stat.S_IMODE(self.store.paths.state_path.stat().st_mode) & 0o077, 0)

    def test_backup_and_restore_metadata(self) -> None:
        auth = make_auth("acct-1")
        write_auth(self.store.paths.auth_path, auth)
        backup = self.store.backup("unit")
        self.assertTrue((backup / "auth.json").exists())
        self.assertTrue((backup / "meta.json").exists())
        self.assertTrue((backup / "state.json").exists() or not self.store.paths.state_path.exists())

    def test_remove_active_profile_is_refused(self) -> None:
        profile = self.store.add_profile("one", make_auth("acct-1"))
        self.store.state["active_profile_id"] = profile["id"]
        self.store.save_state()
        with self.assertRaises(RelayError):
            self.store.remove_profile("one")

    def test_journal_has_no_tokens(self) -> None:
        self.store.journal({"event": "test", "ok": True})
        content = self.store.paths.journal_path.read_text(encoding="utf-8")
        self.assertIn('"event": "test"', content)
        self.assertNotIn("refresh", content)


if __name__ == "__main__":
    unittest.main()
