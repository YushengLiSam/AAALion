# Hardware

What devices we have, what each one is for, and the hard rules around the shared A100 server.

## Devices in active use

| Device | Owner | OS | Role |
|---|---|---|---|
| MacBook (Shufeng's) | Shufeng | macOS 15+ | Primary dev machine for iOS (Xcode + Simulator) and local backend during dev. |
| iPhone 13 | Shufeng | iOS 17+ | Real-device testing. Camera used for the 拍照找货 demo. |
| Mac mini M4 (planned) | Team | macOS 15+ | Planned shared dev/demo machine. M4 unified memory makes it a viable CI runner for the iOS build. Not blocking. |
| A100 server (SSH UC) | Team | Linux | Heavy work: CLIP image embedding (one-shot index build), batch RAG eval. **Strict scope rules below.** |

## A100 server — hard rules

Layout on the A100:

```
~/shufeng/
├── gpu-fuzz/     ← existing, ongoing fuzzing work — DO NOT TOUCH
└── AAALion-/     ← new, this project, sibling of gpu-fuzz/
```

**Rules** (violate any of these and you risk corrupting Shufeng's other work):

1. **All AAALion- work lives under `~/shufeng/AAALion-/`**. Never `cd` out of this subtree during an automated step.
2. **Never modify, list, or run anything under `~/shufeng/gpu-fuzz/`**. It is a different task; treat it as read-only and out of scope.
3. **Never touch paths outside `~/shufeng/`** (no `/opt`, no `~/.bashrc` edits, no `apt install`, no `pip install --user`).
4. Use a project-local Python venv: `~/shufeng/AAALion-/.venv/`. All `pip install` lands there.
5. GPU-heavy steps (CLIP index, batch eval) — that's it. Web servers and request-path code stay on laptops; the A100 is not a service host.

## SSH helper

`tools/ssh_a100.sh` (after creation):

```bash
#!/usr/bin/env bash
ssh uc -t 'cd ~/shufeng/AAALion- && exec $SHELL'
```

Assumes `~/.ssh/config` has a `uc` Host entry. If not, set one up:

```
Host uc
  HostName <your.a100.host>
  User <your.user>
  IdentityFile ~/.ssh/<your.key>
```

## Initial bring-up on the A100 (one-time)

```bash
ssh uc
ls ~/shufeng/                                                  # confirm gpu-fuzz/ present
mkdir -p ~/shufeng/AAALion- && cd ~/shufeng/AAALion-
git clone https://github.com/YushengLiSam/AAALion-.git . || echo "private — rsync from laptop"
python3 -m venv .venv && source .venv/bin/activate
pip install -r rag/requirements.txt
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expected last line: `True NVIDIA A100-...`.

## Estimated A100 utilization

- CLIP indexing 100-200 product images: < 1 minute.
- Batch RAG eval over 30 golden queries: < 10 seconds.
- Total weekly compute: under 30 minutes. The A100 is overkill but cheap to use.

## Future hardware

- **Mac mini M4** (if purchased): use as the always-on backend host on LAN; the team can demo from any iPhone in the room without dragging a laptop along.
- **An extra iPhone** (Sam / Tujie's personal device): a second test target — particularly useful for verifying camera and dark-mode parity.
