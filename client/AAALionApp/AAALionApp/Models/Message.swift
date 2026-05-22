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
    /// Optional JPEG bytes attached to a user message (拍照找货). Rendered
    /// inline above the text bubble; sent to the backend as a base64
    /// image_url part inside the OpenAI-style content array.
    var imageData: Data?

    init(
        id: UUID = UUID(),
        role: Role,
        text: String = "",
        products: [ProductCard] = [],
        isStreaming: Bool = false,
        imageData: Data? = nil
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.products = products
        self.isStreaming = isStreaming
        self.imageData = imageData
    }
}
