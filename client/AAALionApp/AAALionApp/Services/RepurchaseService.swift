import Foundation

/// HTTP client for the repurchase backend endpoints:
///   * `GET  /repurchase/reminders?user_id=…&limit=N`
///   * `POST /repurchase/purchase`
///
/// Mirrors the CacheStatsService pattern (Shufeng) — sync-style async
/// methods, custom `FetchError`, generous timeout because the open-app
/// poll can race with an in-flight `/chat/stream` request that holds
/// the single uvicorn worker.
///
/// Backend implementation: `server/app/routes/repurchase.py` +
/// `server/app/services/repurchase_db.py`. Contract:
/// `docs/REPURCHASE_PLAN.md` §3.
struct RepurchaseService {
    var baseURL: URL = Config.backendURL

    enum FetchError: LocalizedError {
        case http(Int, String?)
        case decode(Error)
        case transport(Error)

        var errorDescription: String? {
            switch self {
            case .http(let code, let body):
                if let body, !body.isEmpty {
                    return "HTTP \(code): \(body)"
                }
                return "HTTP \(code)"
            case .decode(let e): return Lf("解析失败:%@", e.localizedDescription)
            case .transport(let e): return e.localizedDescription
            }
        }
    }

    // MARK: - Reminders (open-screen + monitoring)

    /// Fetch due reminders. `limit=3` for the open-screen banner;
    /// omit for an unbounded list (monitoring / settings page).
    ///
    /// **Side effect on the server**: the act of fetching marks
    /// returned items as "shown" → they won't reappear for the next
    /// `snooze_hours` (default 24 h). This is intentional dedup; see
    /// REPURCHASE_PLAN §2.4.
    ///
    /// Throws on transport / HTTP / decode failures so the caller can
    /// quietly swallow them (open-screen should fail soft — no red
    /// banner if the network blips on app launch).
    func fetchReminders(userId: String, limit: Int? = 3) async throws -> [RepurchaseReminder] {
        var components = URLComponents(
            url: baseURL.appendingPathComponent("repurchase/reminders"),
            resolvingAgainstBaseURL: false
        )
        var items: [URLQueryItem] = [URLQueryItem(name: "user_id", value: userId)]
        if let limit, limit > 0 {
            items.append(URLQueryItem(name: "limit", value: String(limit)))
        }
        components?.queryItems = items
        guard let url = components?.url else {
            throw FetchError.http(-1, "bad URL")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 60
        req.setValue("application/json", forHTTPHeaderField: "Accept")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw FetchError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw FetchError.http(-1, nil)
        }
        guard http.statusCode == 200 else {
            throw FetchError.http(http.statusCode, String(data: data, encoding: .utf8))
        }
        do {
            return try JSONDecoder().decode(RemindersResponse.self, from: data).reminders
        } catch {
            throw FetchError.decode(error)
        }
    }

    // MARK: - Purchase (close the loop)

    /// Persist a purchase. Called when the user taps "再来一单" on a
    /// reminder banner, or when other purchase-intent flows complete.
    ///
    /// Server-side, `purchased_at` and `cycle_days` default sensibly
    /// when omitted (now() and category default cycle, respectively),
    /// so callers usually only pass `userId` + `productId`.
    @discardableResult
    func recordPurchase(
        userId: String,
        productId: String,
        purchasedAt: Int? = nil,
        cycleDays: Int? = nil
    ) async throws -> PurchaseRecordResponse {
        let url = baseURL.appendingPathComponent("repurchase/purchase")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 60

        var body: [String: Any] = [
            "user_id": userId,
            "product_id": productId,
        ]
        if let purchasedAt { body["purchased_at"] = purchasedAt }
        if let cycleDays { body["cycle_days"] = cycleDays }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw FetchError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw FetchError.http(-1, nil)
        }
        guard http.statusCode == 200 else {
            throw FetchError.http(http.statusCode, String(data: data, encoding: .utf8))
        }
        do {
            return try JSONDecoder().decode(PurchaseRecordResponse.self, from: data)
        } catch {
            throw FetchError.decode(error)
        }
    }
}
