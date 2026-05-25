# AAALionApp (iOS) — 狮选 LionPick

The iOS client. SwiftUI, iOS 17+. App display name: **狮选**.

## Generate the Xcode project (recommended)

We use [**xcodegen**](https://github.com/yonaskolb/XcodeGen) so the `.xcodeproj` can be regenerated deterministically from `project.yml`. Install once:

```bash
brew install xcodegen
```

Then, from the repo root:

```bash
make ios     # equivalent to: cd client/AAALionApp && xcodegen
open client/AAALionApp/AAALionApp.xcodeproj
```

Press `Cmd+R` to build on the iOS 17 simulator (iPhone 15) or your iPhone 13.

`AAALionApp.xcodeproj` is **gitignored** — regenerate it from `project.yml` on every machine. `project.yml` IS committed and is the single source of truth.

## Manual setup (only if xcodegen unavailable)

1. Open Xcode → File → New → Project → App.
2. Product Name: `AAALionApp`. Interface: SwiftUI. Language: Swift. Minimum deployment: iOS 17.0. Display name: 狮选.
3. Save at `client/AAALionApp/`. Drag the existing `AAALionApp/` Swift sources into the project navigator (uncheck "Copy items").

## Configure backend URL

Default is `http://localhost:8000`. Override at launch by setting environment variable `PUBLIC_BACKEND_URL` in your Xcode scheme (Product → Scheme → Edit Scheme → Run → Arguments → Environment Variables).

For real-device testing on the same Wi-Fi as your MacBook:
- Find your MacBook's LAN IP (`ipconfig getifaddr en0`).
- Set `PUBLIC_BACKEND_URL=http://<that-ip>:8000`.
- In `Info.plist` (autogen by Xcode), add an App Transport Security exception for that IP if not on HTTPS.

## Price display

The backend returns `price_cny` for foreign-priced catalog items together
with an `exchange_rate` record. Product cards, product detail, cart, and
checkout use RMB as the primary displayed amount while preserving the
original foreign price and rate date. If no reference quote is available,
the item stays in its original currency and is excluded from the RMB total.

## File layout

```
AAALionApp/
├── AAALionAppApp.swift     # @main entrypoint
├── Config.swift            # backend URL
├── Models/
│   ├── Message.swift
│   ├── ProductCard.swift
│   └── ChatDelta.swift
├── Services/
│   ├── ChatService.swift   # SSE streaming
│   └── ProductService.swift
├── ViewModels/
│   └── ChatViewModel.swift # @Observable
└── Views/
    ├── ChatView.swift
    ├── MessageBubbleView.swift
    ├── ProductCardView.swift
    └── ProductDetailView.swift
```

## Tests

Add an `AAALionAppTests` target in Xcode and put `ChatService` SSE parser tests there. (Not yet in this scaffold — the next iteration.)
