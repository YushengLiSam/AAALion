import Foundation

struct Message: Identifiable, Hashable {
    enum Role: String, Codable {
        case user
        case assistant
    }

    let id: UUID
    let role: Role
    var text: String
    var products: [ProductCard]
    var isStreaming: Bool
    /// R8.E: up to `Attachment.maxCount` images / files attached to a user
    /// message. Replaces the single `imageData` from R6. Rendered above
    /// the text bubble as a 2-row grid (ChatGPT pattern); sent to the
    /// backend as multiple `image_url` parts inside the OpenAI-style
    /// content array.
    var attachments: [Attachment]

    init(
        id: UUID = UUID(),
        role: Role,
        text: String = "",
        products: [ProductCard] = [],
        isStreaming: Bool = false,
        attachments: [Attachment] = []
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.products = products
        self.isStreaming = isStreaming
        self.attachments = attachments
    }
}

extension Message {
    /// Convenience: bytes of the first image attachment, for legacy paths
    /// that still expect a single Data? (e.g. the message-edit flow that
    /// rolls back the composer state).
    var firstImageData: Data? {
        attachments.first(where: { $0.isImage })?.data
    }
}
