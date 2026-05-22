# AAALionApp (iOS)

The iOS client. SwiftUI, iOS 17+.

## Open in Xcode

This scaffold uses **plain Swift source files** committed to git. To build it, you need to create an Xcode project that includes them:

1. Open Xcode → File → New → Project → App.
2. Product Name: `AAALionApp`. Interface: SwiftUI. Language: Swift. Minimum deployment: iOS 17.0.
3. Save at `client/AAALionApp/` (this directory) — overwriting the placeholder. Xcode will create `AAALionApp.xcodeproj` here.
4. In Finder, drag the existing `AAALionApp/` Swift sources into the Xcode project navigator. **Uncheck** "Copy items if needed". Add to the `AAALionApp` target.
5. Delete the generated `ContentView.swift` (we use `ChatView.swift` instead).
6. Run on iOS 17+ Simulator (iPhone 15) or your real iPhone 13.

Once the `.xcodeproj` is created, commit it (it must be in the repo so other devs can open it). The user-specific `xcuserdata` is gitignored.

## Configure backend URL

Default is `http://localhost:8000`. Override at launch by setting environment variable `PUBLIC_BACKEND_URL` in your Xcode scheme (Product → Scheme → Edit Scheme → Run → Arguments → Environment Variables).

For real-device testing on the same Wi-Fi as your MacBook:
- Find your MacBook's LAN IP (`ipconfig getifaddr en0`).
- Set `PUBLIC_BACKEND_URL=http://<that-ip>:8000`.
- In `Info.plist` (autogen by Xcode), add an App Transport Security exception for that IP if not on HTTPS.

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
