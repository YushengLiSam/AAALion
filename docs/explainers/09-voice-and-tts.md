# 09 — Voice input and text-to-speech

## What is this?

Two things:

1. **Voice input** — tap the microphone button, speak your question in
   Chinese, see it transcribed into the chat composer. Pause for a
   moment and the mic auto-releases.
2. **Text-to-speech (TTS)** — when the assistant replies, you can tap
   the speaker icon (or enable auto-read in Settings) to have your phone
   read the answer aloud in Chinese.

Together they let you use the app hands-free — useful for accessibility
or just walking around while shopping.

## Why does it matter?

E-commerce apps are mostly text-driven, which assumes the user is
looking at the screen and can type. That's a narrow assumption. Voice
removes the typing barrier — anyone who can speak Chinese can use the
app. TTS removes the reading barrier — anyone who can hear can consume
the recommendation.

For a defense rubric labeled "bonus features", voice + TTS is a
high-effort, high-visibility add. ChatGPT iOS has it. Most competing
shopping apps don't.

## How we built it

### Voice input — `SFSpeechRecognizer`

Apple's `Speech` framework (part of every iPhone since iOS 10) gives us
on-device Chinese speech recognition. We don't need to call an external
API — privacy and latency are both better.

The high-level flow (`client/AAALionApp/AAALionApp/Services/SpeechService.swift`):

1. User taps the mic button → ChatViewModel calls `SpeechService.start()`.
2. We request mic + speech-recognition permission once (system dialog).
3. We open `SFSpeechRecognizer(locale: "zh_CN")` and an
   `AVAudioEngine` that feeds raw audio buffers into the recognizer.
4. The recognizer fires a callback every time it gets a new "partial
   result" — typically every 200-500 ms.
5. Each partial-result callback updates the composer's draft text in
   real time. The user sees their words appearing as they speak.
6. After 1.8 seconds of silence (no new partial results), we auto-stop
   the recognizer. The mic icon goes back to grey.

### The two bugs we had to fix

**Bug 1: text from the previous session leaks into the new one.** The
user says "toy", sends it. Says "cosmetic" — the composer shows "toys
and cosmetic". What was happening: when you cancel a recognition task,
Apple's framework fires ONE more callback after the cancel — but the
new task has already started, and the late callback overwrites the new
text. Classic race condition.

Fix: every time we start a new session, we bump a counter
(`sessionID`). The recognition callback checks this counter before it
does anything; if the counter has changed since the callback was
scheduled, the callback is from an old session and we ignore it.

```swift
let mySession = sessionID  // captured at task creation
// later, in the callback:
guard sessionID == mySession else { return }  // stale, drop it
```

**Bug 2: the mic stays on forever.** Apple's `SFSpeechRecognizer`
doesn't auto-stop on silence — you have to call `stop()` yourself.
Without that, the mic keeps listening, picks up ambient noise, and the
draft keeps churning with garbage transcripts of room hum.

Fix: a **1.8-second idle timer**. Every time we receive a partial
result WHERE THE TEXT CHANGED, we restart the timer. If 1.8 seconds
pass without the text changing, we auto-stop. Same-text repeat
callbacks (which happen when the recognizer is just confirming what it
already heard) don't reset the timer.

We picked 1.8 seconds by experiment. 1.0 cuts off slow speakers
mid-sentence. 2.5 feels sluggish. ChatGPT and Claude iOS both feel
roughly the same as 1.8 in our testing.

Code: the `resetIdleTimer()` function in `SpeechService.swift`.

### TTS — `AVSpeechSynthesizer`

iOS's `AVSpeechSynthesizer` reads any string aloud using the system TTS
engine. We pick the Chinese voice and configure rate/pitch defaults.
Code: `client/AAALionApp/AAALionApp/Services/TTSService.swift`.

Trigger paths:

- **Manual**: tap the speaker icon on any assistant message → reads that
  message.
- **Auto** (opt-in via Settings toggle): when an assistant reply
  arrives, we detect the end of the first paragraph (a period, an
  exclamation mark, a double newline, or 200 characters — whichever
  comes first) and start reading. This is fast: the user hears the
  recommendation BEFORE the LLM has finished generating all the detail.

We use a `Set<UUID>` of message IDs we've already auto-read so a
single message doesn't get read twice when it streams in chunks. Code:
`ChatViewModel.maybeSpeakFirstParagraph(...)`.

### Why on-device and not cloud TTS?

Cloud TTS (Google Cloud TTS, Azure, etc.) sounds better but has three
costs: (a) network round trip adds 200-500 ms, (b) per-call billing,
(c) requires user audio to leave the device. For a demo and a defense,
the system voice is "good enough" and avoids all three costs. If we
were building this as a product, we'd swap in a higher-quality engine.

## What you actually see

```
[User taps mic — mic icon turns red, composer shows "正在听… / Listening — auto-stops on silence"]
[User speaks: "推荐一款洗面奶"]
[Composer fills in real-time as they speak]
[User stops talking]
[After 1.8 s, mic icon turns grey, "正在听…" hint disappears]
[User reviews text, taps send]
```

For TTS:

```
[Assistant message arrives — speaker icon visible at the bottom of each bubble]
[User taps speaker → phone reads the message in Mandarin]
```

Or with auto-read enabled:

```
[Assistant first paragraph arrives → phone speaks automatically]
[Rest of the message types out silently]
```

## Where to dig deeper

- `client/AAALionApp/AAALionApp/Services/SpeechService.swift` — voice
  input, idle timer, sessionID guard.
- `client/AAALionApp/AAALionApp/Services/TTSService.swift` — TTS engine
  wrapper.
- `client/AAALionApp/AAALionApp/ViewModels/ChatViewModel.swift` — the
  `startListening` / `stopListening` glue + the auto-read first-paragraph
  detector.
- `docs/cluely/log.md` (LOCAL) — full debug history of the two voice
  bugs.
