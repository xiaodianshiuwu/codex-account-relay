from __future__ import annotations

import unittest

from codex_relay.authfile import auth_summary, validate_auth
from codex_relay.jwt import decode_jwt_payload, extract_identity
from codex_relay.util import RelayError

from tests.helpers import make_auth, make_jwt


class JwtTests(unittest.TestCase):
    def test_extracts_identity_from_access_and_id_tokens(self) -> None:
        auth = make_auth("acct-1", "one@example.com", plan="pro", user_id="user-1")
        identity = extract_identity(auth)
        self.assertEqual(identity["account_id"], "acct-1")
        self.assertEqual(identity["user_id"], "user-1")
        self.assertEqual(identity["email"], "one@example.com")
        self.assertEqual(identity["plan"], "pro")
        self.assertFalse(identity["access_expired"])

    def test_expired_access_token_is_detected(self) -> None:
        auth = make_auth(exp_offset=-60)
        self.assertTrue(extract_identity(auth)["access_expired"])

    def test_api_key_auth_is_rejected_for_chatgpt_handoff(self) -> None:
        with self.assertRaises(RelayError):
            validate_auth({"auth_mode": "apiKey", "OPENAI_API_KEY": "sk-test", "tokens": None})

    def test_missing_refresh_token_is_rejected(self) -> None:
        auth = make_auth()
        auth["tokens"]["refresh_token"] = ""
        with self.assertRaises(RelayError):
            validate_auth(auth)

    def test_summary_never_contains_tokens(self) -> None:
        auth = make_auth()
        summary = auth_summary(auth)
        serialized = repr(summary)
        for secret in (
            auth["tokens"]["access_token"],
            auth["tokens"]["id_token"],
            auth["tokens"]["refresh_token"],
        ):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("signature", serialized)

    def test_malformed_jwt_returns_empty_payload(self) -> None:
        self.assertEqual(decode_jwt_payload("not-a-jwt"), {})


if __name__ == "__main__":
    unittest.main()
