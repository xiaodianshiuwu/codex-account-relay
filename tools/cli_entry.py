"""PyInstaller entry point for the bundled Codex Relay backend."""

from codex_relay.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
