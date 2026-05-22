import Foundation
import Observation

@Observable
final class ChatViewModel {
    var messages: [Message] = []
    var draft: String = ""
    var isStreaming: Bool = false
    var errorMessage: String?
    /// JPEG bytes of the image the user has staged for the next send.
    /// Set by the PhotosPicker flow. Cleared after a send.
    var pendingImage: Data?

    private var streamTask: Task<Void, Never>?
    private let service: ChatService

    init(service: ChatService = ChatService()) {
        self.service = service
    }

    /// Pre-fill and auto-send a query (and optionally an image) from launch args.
    /// `-test-query "<text>"`       — pre-fill the input and send.
    /// `-test-image-url <url>`      — fetch the image from URL, attach.
    /// (`-test-image <path>` is rejected by the iOS sandbox; use -test-image-url.)
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

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        // Allow image-only or text-only sends; not both empty.
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

    private func appendText(_ chunk: String, to id: UUID) {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].text.append(chunk)
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
}
