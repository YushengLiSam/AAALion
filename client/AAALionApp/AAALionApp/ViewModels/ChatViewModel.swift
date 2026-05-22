import Foundation
import Observation

@Observable
final class ChatViewModel {
    var messages: [Message] = []
    var draft: String = ""
    var isStreaming: Bool = false
    var errorMessage: String?

    private var streamTask: Task<Void, Never>?
    private let service: ChatService

    init(service: ChatService = ChatService()) {
        self.service = service
    }

    func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming else { return }
        draft = ""

        let userMessage = Message(role: .user, text: text)
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
