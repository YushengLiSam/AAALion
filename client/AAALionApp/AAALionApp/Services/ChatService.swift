import Foundation

/// Streams `ChatDelta` events from the backend's SSE endpoint.
struct ChatService {
    var backendURL: URL = Config.backendURL

    struct ChatRequest: Encodable {
        struct WireMessage: Encodable {
            let role: String
            let content: String
        }
        struct Filters: Encodable {
            var category: String?
            var priceMax: Double?
            var excludeBrands: [String]?

            enum CodingKeys: String, CodingKey {
                case category
                case priceMax = "price_max"
                case excludeBrands = "exclude_brands"
            }
        }

        let messages: [WireMessage]
        let filters: Filters?
    }

    func stream(messages: [Message], filters: ChatRequest.Filters? = nil) -> AsyncThrowingStream<ChatDelta, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let request = try buildRequest(messages: messages, filters: filters)
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                        throw NSError(
                            domain: "ChatService",
                            code: (response as? HTTPURLResponse)?.statusCode ?? -1,
                            userInfo: [NSLocalizedDescriptionKey: "Unexpected status from backend"]
                        )
                    }
                    var buffer = ""
                    for try await line in bytes.lines {
                        if Task.isCancelled { break }
                        if line.isEmpty {
                            // event terminator
                            if let event = try decodeEvent(buffer: buffer) {
                                continuation.yield(event)
                                if case .done = event { break }
                            }
                            buffer = ""
                        } else {
                            buffer += line + "\n"
                        }
                    }
                    continuation.finish()
                } catch {
                    if !Task.isCancelled {
                        continuation.finish(throwing: error)
                    } else {
                        continuation.finish()
                    }
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func buildRequest(messages: [Message], filters: ChatRequest.Filters?) throws -> URLRequest {
        var request = URLRequest(url: backendURL.appendingPathComponent("chat/stream"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let wire = ChatRequest(
            messages: messages.map { .init(role: $0.role.rawValue, content: $0.text) },
            filters: filters
        )
        request.httpBody = try JSONEncoder().encode(wire)
        return request
    }

    private func decodeEvent(buffer: String) throws -> ChatDelta? {
        var payload: String?
        for line in buffer.split(separator: "\n") {
            if line.hasPrefix("data: ") {
                payload = String(line.dropFirst(6))
            } else if line.hasPrefix("data:") {
                payload = String(line.dropFirst(5))
            }
        }
        guard let payload, let data = payload.data(using: .utf8) else { return nil }
        return try JSONDecoder().decode(ChatDelta.self, from: data)
    }
}
