#!/usr/bin/env bash
# SSH into the A100 server and drop into the project namespace.
#
# Assumes ~/.ssh/config has a `Host uc` entry. If not, see docs/HARDWARE.md.
# Strictly drops you into ~/shufeng/AAALion-/; never modify gpu-fuzz/.

set -euo pipefail

exec ssh uc -t 'cd ~/shufeng/AAALion- && exec "$SHELL" -l'
