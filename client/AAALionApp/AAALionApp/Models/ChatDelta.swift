import Foundation

enum ChatDelta: Decodable {
    case text(String)
    case product(ProductCard)
    case cartIntent(String, Int?, Int?)   // (action, ordinal index, quantity for set_quantity)
    case clarify([String])                // R10 #5 — 反问 quick-reply chips
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
        case quantity
        case chips
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
            let quantity = try? container.decode(Int.self, forKey: .quantity)
            self = .cartIntent(action, index, quantity)
        case "clarify":
            let chips = (try? container.decode([String].self, forKey: .chips)) ?? []
            self = .clarify(chips)
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
