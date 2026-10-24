## Summary

## Verification

- [ ] `python3 tests/run.py`
- [ ] `python3 tools/build_strings.py --check`
- [ ] `python3 tools/i18n_audit.py --json`
- [ ] `swift test --package-path desktop --scratch-path /tmp/codex-relay-swift` (if macOS code changed)
- [ ] `git diff --check`

## Security checklist

- [ ] No tokens, auth files, cookies, account data or personal paths
- [ ] No screenshots/logs with personal data
- [ ] Platform limitations are stated honestly
