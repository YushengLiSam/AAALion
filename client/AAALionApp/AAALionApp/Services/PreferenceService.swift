import Foundation

/// R9.B / proposal #12 — client for the /preferences/* endpoints.
/// Records 👍/👎 feedback and reads / wipes the user's preference table.
/// Keyed by the anonymous DeviceIdentity.userId (IDFV).
struct PreferenceService {
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

    /// signal: +1 like, -1 dislike.
    func sendFeedback(userId: String, productId: String, signal: Int) async throws {
        let url = baseURL.appendingPathComponent("preferences/feedback")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        req.httpBody = try JSONSerialization.data(withJSONObject: [
            "user_id": userId,
            "product_id": productId,
            "signal": signal,
        ])
        let (_, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw FetchError.http(-1) }
        guard http.statusCode == 200 else { throw FetchError.http(http.statusCode) }
    }

    /// Fetch the user's current preference rows (for the "我的偏好" view).
    func fetchPreferences(userId: String) async throws -> [PreferenceItem] {
        var comp = URLComponents(url: baseURL.appendingPathComponent("preferences"),
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
                return try JSONDecoder().decode(PreferencesResponse.self, from: data).items
            } catch {
                throw FetchError.decode(error)
            }
        } catch let e as FetchError {
            throw e
        } catch {
            throw FetchError.transport(error)
        }
    }

    /// Wipe all preferences for the user ("我变了，重新学").
    func resetPreferences(userId: String) async throws {
        var comp = URLComponents(url: baseURL.appendingPathComponent("preferences"),
                                 resolvingAgainstBaseURL: false)
        comp?.queryItems = [URLQueryItem(name: "user_id", value: userId)]
        guard let url = comp?.url else { throw FetchError.http(-1) }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        req.timeoutInterval = 30
        let (_, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw FetchError.http(-1) }
        guard http.statusCode == 200 else { throw FetchError.http(http.statusCode) }
    }

    private struct PreferencesResponse: Codable {
        let items: [PreferenceItem]
    }
}

/// One row of the user's preference table.
struct PreferenceItem: Codable, Identifiable, Hashable {
    let dimension: String   // "brand" | "category" | "sub_category"
    let value: String
    let score: Double
    let updatedAt: Int

    var id: String { "\(dimension):\(value)" }

    enum CodingKeys: String, CodingKey {
        case dimension, value, score
        case updatedAt = "updated_at"
    }

    /// "品牌" / "类目" / "子类" — Chinese label for the dimension.
    var dimensionLabel: String {
        switch dimension {
        case "brand": return "品牌"
        case "category": return "类目"
        case "sub_category": return "子类"
        default: return dimension
        }
    }

    /// 👍 liked (score > 0) vs 👎 disliked (score < 0).
    var isLiked: Bool { score > 0 }
}
