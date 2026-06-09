import Foundation

/// R9.B / proposal #11 — client for /groupbuy/* endpoints.
/// Opens a group buy, polls its live state, and lets a "friend" join.
/// Member growth toward the target is simulated server-side from elapsed
/// time; this client just creates + polls.
struct GroupBuyService {
    var baseURL: URL = Config.backendURL

    enum FetchError: LocalizedError {
        case http(Int)
        case decode(Error)
        case transport(Error)
        var errorDescription: String? {
            switch self {
            case .http(let code): return "HTTP \(code)"
            case .decode(let e): return "解析失败 / decode: \(e.localizedDescription)"
            case .transport(let e): return e.localizedDescription
            }
        }
    }

    func createGroup(userId: String, productId: String, targetSize: Int = 3) async throws -> GroupBuy {
        let url = baseURL.appendingPathComponent("groupbuy/create")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        req.httpBody = try JSONSerialization.data(withJSONObject: [
            "user_id": userId, "product_id": productId, "target_size": targetSize,
        ])
        return try await send(req)
    }

    func getGroup(groupId: String) async throws -> GroupBuy {
        let url = baseURL.appendingPathComponent("groupbuy/\(groupId)")
        var req = URLRequest(url: url)
        req.timeoutInterval = 30
        return try await send(req)
    }

    func joinGroup(groupId: String, userId: String) async throws -> GroupBuy {
        let url = baseURL.appendingPathComponent("groupbuy/\(groupId)/join")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        req.httpBody = try JSONSerialization.data(withJSONObject: ["user_id": userId])
        return try await send(req)
    }

    /// R11 — the groups this user opened, for the "我的拼单" list in the
    /// account/profile page. Backend: `GET /groupbuy/active?user_id=`.
    func fetchActive(userId: String) async throws -> [GroupBuy] {
        var comp = URLComponents(url: baseURL.appendingPathComponent("groupbuy/active"),
                                 resolvingAgainstBaseURL: false)
        comp?.queryItems = [URLQueryItem(name: "user_id", value: userId)]
        guard let url = comp?.url else { throw FetchError.http(-1) }
        var req = URLRequest(url: url)
        req.timeoutInterval = 30
        do {
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw FetchError.http(-1) }
            guard http.statusCode == 200 else { throw FetchError.http(http.statusCode) }
            do {
                return try JSONDecoder().decode(ActiveGroupsResponse.self, from: data).groups
            } catch {
                throw FetchError.decode(error)
            }
        } catch let e as FetchError {
            throw e
        } catch {
            throw FetchError.transport(error)
        }
    }

    private func send(_ req: URLRequest) async throws -> GroupBuy {
        do {
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw FetchError.http(-1) }
            guard http.statusCode == 200 else { throw FetchError.http(http.statusCode) }
            do {
                return try JSONDecoder().decode(GroupBuy.self, from: data)
            } catch {
                throw FetchError.decode(error)
            }
        } catch let e as FetchError {
            throw e
        } catch {
            throw FetchError.transport(error)
        }
    }

    private struct ActiveGroupsResponse: Codable {
        let groups: [GroupBuy]
    }
}

/// Live state of a group buy.
struct GroupBuy: Codable, Hashable {
    let groupId: String
    let productId: String
    let product: ProductCard?
    let targetSize: Int
    let filled: Int
    let remaining: Int
    let status: String          // "open" | "complete" | "expired"
    let discountPct: Int
    let groupPriceCNY: Double?
    let secondsLeft: Int
    let members: [GroupMember]

    enum CodingKeys: String, CodingKey {
        case groupId = "group_id"
        case productId = "product_id"
        case product
        case targetSize = "target_size"
        case filled
        case remaining
        case status
        case discountPct = "discount_pct"
        case groupPriceCNY = "group_price_cny"
        case secondsLeft = "seconds_left"
        case members
    }

    var isComplete: Bool { status == "complete" }
}

struct GroupMember: Codable, Hashable, Identifiable {
    let userId: String
    let kind: String            // "opener" | "real" | "simulated"
    let joinedAt: Int?

    var id: String { "\(kind):\(userId)" }

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case kind
        case joinedAt = "joined_at"
    }

    /// Avatar glyph by member kind.
    var glyph: String {
        switch kind {
        case "opener": return "person.crop.circle.fill"
        case "real": return "person.crop.circle.badge.checkmark"
        default: return "person.crop.circle.dashed"   // simulated neighbour
        }
    }

    var label: String {
        switch kind {
        case "opener": return L("你")
        case "real": return L("好友")
        default: return userId   // "邻居N"
        }
    }
}
