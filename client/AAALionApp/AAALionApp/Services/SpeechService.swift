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
    /// from a prior turn). We reset this timer every time a new partial
    /// arrives; if 1.8 s pass without any new partial we treat it as
    /// "user stopped" and stop the session. Matches ChatGPT/Claude UX.
    private var idleTimer: Timer?
    private let idleTimeout: TimeInterval = 1.8

    private(set) var isRecording = false
    var onTranscript: ((String) -> Void)?
    var onError: ((String) -> Void)?

    private init() {}

    /// Schedule (or re-schedule) the silence-timeout. Called once at
    /// `start()` so a tap-with-no-speech still releases, and again on
    /// every partial result so an active speaker doesn't get cut off.
    private func resetIdleTimer() {
        idleTimer?.invalidate()
        idleTimer = Timer.scheduledTimer(withTimeInterval: idleTimeout, repeats: false) { [weak self] _ in
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
            onError?("zh-CN 语音识别不可用")
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
                    self.onTranscript?(result.bestTranscription.formattedString)
                    // R8.E-VOICE: speech is active → push the silence
                    // timeout out. If no further partial arrives within
                    // idleTimeout, we'll auto-stop.
                    self.resetIdleTimer()
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
        // Release the audio session so subsequent sessions start clean.
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
