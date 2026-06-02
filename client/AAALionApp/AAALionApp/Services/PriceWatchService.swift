import Foundation

/// R9.A.4 — minimal HTTP client for the /price_watch/* endpoints.
/// Mirror of RepurchaseService. Adds a watch when the user taps
/// "提醒我降价" on a product detail; reads alerts on app launch.
struct PriceWatchService {
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

    /// Start watching a product at a target CNY price. Idempotent — the
    /// backend upserts on (user_id, product_id) so calling twice updates
    /// the target instead of creating duplicates.
    func startWatch(userId: String, productId: String, targetPriceCNY: Double) async throws -> WatchResponse {
        let url = baseURL.appendingPathComponent("price_watch/watch")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        let payload: [String: Any] = [
            "user_id": userId,
            "product_id": productId,
            "target_price_cny": targetPriceCNY,
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)
        do {
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw FetchError.http(-1) }
            guard http.statusCode == 200 else { throw FetchError.http(http.statusCode) }
            do {
                return try JSONDecoder().decode(WatchResponse.self, from: data)
            } catch {
                throw FetchError.decode(error)
            }
        } catch let e as FetchError {
            throw e
        } catch {
            throw FetchError.transport(error)
        }
    }

    /// Fetch all due price alerts for the user. Empty array if nothing
    /// is due (the iOS app hides the banner in that case).
    func fetchAlerts(userId: String, limit: Int? = nil) async throws -> [PriceAlert] {
        var components = URLComponents(url: baseURL.appendingPathComponent("price_watch/alerts"),
                                       resolvingAgainstBaseURL: false)
        var items = [URLQueryItem(name: "user_id", value: userId)]
        if let limit { items.append(URLQueryItem(name: "limit", value: String(limit))) }
        components?.queryItems = items
        guard let url = components?.url else { throw FetchError.http(-1) }
        var req = URLRequest(url: url)
        req.timeoutInterval = 30
        do {
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw FetchError.http(-1) }
            guard http.statusCode == 200 else { throw FetchError.http(http.statusCode) }
            do {
                return try JSONDecoder().decode(AlertsResponse.self, from: data).alerts
            } catch {
                throw FetchError.decode(error)
            }
        } catch let e as FetchError {
            throw e
        } catch {
            throw FetchError.transport(error)
        }
    }

    /// Stop watching a product.
    func removeWatch(userId: String, productId: String) async throws {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("price_watch/watch/\(productId)"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [URLQueryItem(name: "user_id", value: userId)]
        guard let url = components?.url else { throw FetchError.http(-1) }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        req.timeoutInterval = 30
        let (_, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw FetchError.http(-1) }
        guard http.statusCode == 200 else { throw FetchError.http(http.statusCode) }
    }

    // MARK: - Response shapes.

    struct WatchResponse: Codable {
        let id: Int
        let targetPriceCNY: Double

        enum CodingKeys: String, CodingKey {
            case id
            case targetPriceCNY = "target_price_cny"
        }
    }

    private struct AlertsResponse: Codable {
        let alerts: [PriceAlert]
    }
}

/// A single due price-watch alert. Includes the full embedded product so
/// the iOS banner can render the same way as a chat product card.
struct PriceAlert: Codable, Identifiable, Hashable {
    let watchId: Int
    let product: ProductCard
    let currentPriceCNY: Double
    let targetPriceCNY: Double
    let savingsCNY: Double
    let createdAt: Int

    var id: Int { watchId }

    enum CodingKeys: String, CodingKey {
        case watchId = "watch_id"
        case product
        case currentPriceCNY = "current_price_cny"
        case targetPriceCNY = "target_price_cny"
        case savingsCNY = "savings_cny"
        case createdAt = "created_at"
    }
}
