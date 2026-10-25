from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codex_relay.portable_gui import PortableApp


class FakeList:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.selection = 0

    def delete(self, *args) -> None:
        self.items = []

    def insert(self, index, value) -> None:
        self.items.append(value)

    def selection_clear(self, *args) -> None:
        pass

    def selection_set(self, index) -> None:
        self.selection = index

    def curselection(self):
        return (self.selection,)


class RefreshArgsTests(unittest.TestCase):
    def test_set_accounts_preserves_selected_account(self) -> None:
        app = object.__new__(PortableApp)
        app.account_list = FakeList()
        app.selected_account = {"id": "b", "alias": "work"}
        app.accounts = []
        app.select_account = lambda: None  # type: ignore[method-assign]
        app._set_accounts([
            {"id": "a", "alias": "personal", "email": "a@example.com"},
            {"id": "b", "alias": "work", "email": "b@example.com"},
        ])
        self.assertEqual(app.account_list.selection, 1)
        self.assertEqual(app.selected_account, {"id": "b", "alias": "work"})

    def test_refresh_accounts_requests_all_profiles(self) -> None:
        app = object.__new__(PortableApp)
        app.async_call = lambda label, work, done: done(work())  # type: ignore[method-assign]
        app.t = lambda key: key  # type: ignore[method-assign]
        app._set_accounts = lambda accounts: None  # type: ignore[method-assign]
        seen = {}

        def fake_cli_json(args, **kwargs):
            seen["args"] = args
            return {"profiles": []}

        with patch("codex_relay.portable_gui._cli_json", side_effect=fake_cli_json):
            PortableApp.refresh_accounts(app)
        self.assertEqual(seen["args"], ["list", "--refresh", "--json"])

    def test_refresh_usage_requests_all_profiles(self) -> None:
        app = object.__new__(PortableApp)
        app.async_call = lambda label, work, done: done(work())  # type: ignore[method-assign]
        app.t = lambda key: key  # type: ignore[method-assign]
        app._set_accounts = lambda accounts: None  # type: ignore[method-assign]
        app.refresh_overview = lambda: None  # type: ignore[method-assign]
        seen = {}

        def fake_cli_json(args, **kwargs):
            seen["args"] = args
            return {"profiles": []}

        with patch("codex_relay.portable_gui._cli_json", side_effect=fake_cli_json):
            PortableApp.refresh_usage(app)
        self.assertEqual(seen["args"], ["usage", "--all", "--json"])


if __name__ == "__main__":
    unittest.main()
