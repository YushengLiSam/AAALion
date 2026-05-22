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

    init(
        id: UUID = UUID(),
        role: Role,
        text: String = "",
        products: [ProductCard] = [],
        isStreaming: Bool = false
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.products = products
        self.isStreaming = isStreaming
    }
}
