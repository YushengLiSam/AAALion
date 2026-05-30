import Foundation

enum ChatDelta: Decodable {
    case text(String)
    case product(ProductCard)
    case cartIntent(String, Int?)   // ("add"|"checkout"|"remove", ordinal index for remove)
    case error(String)
    /// R9.A.5 — proposal #8 fact-check summary. Carries the counts of
    /// `[目录✓]` and `[推断?]` markers the LLM emitted in this reply.
    case claimSummary(verified: Int, inferred: Int)
    case done

    private enum CodingKeys: String, CodingKey {
        case type
        case text
        case product
        case action
        case index
        case message
        case verified
        case inferred
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
            let index = try? container.decode(Int.self, forKey: .index)
            self = .cartIntent(action, index)
        case "error":
            self = .error(try container.decode(String.self, forKey: .message))
        case "claim_summary":
            let v = (try? container.decode(Int.self, forKey: .verified)) ?? 0
            let i = (try? container.decode(Int.self, forKey: .inferred)) ?? 0
            self = .claimSummary(verified: v, inferred: i)
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
