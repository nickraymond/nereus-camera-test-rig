---
name: Pi Deploy
description: Get the latest code onto the Raspberry Pi (nereus000) over SSH/Tailscale. Clones the repo on first run, pulls updates after that, and reports the commit the Pi is on. Use when asked to "deploy to the pi", "update the pi", "get latest code on the pi", or before running anything on the pi.
---

# Deploy latest code to the Raspberry Pi

Bring the Pi's copy of the repo up to date with `main` on GitHub, then confirm
what commit the Pi is now on.

## Connection details

- **SSH host:** `pi@nereus000` (Tailscale MagicDNS; auth handled by Tailscale — no password)
- **Repo path on the Pi:** `~/nereus-camera-test-rig`
- **Clone URL:** `https://github.com/nickraymond/nereus-camera-test-rig.git` (repo is **public** → HTTPS needs no key)
- **Default branch:** `main`

Prefer these SSH options so a call fails fast instead of hanging on a prompt:
`ssh -o ConnectTimeout=12 -o BatchMode=yes pi@nereus000 "..."`

## Prerequisites (one-time, needs sudo)

The Pi must have `git`, `python3-venv`, and `python3-pip` installed. These require
`sudo` (password), so a human runs the setup once — see `scripts/install_pi.sh`.
Until then, `git` is unavailable and the clone step below will fail.

## Workflow

Each SSH call is a fresh shell, so every command must `cd` into the repo itself.

1. **Sanity check the connection**
   ```bash
   ssh -o ConnectTimeout=12 -o BatchMode=yes pi@nereus000 "echo connected && hostname"
   ```
   If this fails, stop and tell the user — Tailscale or SSH is down.

2. **Clone on first run, pull on every run after** (one idempotent command)
   ```bash
   ssh -o BatchMode=yes pi@nereus000 'REPO=~/nereus-camera-test-rig; \
     if [ -d "$REPO/.git" ]; then \
       cd "$REPO" && git fetch origin && git checkout main && git pull --ff-only 2>&1; \
     else \
       git clone https://github.com/nickraymond/nereus-camera-test-rig.git "$REPO" 2>&1; \
     fi'
   ```

3. **Confirm what the Pi is now running**
   ```bash
   ssh -o BatchMode=yes pi@nereus000 "cd ~/nereus-camera-test-rig && git log -1 --oneline && git status -s"
   ```

4. **Report back to the user**
   State the branch, the latest commit (hash + message), and whether the working
   tree was clean. If `git pull` reported conflicts or a non-fast-forward, do NOT
   force anything — surface the error and ask how to proceed.

## Notes & gotchas

- The repo is public, so HTTPS clone/pull needs no credentials. If it is ever made
  private, the Pi will need a deploy key or `ssh -A` agent forwarding — flag that
  rather than guessing.
- `--ff-only` is deliberate: it refuses to create merge commits on the Pi. The Pi is
  a deploy target, not a place to author changes. If a pull is rejected, someone
  committed directly on the Pi — investigate before overwriting.
- Never run `git reset --hard` or `git checkout -f` on the Pi without explicit
  confirmation; it can wipe local hardware-config changes.
- To deploy a non-`main` branch for testing, substitute the branch name in step 2's
  `checkout`/`pull` (and mention it in the report).
