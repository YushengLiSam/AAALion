# 11 — Getting the app onto your phone over the public internet

## What is this?

Our backend runs on Shufeng's Mac. The iOS app runs on an iPhone. How
do they talk? Through several layers — LAN networking in early rounds,
then Cloudflare Tunnel for the demo phase, eventually a cloud VM for
the defense. This file walks through the why and the how.

## Why does it matter?

In Round 2, the iPhone could ONLY talk to the Mac when they were on the
same Wi-Fi. That broke any time the demo Wi-Fi was a different network,
or any time the user was on cellular, or any time a corporate firewall
blocked local IPs. We needed something that "just works" regardless of
which network the judge is on. Cloudflare Tunnel solved that. Now we're
planning Phase 2: a real cloud VM so the Mac isn't a single point of
failure during the defense.

## How we built it

### Phase 0 — localhost (works in simulator only)

The simplest case. The iOS simulator runs on the same Mac as the
backend. `Config.swift` defaults to `http://localhost:8000` and everything
"just works".

But the simulator isn't a real demo. Judges need to see it on a real
phone. So we have to leave localhost behind.

### Phase 1 — LAN (Rounds 2 to 7)

The Mac binds uvicorn to `0.0.0.0` (all network interfaces) and listens
on port 8000. We find the Mac's LAN IP with `ipconfig getifaddr en0`
(e.g., `192.168.22.50`). We hard-code that IP into `Config.swift`:

```swift
static let defaultBackendURL = "http://192.168.22.50:8000"
```

The iPhone, on the same Wi-Fi, can reach `http://192.168.22.50:8000`
because both devices are on the same subnet. Good enough for testing in
the dorm.

**Problems**:
- Different demo location = different Wi-Fi = different LAN IP. Have
  to rebuild the app.
- Cellular doesn't work (private IPs are unreachable from the internet).
- "AP isolation" routers (hotels, coffee shops) block client-to-client
  traffic on the same Wi-Fi.
- macOS Firewall sometimes blocks inbound traffic.

We documented all these in `docs/TROUBLESHOOTING.md` after debugging
them the hard way.

### Phase 2 — Cloudflare Tunnel (Round 8, what we're on now)

**Cloudflare Tunnel** is a service from Cloudflare that runs a small
daemon on your Mac (`cloudflared`) that opens a persistent outbound
connection to Cloudflare's edge. Cloudflare gives you back a public
HTTPS URL like `https://reader-missile-absolute-memphis.trycloudflare.com`.
Any request to that URL gets relayed through the Cloudflare edge → the
daemon → your local `localhost:8000`.

Why this is wonderful:
- The URL is HTTPS (so iOS doesn't complain about insecure HTTP).
- It works from any network the iPhone can reach the internet from —
  cellular, coffee-shop Wi-Fi, the defense panel's Wi-Fi.
- No firewall configuration needed because the connection is OUTBOUND
  from the Mac.
- Free for personal use.

We start the tunnel with a one-line wrapper:

```bash
# tools/start-tunnel.sh
exec cloudflared tunnel --url http://localhost:8000
```

The first time it runs, `cloudflared` prints the assigned URL. We copy
it into `Config.swift`'s `defaultBackendURL`, rebuild the iOS app, and
install on the iPhone. Done.

**Caveat**: the URL changes every time `cloudflared` restarts. For
stability across days, we'd switch to a "named tunnel" which gives us a
permanent subdomain. We'll do that closer to defense.

### Phase 2.5 — Dev-mode override

In the iOS Settings sheet, there's a hidden field that lets the user
override `defaultBackendURL` at runtime (saved in `UserDefaults`). It's
hidden by default so non-engineer users never see infrastructure config.
To unlock it: **long-press the gear icon on the chat screen for 1.5
seconds**. An amber gear icon and toast confirm dev-mode is on.

This is handy when:
- The baked tunnel URL is stale.
- We want to point at a local backend (`http://192.168.22.50:8000`) for
  one-off testing.
- We've moved to the cloud VM and want to swap URLs without rebuilding.

Code: `client/AAALionApp/AAALionApp/Views/ChatView.swift` (the toolbar
gesture) and `SettingsView.swift` (the field, gated by an
`@AppStorage("lionpick.devMode")` flag).

### Phase 3 — Cloud VM (planned for ~2026-06-05)

For the defense, we don't want the Mac in the room. The plan:

- Provision **Hetzner CX22** in Singapore: 4 vCPU, 8 GB RAM, 80 GB SSD,
  about €4.50/month. We chose Hetzner because they're the cheapest
  reasonable option and Singapore has good latency to mainland China.
- `docker compose up` the backend on the VM. Our `Dockerfile.rag` is
  already production-ready.
- Migrate the Chroma index (about 100 MB) via `rsync`.
- Set up a Cloudflare-managed domain (`api.lionpick.<something>.dev`).
- Bake that stable URL into `Config.swift`.

After Phase 3:
- The Mac is unused during defense.
- Cloudflare Tunnel demotes to "dev fallback" (still useful when
  hacking on the Mac locally).
- The iPhone hits the cloud VM directly.

### What we did NOT do

We didn't go the "Render free / Railway free / Fly.io free" route
because they all have cold-start penalties (apps spin down after a few
minutes idle, then take 20+ seconds to wake up). That's bad demo optics
when a judge taps in and waits 20 seconds. A €4.5/month always-on VM
has none of that problem.

## A small note on the URL we use

The current tunnel URL — at the time of writing —
`https://reader-missile-absolute-memphis.trycloudflare.com` — is a
**random subdomain** picked by Cloudflare. If you restart the tunnel,
you get a different random string. We re-bake the iOS app whenever
this changes.

## Where to dig deeper

- `tools/start-tunnel.sh` — the cloudflared wrapper.
- `client/AAALionApp/AAALionApp/Config.swift` — the URL resolution
  order (env var → UserDefaults → baked default).
- `docs/DEPLOY_GUIDE.md` — full teammate onboarding for Mac+iPhone
  deploy.
- `docs/TROUBLESHOOTING.md` §"iPhone shows 无法连接服务器" — the LAN
  debugging history that motivated Cloudflare Tunnel.
- [`10-app-architecture.md`](10-app-architecture.md) — where this fits
  in the overall system.
