import Foundation

/// R14 multi-hop —— 检索链。多跳检索时后端会先发一个 `hop_trace` 事件,
/// 描述"锚点 → 派生约束 → hop2"的推理路径,让可解释性从生成层延伸到检索层。
struct HopTrace: Decodable, Equatable, Hashable {
    struct Anchor: Decodable, Equatable, Hashable {
        let title: String?
        let brand: String?
        let priceCny: Double?
        private enum CodingKeys: String, CodingKey {
            case title, brand
            case priceCny = "price_cny"
        }
    }
    let relation: String
    let label: String?
    let anchor: Anchor?
    let relaxed: Bool?

    /// 面包屑文案:「参照 特步160X ¥999 → 同价位跑鞋」
    var breadcrumb: String {
        let a = anchor?.title ?? ""
        let short = a.count > 14 ? String(a.prefix(14)) + "…" : a
        var s = "参照 \(short)"
        if let p = anchor?.priceCny { s += " ¥\(Int(p))" }
        if let l = label, !l.isEmpty { s += " → \(l)" }
        return s
    }
}

enum ChatDelta: Decodable {
    case text(String)
    case product(ProductCard)
    case cartIntent(String, Int?, Int?)   // (action, ordinal index, quantity for set_quantity)
    case clarify([String])                // R10 #5 — 反问 quick-reply chips
    case error(String)
    /// R9.A.5 — proposal #8 fact-check summary. Carries the counts of
    /// `[目录✓]` and `[推断?]` markers the LLM emitted in this reply.
    case claimSummary(verified: Int, inferred: Int)
    /// R14 multi-hop —— 检索链(锚点 → 派生约束 → hop2)
    case hopTrace(HopTrace)
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
        case relation
        case label
        case anchor
        case relaxed
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
        case "hop_trace":
            let relation = (try? container.decode(String.self, forKey: .relation)) ?? ""
            let label = try? container.decode(String.self, forKey: .label)
            let anchor = try? container.decode(HopTrace.Anchor.self, forKey: .anchor)
            let relaxed = try? container.decode(Bool.self, forKey: .relaxed)
            self = .hopTrace(HopTrace(relation: relation, label: label,
                                      anchor: anchor, relaxed: relaxed))
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
