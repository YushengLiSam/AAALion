# tools/

Helper scripts for the team.

## `screenshot_watcher.py`

Polls the macOS pasteboard and saves screenshots from `shift+ctrl+cmd+4` into `../screenshots/`.

```bash
python tools/screenshot_watcher.py
```

Stop with `Ctrl+C`. Requires `pyobjc-framework-Cocoa` (install once: `pip install pyobjc-framework-Cocoa`).

Files are named `screenshot_YYYYMMDD_HHMMSS_<changeCount>.png` for sortability and uniqueness.

The first time you run it, macOS may show a one-time "Python wants to paste from other apps" prompt. Allow it.

## `ssh_a100.sh`

```bash
./tools/ssh_a100.sh
```

Drops you into `~/shufeng/AAALion-/` on the A100. Assumes you have a `Host uc` entry in `~/.ssh/config`.

**Reminder**: never `cd` out of `~/shufeng/AAALion-/`. `~/shufeng/gpu-fuzz/` is a different ongoing task.
