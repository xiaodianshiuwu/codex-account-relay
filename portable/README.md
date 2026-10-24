# Portable desktop / Windows / Linux

The native macOS client remains `desktop/` (SwiftUI). This portable Tkinter
client uses the same Python core and the same `~/.codex-relay` state as the CLI.

## Run from source

```sh
python3 -m codex_relay.portable_gui --self-test
python3 -m codex_relay.portable_gui
```

Linux needs Tk:

```sh
sudo apt install python3-tk   # Debian/Ubuntu
sudo dnf install python3-tkinter  # Fedora
```

Windows and standard macOS Python builds include Tk. On a headless server use
the CLI instead; the GUI exits with a clear message if no display is available.

## Build a desktop artifact

```sh
python3 -m pip install pyinstaller
./portable/build.sh          # Linux/macOS
# Windows PowerShell:
# .\portable\build.ps1
```

PyInstaller must run on the target operating system; this repository's CI
workflow builds on each OS. The produced executable is platform-specific.
