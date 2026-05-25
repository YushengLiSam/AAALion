import Foundation
import Observation

@Observable
final class ChatViewModel {
    var messages: [Message] = []
    var draft: String = ""
    var isStreaming: Bool = false
    var errorMessage: String?
    /// JPEG bytes of the image the user has staged for the next send.
    /// Set by the PhotosPicker / Camera / Files flows. Cleared after a send.
    var pendingImage: Data?
    /// True while the mic is active.
    var isRecording: Bool = false
    /// Most recent cart intent emitted by the backend (consumed by ChatView).
    var cartIntent: String? = nil

    private var streamTask: Task<Void, Never>?
    private let service: ChatService

    init(service: ChatService = ChatService()) {
        self.service = service
    }

    // MARK: - Scripted harness for simctl-driven demos.

    func runScriptedQueryIfAny() {
        let args = ProcessInfo.processInfo.arguments
        let queryIdx = args.firstIndex(of: "-test-query")
        let imageURLIdx = args.firstIndex(of: "-test-image-url")
        let query: String? = (queryIdx.flatMap { idx in
            idx + 1 < args.count ? args[idx + 1] : nil
        })
        let imageURLString: String? = (imageURLIdx.flatMap { idx in
            idx + 1 < args.count ? args[idx + 1] : nil
        })
        guard query != nil || imageURLString != nil else { return }

        Task { @MainActor in
            if let urlStr = imageURLString, let url = URL(string: urlStr) {
                do {
                    let (data, _) = try await URLSession.shared.data(from: url)
                    self.pendingImage = data
                } catch {
                    self.errorMessage = "test-image fetch failed: \(error.localizedDescription)"
                }
            }
            try? await Task.sleep(nanoseconds: 350_000_000)
            if let q = query, !q.isEmpty {
                self.draft = q
            }
            self.send()
        }
    }

    // MARK: - Send / cancel.

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!text.isEmpty || pendingImage != nil), !isStreaming else { return }
        draft = ""
        let imageData = pendingImage
        pendingImage = nil

        let userMessage = Message(role: .user, text: text, imageData: imageData)
        messages.append(userMessage)

        let assistantMessage = Message(role: .assistant, text: "", isStreaming: true)
        messages.append(assistantMessage)
        let assistantId = assistantMessage.id

        isStreaming = true
        errorMessage = nil

        streamTask?.cancel()
        streamTask = Task { [service] in
            do {
                for try await event in service.stream(messages: messages) {
                    switch event {
                    case .text(let chunk):
                        await MainActor.run { self.appendText(chunk, to: assistantId) }
                    case .product(let card):
                        await MainActor.run { self.appendProduct(card, to: assistantId) }
                    case .cartIntent(let action):
                        await MainActor.run { self.cartIntent = action }
                    case .error(let message):
                        await MainActor.run { self.errorMessage = message }
                    case .done:
                        break
                    }
                }
            } catch {
                await MainActor.run { self.errorMessage = error.localizedDescription }
            }
            await MainActor.run { self.finalize(messageId: assistantId) }
        }
    }

    func cancel() {
        streamTask?.cancel()
        streamTask = nil
        if let lastId = messages.last?.id {
            finalize(messageId: lastId)
        }
    }

    // MARK: - Edit last user message (ChatGPT/Claude-style).

    /// Roll back history to just before the given user message and refill
    /// the composer with its text. Discards any assistant reply that
    /// followed.
    func editMessage(id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        let target = messages[index]
        guard target.role == .user else { return }
        draft = target.text
        pendingImage = target.imageData
        messages.removeSubrange(index..<messages.count)
        cancel()
    }

    // MARK: - TTS.

    func speakAssistant(text: String) {
        guard !text.isEmpty else { return }
        TTSService.shared.speak(text)
    }

    // MARK: - Voice input.

    func startListening() {
        Task { @MainActor in
            let ok = await SpeechService.shared.requestAuthorization()
            guard ok else {
                self.errorMessage = "麦克风 / 语音权限未授权"
                return
            }
            // R8.D-FIX: clear the draft before recording. Without this,
            // any leftover text from a previous send (or a typed prefix)
            // remains, and stale partial-results from prior sessions
            // can append onto it. Combined with the SpeechService
            // session-ID guard, this makes each tap-to-record start
            // from a clean slate.
            self.draft = ""
            SpeechService.shared.onTranscript = { [weak self] text in
                self?.draft = text
            }
            SpeechService.shared.onError = { [weak self] msg in
                self?.errorMessage = msg
            }
            do {
                try SpeechService.shared.start()
                self.isRecording = true
            } catch {
                self.errorMessage = "无法启动语音识别: \(error.localizedDescription)"
            }
        }
    }

    func stopListening() {
        Task { @MainActor in
            SpeechService.shared.stop()
            self.isRecording = false
        }
    }

    // MARK: - Auto-TTS state (R7 nice-to-have).
    //
    // When the user enables `lionpick.autoTTS` in Settings, the assistant's
    // first complete paragraph (up to the first 。 / ! / ? / \n\n boundary
    // or 200 chars) is read aloud automatically. We track which assistant
    // message IDs have already had their first paragraph spoken so we don't
    // double-speak when more deltas arrive after the boundary.
    private var autoTTSSpokenMessageIDs: Set<UUID> = []

    private var autoTTSEnabled: Bool {
        UserDefaults.standard.bool(forKey: "lionpick.autoTTS")
    }

    // MARK: - Internal mutation.

    private func appendText(_ chunk: String, to id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].text.append(chunk)
        if autoTTSEnabled {
            maybeSpeakFirstParagraph(messageID: id)
        }
    }

    private func appendProduct(_ card: ProductCard, to id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].products.append(card)
    }

    private func finalize(messageId: UUID) {
        if let index = messages.firstIndex(where: { $0.id == messageId }) {
            messages[index].isStreaming = false
        }
        isStreaming = false
    }

    /// First-paragraph detector. Triggers TTS at the earliest of:
    /// - first sentence-end punctuation (`。`, `！`, `？`, `.`, `!`, `?`)
    /// - double-newline (paragraph break)
    /// - 200 chars (so chunky single-sentence replies don't go untold)
    private func maybeSpeakFirstParagraph(messageID: UUID) {
        guard !autoTTSSpokenMessageIDs.contains(messageID) else { return }
        guard let msg = messages.first(where: { $0.id == messageID }), msg.role == .assistant else { return }
        let t = msg.text
        let boundaries: [Character] = ["。", "！", "？", ".", "!", "?"]
        if t.count >= 200 || t.contains("\n\n") || t.contains(where: { boundaries.contains($0) }) {
            // Take everything up to and including the first sentence-end (or full string).
            var spoken = t
            if let idx = t.firstIndex(where: { boundaries.contains($0) }) {
                spoken = String(t[..<t.index(after: idx)])
            } else if let pp = t.range(of: "\n\n") {
                spoken = String(t[..<pp.lowerBound])
            } else if t.count > 200 {
                spoken = String(t.prefix(200))
            }
            autoTTSSpokenMessageIDs.insert(messageID)
            TTSService.shared.speak(spoken)
        }
    }
}
