import Foundation

/// Streams `ChatDelta` events from the backend's SSE endpoint.
struct ChatService {
    var backendURL: URL = Config.backendURL

    struct ChatRequest: Encodable {
        // Wire shape: each message has role + content.
        // Content is either a plain string (text-only, backward-compatible)
        // or a list of OpenAI-style content parts (text + image_url) for
        // multimodal sends.
        struct TextPart: Encodable {
            let type = "text"
            let text: String
        }
        struct ImageURL: Encodable {
            let url: String
        }
        struct ImagePart: Encodable {
            let type = "image_url"
            let image_url: ImageURL
        }
        enum ContentPart: Encodable {
            case text(TextPart)
            case image(ImagePart)
            func encode(to encoder: Encoder) throws {
                var c = encoder.singleValueContainer()
                switch self {
                case .text(let t): try c.encode(t)
                case .image(let i): try c.encode(i)
                }
            }
        }
        enum Content: Encodable {
            case plain(String)
            case parts([ContentPart])
            func encode(to encoder: Encoder) throws {
                var c = encoder.singleValueContainer()
                switch self {
                case .plain(let s): try c.encode(s)
                case .parts(let p): try c.encode(p)
                }
            }
        }
        struct WireMessage: Encodable {
            let role: String
            let content: Content
        }
        struct Filters: Encodable {
            var category: String?
            var subCategory: String?
            var priceMin: Double?
            var priceMax: Double?
            var includeBrands: [String]?
            var excludeBrands: [String]?

            enum CodingKeys: String, CodingKey {
                case category
                case subCategory = "sub_category"
                case priceMin = "price_min"
                case priceMax = "price_max"
                case includeBrands = "include_brands"
                case excludeBrands = "exclude_brands"
            }
        }

        let messages: [WireMessage]
        let filters: Filters?
        // R9.B — anonymous per-device id so the backend can apply this
        // user's 👍/👎 preference prior. Omitted (nil) → pure relevance.
        var userId: String? = nil

        enum CodingKeys: String, CodingKey {
            case messages
            case filters
            case userId = "user_id"
        }
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
                    // Each event arrives as a single `data: …` line on this
                    // platform; `URLSession.bytes(_:).lines` is observed to
                    // elide blank separator lines on iOS 17/18, so we decode
                    // line-by-line rather than relying on the SSE empty-line
                    // boundary.
                    let decoder = JSONDecoder()
                    for try await line in bytes.lines {
                        if Task.isCancelled { break }
                        let payload: String
                        if line.hasPrefix("data: ") {
                            payload = String(line.dropFirst(6))
                        } else if line.hasPrefix("data:") {
                            payload = String(line.dropFirst(5))
                        } else {
                            continue
                        }
                        guard let data = payload.data(using: .utf8) else { continue }
                        if let event = try? decoder.decode(ChatDelta.self, from: data) {
                            continuation.yield(event)
                            if case .done = event { break }
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
            messages: messages.map { msg in
                let content: ChatRequest.Content
                // R8.E: send up to `Attachment.maxCount` image parts. Vision-
                // capable LLMs (Claude / GPT-4o / Doubao Vision) handle
                // multi-image content arrays natively; the backend forwards
                // the entire list to the provider, while the CLIP retriever
                // currently uses only attachments[0] (see chat.py
                // `_extract_image_bytes_list`).
                let imageAttachments = msg.attachments.filter { $0.isImage }
                if !imageAttachments.isEmpty {
                    var parts: [ChatRequest.ContentPart] = []
                    if !msg.text.isEmpty {
                        parts.append(.text(.init(text: msg.text)))
                    }
                    for attachment in imageAttachments {
                        let b64 = attachment.data.base64EncodedString()
                        let dataURL = "data:\(attachment.mime);base64,\(b64)"
                        parts.append(.image(.init(image_url: .init(url: dataURL))))
                    }
                    content = .parts(parts)
                } else {
                    content = .plain(msg.text)
                }
                return .init(role: msg.role.rawValue, content: content)
            },
            filters: filters,
            // R9.B — attach the anonymous device id so the backend applies
            // this user's 👍/👎 preference prior to the results.
            userId: DeviceIdentity.userId
        )
        request.httpBody = try JSONEncoder().encode(wire)
        return request
    }
}
