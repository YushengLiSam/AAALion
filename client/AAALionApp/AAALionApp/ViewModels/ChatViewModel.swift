import Foundation
import Observation

@Observable
final class ChatViewModel {
    var messages: [Message] = []
    var draft: String = ""
    var isStreaming: Bool = false
    var errorMessage: String?
    /// R8.E: up to `Attachment.maxCount` items the user has staged for the
    /// next send. Replaces the R6-era single `pendingImage: Data?`. Set
    /// by the PhotosPicker (plural) / CameraPicker (append per tap) /
    /// FileImporter (multi) flows. Cleared after a send.
    var pendingAttachments: [Attachment] = []
    /// True while the mic is active.
    var isRecording: Bool = false
    /// Most recent cart intent emitted by the backend (consumed by ChatView).
    var cartIntent: String? = nil
    /// R10 — ordinal carried by a "remove"/"set_quantity" cart intent
    /// ("删掉第二个" → 2; -1 means "last"). nil for add / checkout.
    var cartIntentIndex: Int? = nil
    /// R10 #4.1⭐⭐ — target quantity carried by a "set_quantity" intent
    /// ("把数量改成2" → 2). nil for other actions.
    var cartIntentQuantity: Int? = nil
    /// R10 #5 — 主动反问 quick-reply chips for the current clarification turn.
    /// Rendered above the composer; tapping one sends it as the next message.
    var clarifyChips: [String] = []

    /// Proactive repurchase reminders, fetched once when the chat view
    /// first appears. Rendered as a horizontal banner above the chat
    /// stream. Empty list = nothing to show, banner is hidden.
    /// Backend contract: `docs/REPURCHASE_PLAN.md` §3.2.
    var repurchaseReminders: [RepurchaseReminder] = []
    /// Toast shown briefly after "再来一单" tap. Drives the small
    /// "已加入购物车" pill rendered by ChatView. Cleared automatically
    /// after ~1.5s; the toast is purely an acknowledgement, not a queue.
    var repurchaseToast: String?
    private let repurchaseService = RepurchaseService()
    private var didFetchReminders = false

    /// Remaining slots in the composer. Surfaced to ChatView so the
    /// PhotosPicker can ask for `maxSelectionCount: remaining` and the
    /// "+" menu can grey itself out when 0.
    var remainingAttachmentSlots: Int {
        max(0, Attachment.maxCount - pendingAttachments.count)
    }

    /// Append a single attachment, truncating gracefully if the user
    /// somehow asked for more than `maxCount` (e.g. a multi-select that
    /// races with state). Sets `errorMessage` once on overflow so the
    /// UI can show "已达上限".
    func appendAttachment(_ attachment: Attachment) {
        guard remainingAttachmentSlots > 0 else {
            errorMessage = "已达上限 \(Attachment.maxCount) / Max \(Attachment.maxCount) attachments reached"
            return
        }
        pendingAttachments.append(attachment)
    }

    func removeAttachment(id: UUID) {
        pendingAttachments.removeAll { $0.id == id }
    }

    private var streamTask: Task<Void, Never>?
    private let service: ChatService

    // R9.A.3 — voice-to-cart intent patterns. Mirrors the server-side
    // _ADD_TO_CART / _CHECKOUT regex in chat.py — kept in sync so a typed
    // and a spoken "加购" behave identically. Compiled once.
    static let addCartIntentRegex: NSRegularExpression = {
        try! NSRegularExpression(pattern: "加入?购物?车|加购|加入车|放购物?车", options: [])
    }()
    static let checkoutIntentRegex: NSRegularExpression = {
        try! NSRegularExpression(pattern: "下单|结(账|算)|去结算|帮我下个?单|买单", options: [])
    }()

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
                    self.appendAttachment(.init(data: data, kind: .photo))
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

    /// R10 #5 — tapping a clarification chip sends it as the next message.
    func sendChip(_ chip: String) {
        guard !isStreaming else { return }
        draft = chip
        send()
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!text.isEmpty || !pendingAttachments.isEmpty), !isStreaming else { return }
        draft = ""
        // R10 #5 — a new turn supersedes any pending clarification chips.
        clarifyChips = []
        let staged = pendingAttachments
        pendingAttachments = []

        let userMessage = Message(role: .user, text: text, attachments: staged)
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
                    case .cartIntent(let action, let index, let quantity):
                        await MainActor.run {
                            self.cartIntentIndex = index
                            self.cartIntentQuantity = quantity
                            self.cartIntent = action
                        }
                    case .clarify(let chips):
                        await MainActor.run { self.clarifyChips = chips }
                    case .claimSummary(let v, let i):
                        await MainActor.run {
                            self.setClaimSummary(.init(verified: v, inferred: i), to: assistantId)
                        }
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
    /// the composer with its text + attachments. Discards any assistant
    /// reply that followed.
    func editMessage(id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        let target = messages[index]
        guard target.role == .user else { return }
        draft = target.text
        pendingAttachments = target.attachments
        messages.removeSubrange(index..<messages.count)
        cancel()
    }

    // MARK: - TTS.

    func speakAssistant(text: String) {
        guard !text.isEmpty else { return }
        TTSService.shared.speak(text)
    }

    // MARK: - Repurchase reminders (open-screen proactive).
    //
    // Called once from ChatView.onAppear. We don't poll — snooze
    // semantics live server-side (24 h dedup), so re-fetching on every
    // view re-render would only yield "fresh" data after the snooze
    // window expires anyway.

    /// One-shot fetch of due reminders for the open-screen banner.
    /// Fails soft — a transport / decode error leaves the banner empty
    /// rather than triggering a red `errorMessage` (proactive features
    /// shouldn't yell at the user on every cold launch).
    func fetchRepurchaseRemindersIfNeeded() {
        guard !didFetchReminders else { return }
        didFetchReminders = true
        let userId = DeviceIdentity.userId
        Task { @MainActor in
            do {
                let items = try await repurchaseService.fetchReminders(userId: userId, limit: 3)
                self.repurchaseReminders = items
            } catch {
                // Silent failure on purpose. Log to console for debug;
                // production-grade observability would route this to
                // a metrics endpoint instead.
                print("[repurchase] reminders fetch failed: \(error.localizedDescription)")
            }
        }
    }

    /// User tapped "再来一单" on a reminder card. R8.F.2 fix: this now
    /// **adds to cart directly** instead of seeding the chat composer
    /// (which was a shortcut that conflated re-ordering with chatting).
    /// Semantics:
    ///   * cart.add() — the same code path as the regular product card
    ///     "+ 加入购物车" pill, so the cart UI is consistent.
    ///   * NO POST /repurchase/purchase here — adding to cart is intent,
    ///     not purchase. The actual `record_purchase` fires at checkout
    ///     time (CheckoutView's "确认下单" button), so the cycle resets
    ///     only when the user truly buys.
    ///   * Banner snooze (24h dedup) is already locked in by the server
    ///     at the moment GET /reminders returned this row, so the same
    ///     item won't reappear regardless of this tap.
    func reorderFromReminder(_ reminder: RepurchaseReminder) {
        CartStore.shared.add(reminder.product)
        repurchaseReminders.removeAll { $0.id == reminder.id }
        repurchaseToast = "已加入购物车 · \(reminder.product.title)"
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(1.6))
            // Only clear if no newer toast superseded ours.
            if repurchaseToast?.contains(reminder.product.title) == true {
                repurchaseToast = nil
            }
        }
    }

    /// User explicitly closed (X) a reminder card. Local-only dismiss;
    /// server snooze already kicked in when /reminders returned the
    /// item (mark-shown is automatic on read), so the same item won't
    /// reappear for 24 h regardless of this action.
    func dismissReminder(_ reminder: RepurchaseReminder) {
        repurchaseReminders.removeAll { $0.id == reminder.id }
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
                guard let self else { return }
                self.draft = text
                // R9.A.3 — voice-to-cart: if the live transcript matches a
                // cart-intent regex (加购 / 加入购物车 / 下单 / 结算 etc.),
                // fire the cart action directly without waiting for the
                // user to tap Send. Mirrors ChatGPT's "Hey, add to cart"
                // shortcut but works in zh-CN.
                let lower = text.replacingOccurrences(of: " ", with: "")
                if Self.checkoutIntentRegex.firstMatch(
                    in: lower,
                    range: NSRange(lower.startIndex..., in: lower)
                ) != nil {
                    self.cartIntent = "checkout"
                    self.draft = ""
                    SpeechService.shared.stop()
                } else if Self.addCartIntentRegex.firstMatch(
                    in: lower,
                    range: NSRange(lower.startIndex..., in: lower)
                ) != nil {
                    self.cartIntent = "add"
                    self.draft = ""
                    SpeechService.shared.stop()
                }
            }
            SpeechService.shared.onError = { [weak self] msg in
                self?.errorMessage = msg
            }
            // R8.E-VOICE-FIX: SpeechService can stop itself via the idle
            // timer (1.8 s of silence). When that happens the UI-bound
            // `isRecording` flag here MUST be cleared too, otherwise the
            // red mic icon and "正在听…" hint stay on even though the
            // audio engine is already shut down.
            SpeechService.shared.onStop = { [weak self] in
                self?.isRecording = false
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

    private func setClaimSummary(_ summary: ClaimSummary, to id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].claimSummary = summary
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
