import AVFoundation
import Foundation
import Speech

/// Streams transcribed text from the microphone using Apple's `Speech`
/// framework. Tap mic → start; tap again → stop. Transcribed text is
/// delivered to `onTranscript` on the main actor as each partial result
/// becomes available.
@MainActor
final class SpeechService {
    static let shared = SpeechService()

    private let recognizer: SFSpeechRecognizer? = SFSpeechRecognizer(locale: Locale(identifier: "zh_CN"))
    private let audioEngine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    /// Monotonic session ID. Bumped on each `start()`. Callbacks from
    /// older sessions check this guard before delivering transcripts,
    /// so a stale partial-result from the previous tap can't pollute
    /// the new recording's draft text. This fixes the "I said 'toy',
    /// sent it; then said 'cosmetic' but it shows 'toy and cosmetic'"
    /// bug: that was the prior task's delayed callback racing with
    /// the new task's first partial.
    private var sessionID: UInt64 = 0

    /// R8.E-VOICE: auto-stop on silence. SFSpeechRecognizer doesn't release
    /// itself when the user stops talking; without this the red mic stays
    /// on forever and the draft picks up garbage (or carries stale text
    /// from a prior turn). We reset this timer ONLY when a partial arrives
    /// whose transcript is *strictly different* from the previous one
    /// (see `lastTranscript`). If the user stops talking, the recognizer
    /// may still emit "same text" partials from ambient noise — those
    /// must NOT extend the window, or the mic never releases.
    ///
    /// R9.A.7 — TWO timeouts:
    ///   * `firstUtteranceTimeout` = 6 s — wait for the user to start
    ///     speaking. Tapping mic and thinking what to say can easily
    ///     take 3-5 s; 1.8 s here was killing the recording before
    ///     the user could open their mouth.
    ///   * `idleTimeout` = 1.8 s — switches in AFTER the first partial
    ///     arrives. Now we're confident the user is mid-utterance, so
    ///     the tighter window catches end-of-speech promptly.
    private var idleTimer: Timer?
    private let firstUtteranceTimeout: TimeInterval = 6.0
    private let idleTimeout: TimeInterval = 1.8
    /// Last partial transcript seen this session. Used to suppress the
    /// "noise keeps re-firing the same text" idle-timer extension.
    private var lastTranscript: String = ""
    /// True once the user has produced ANY non-empty partial transcript.
    /// Drives the timeout switch from firstUtteranceTimeout → idleTimeout.
    private var hasReceivedFirstUtterance: Bool = false

    private(set) var isRecording = false
    var onTranscript: ((String) -> Void)?
    var onError: ((String) -> Void)?
    /// R8.E-VOICE-FIX: fired whenever the recognizer stops — including
    /// when the idle-timer auto-stops it. The ViewModel listens here so
    /// it can flip its own `isRecording` back to false (otherwise the UI
    /// stays red even though the audio engine is already shut down).
    var onStop: (() -> Void)?

    private init() {}

    /// Schedule (or re-schedule) the silence-timeout. Two-stage policy
    /// (R9.A.7): use the longer firstUtteranceTimeout before any partial
    /// arrives (give the user time to think + start speaking), then
    /// switch to the tight idleTimeout once they've started.
    ///
    /// Uses RunLoop.main with .common modes so it still fires while the
    /// user is scrolling.
    private func resetIdleTimer() {
        idleTimer?.invalidate()
        let interval = hasReceivedFirstUtterance ? idleTimeout : firstUtteranceTimeout
        let t = Timer(timeInterval: interval, repeats: false) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                // Guard against firing after an explicit stop or after
                // a newer session started — in either case `isRecording`
                // is already false and `stop()` would be a no-op anyway.
                if self.isRecording {
                    self.stop()
                }
            }
        }
        RunLoop.main.add(t, forMode: .common)
        idleTimer = t
    }

    /// Asks for mic + speech recognition permission. Call before first start.
    func requestAuthorization() async -> Bool {
        let speechStatus: SFSpeechRecognizerAuthorizationStatus = await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in cont.resume(returning: status) }
        }
        guard speechStatus == .authorized else { return false }
        let micGranted: Bool = await withCheckedContinuation { cont in
            AVAudioApplication.requestRecordPermission { granted in cont.resume(returning: granted) }
        }
        return micGranted
    }

    func start() throws {
        // Defensive: if a previous session is still half-alive
        // (audio engine running, task not yet fully cancelled), tear it
        // down before we open a new one. Idempotent.
        if isRecording {
            stop()
        }

        guard let recognizer, recognizer.isAvailable else {
            onError?(L("zh-CN 语音识别不可用"))
            return
        }

        // Bump session ID so any late callbacks from the previous task
        // can detect they're stale and exit without firing onTranscript.
        sessionID &+= 1
        let mySession = sessionID

        // Configure audio session.
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: .duckOthers)
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        request = SFSpeechAudioBufferRecognitionRequest()
        request?.shouldReportPartialResults = true

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)

        task = recognizer.recognitionTask(with: request!) { [weak self] result, error in
            guard let self else { return }
            // R8.D-FIX: drop callbacks that arrive after a newer session started.
            // Without this guard, the prior task can fire one last partial
            // result AFTER the user taps mic again, polluting the new draft
            // with stale text like "toy and cosmetic".
            Task { @MainActor in
                guard self.sessionID == mySession else { return }
                if let result {
                    let newText = result.bestTranscription.formattedString
                    self.onTranscript?(newText)
                    // R8.E-VOICE-FIX: ONLY reset the silence timer when
                    // the transcript actually changed. SFSpeechRecognizer
                    // keeps emitting partials with the SAME text on
                    // ambient noise / breathing / room hum — if every
                    // such partial extended the window, the mic would
                    // never auto-release. Comparing `newText !=
                    // lastTranscript` is the cheapest correct guard.
                    if newText != self.lastTranscript {
                        self.lastTranscript = newText
                        // R9.A.7 — once the first NON-EMPTY transcript
                        // arrives, switch from the long pre-utterance
                        // grace window to the tight 1.8s end-of-speech
                        // window.
                        if !newText.isEmpty {
                            self.hasReceivedFirstUtterance = true
                        }
                        self.resetIdleTimer()
                    }
                }
                if error != nil || (result?.isFinal ?? false) {
                    self.stop()
                }
            }
        }

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            self?.request?.append(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()
        isRecording = true
        // Tap-without-speech still releases the mic after idleTimeout.
        resetIdleTimer()
    }

    func stop() {
        guard isRecording else { return }
        // R8.E-VOICE: tear down the silence-timeout first so it can't fire
        // a redundant stop() while we're already stopping.
        idleTimer?.invalidate()
        idleTimer = nil
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        // R8.D-FIX: finish() lets the recognizer flush any in-flight audio
        // gracefully; cancel() can leave the task in a state that fires
        // one more callback. Bump sessionID first so any such late callback
        // hits the staleness guard and no-ops.
        sessionID &+= 1
        task?.finish()
        request = nil
        task = nil
        isRecording = false
        lastTranscript = ""
        hasReceivedFirstUtterance = false
        // Release the audio session so subsequent sessions start clean.
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        // R8.E-VOICE-FIX: notify the ViewModel so its own `isRecording`
        // can flip to false — otherwise the UI mic stays red even though
        // we've torn down the audio engine. This is the path that fires
        // when the idle timer auto-stops us; the manual-tap path also
        // hits it but ChatViewModel.stopListening was already clearing
        // its flag synchronously (idempotent).
        onStop?()
    }
}
