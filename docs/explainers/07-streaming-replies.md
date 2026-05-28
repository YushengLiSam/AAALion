# 07 — Why the answer types itself out word by word

## What is this?

When you send a message in our app, the reply doesn't appear all at once.
The first few words show up almost immediately, and the rest streams in
over a couple of seconds. This isn't a fake animation — it's the LLM
producing words one at a time, and we're forwarding each word to your
phone the moment it arrives. The technology that makes this work is
called **Server-Sent Events (SSE)**.

## Why does it matter?

A small RAG-LLM query end-to-end takes 4-8 seconds on our setup. If we
waited for the WHOLE reply before showing anything, the user would stare
at a blank screen for 7 seconds and probably tap something else,
thinking it was broken.

By streaming, the user sees the first word in about 1 second — fast
enough to feel responsive. They start reading; the rest catches up
naturally. Perceived latency drops from "7 seconds" to "1 second" even
though the total time is the same.

ChatGPT, Claude, Gemini — they all do this. It's the modern AI app
standard. We do it because we want our app to feel like those, not like
2010-era chatbots.

## How we built it

### Server-Sent Events in 60 seconds

SSE is an old web technology (in browsers since ~2010) that's perfect
for "server sends a stream of small updates to a long-lived HTTP
connection". The HTTP response stays open; the server writes lines like:

```
data: {"type":"delta","text":"我"}

data: {"type":"delta","text":"为"}

data: {"type":"delta","text":"您"}

data: {"type":"product_card","product":{...}}

data: {"type":"done"}
```

Each line is prefixed with `data: ` and ended with a blank line. The
client reads one line at a time and reacts to each.

It's simpler than WebSockets (which is bi-directional) because here we
only need one direction: server → client.

### The server side

`server/app/routes/chat.py` defines `/chat/stream` as a FastAPI
`StreamingResponse`. The endpoint function is an `async def generator()`
that:

1. Calls the LLM via `provider.stream_chat(history)` — this is an async
   iterator that yields tokens (small pieces of text) as the LLM
   produces them.
2. For each token, wraps it in a JSON event `{"type":"delta", "text":"…"}`
   and writes it as `data: {…}\n\n`.
3. After all tokens, writes one event per retrieved product card:
   `{"type":"product_card", "product":{…}}`.
4. Optionally writes a `{"type":"cart_intent", "action":"add"}` if the
   user said something like "加入购物车".
5. Finally writes `{"type":"done"}` and closes the response.

The whole thing is async so the FastAPI server can handle other
requests while one chat stream is in flight.

There's a subtle bit: if the client disconnects (closes the app), we
detect it via `request.is_disconnected()` and STOP calling the LLM. This
saves money (we'd otherwise burn tokens generating a reply nobody will
see).

### The iOS side

iOS has built-in support for HTTP streams via `URLSession.bytes(for:)`.
Our `ChatService.swift` opens the streaming response and reads it line
by line, decoding each `data: {…}` line into a `ChatDelta` enum:

```swift
for try await line in bytes.lines {
    if line.hasPrefix("data: ") {
        let payload = String(line.dropFirst(6))
        let event = try? JSONDecoder().decode(ChatDelta.self, from: data)
        // dispatch to the view model
    }
}
```

The view model appends each delta's text to the current assistant
message and SwiftUI re-renders the bubble — that's why you see it
"typing".

### The gotcha — `bytes.lines` elides blank separators

This is a real bug we found on iOS 17/18. `URLSession.bytes(for:).lines`
is supposed to give us each line one at a time. The SSE protocol
separates events with a blank line. But iOS quietly DROPS those blank
lines from the iterator.

For us this didn't matter because every event in our stream is a single
`data:` line — we never use multi-line events. But if someone in the
future adds them, the parsing would silently skip the second half. We
documented the workaround in `docs/TROUBLESHOOTING.md`: parse each line
as a complete event, don't rely on the blank-line boundary.

### Why not WebSockets?

WebSockets are full-duplex (both sides can send anytime). We don't need
that — the server is doing all the talking. SSE is simpler, works over
plain HTTP/HTTPS (no Upgrade handshake), survives most corporate
firewalls and CDNs, and integrates naturally with FastAPI's
`StreamingResponse`. Use the simplest tool that solves the problem.

## What the user sees vs what we're shipping

Three event types currently:

- **`delta`** — a piece of LLM text. There are dozens to hundreds of
  these per reply.
- **`product_card`** — a structured product object (id, title, brand,
  price, image, provenance). These arrive AFTER all the text, but
  before `done`. Each one renders as a card under the chat bubble.
- **`cart_intent`** — `{"action":"add"}` or `{"action":"checkout"}`.
  Fires when the user's message includes "加购" / "下单" / "结算". The
  iOS app immediately updates the cart UI without waiting for the LLM
  to finish.
- **`done`** — signals end of stream. The iOS app stops the loading
  animation and finalizes the message.

You can see the actual event flow by curling the endpoint:

```bash
curl -N -X POST http://localhost:8000/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"推荐洗面奶"}]}'
```

The `-N` flag disables curl's output buffering so you see each `data:`
line as it arrives.

## Where to dig deeper

- `server/app/routes/chat.py` — the `/chat/stream` endpoint and its
  `generator()` function.
- `client/AAALionApp/AAALionApp/Services/ChatService.swift` — the iOS
  side, including the SSE parsing.
- `docs/API.md` — the event taxonomy reference.
- `docs/TROUBLESHOOTING.md` §"SSE parser hang on iOS 17/18" — the
  gotcha mentioned above.
- [`08-cache-and-speed.md`](08-cache-and-speed.md) — how cached replies
  are also streamed for UX consistency.
