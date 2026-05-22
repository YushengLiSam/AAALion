import Foundation

enum ChatDelta: Decodable {
    case text(String)
    case product(ProductCard)
    case cartIntent(String)      // "add" | "checkout"
    case error(String)
    case done

    private enum CodingKeys: String, CodingKey {
        case type
        case text
        case product
        case action
        case message
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        switch type {
        case "delta":
            self = .text(try container.decode(String.self, forKey: .text))
        case "product_card":
            self = .product(try container.decode(ProductCard.self, forKey: .product))
        case "cart_intent":
            let action = (try? container.decode(String.self, forKey: .action)) ?? "add"
            self = .cartIntent(action)
        case "error":
            self = .error(try container.decode(String.self, forKey: .message))
        case "done":
            self = .done
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type,
                in: container,
                debugDescription: "Unknown event type: \(type)"
            )
        }
    }
}
