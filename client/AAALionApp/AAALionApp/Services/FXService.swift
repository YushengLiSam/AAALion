import Foundation

/// On-demand FX rate fetcher with an in-memory cache. Used by
/// CheckoutView when the user explicitly picks a settlement currency —
/// that picker tap IS the language-context Tujie's chat path normally
/// relies on, so converting at this moment is principled, not a hack.
///
/// Same shape / failure mode as CacheStatsService:
///   * Backend route: `GET /currency/rate?source=X&target=Y`
///   * 60s timeout because in-flight chat requests can hold the single
///     uvicorn worker (per R8.E.3).
///   * Returns the cached rate forever on success — Frankfurter mid-
///     market reference is stable enough that for a single checkout
///     session this is fine.
actor FXService {
    static let shared = FXService()

    private var cache: [String: Double] = [:]

    struct Quote: Decodable {
        let sourceCurrency: String
        let targetCurrency: String
        let rate: Double
        let rateDate: String
        let provider: String
        let stale: Bool

        enum CodingKeys: String, CodingKey {
            case sourceCurrency = "source_currency"
            case targetCurrency = "target_currency"
            case rate
            case rateDate = "rate_date"
            case provider
            case stale
        }
    }

    enum FXError: LocalizedError {
        case http(Int, String?)
        case transport(Error)
        case decode(Error)

        var errorDescription: String? {
            switch self {
            case .http(let code, let body):
                if let body, !body.isEmpty { return "HTTP \(code): \(body)" }
                return "HTTP \(code)"
            case .transport(let e): return e.localizedDescription
            case .decode(let e): return "FX decode failed: \(e.localizedDescription)"
            }
        }
    }

    /// Returns rate such that `source_amount * rate = target_amount`.
    /// Identity pair returns 1.0 without network. On any failure throws —
    /// callers should catch and fall back to a user-facing "汇率不可用".
    func rate(from source: String, to target: String) async throws -> Double {
        let src = source.uppercased()
        let tgt = target.uppercased()
        if src == tgt { return 1.0 }

        let key = "\(src)->\(tgt)"
        if let cached = cache[key] { return cached }

        var components = URLComponents(
            url: Config.backendURL.appendingPathComponent("currency/rate"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [
            URLQueryItem(name: "source", value: src),
            URLQueryItem(name: "target", value: tgt),
        ]
        guard let url = components?.url else {
            throw FXError.http(-1, "bad URL")
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 60
        req.setValue("application/json", forHTTPHeaderField: "Accept")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw FXError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw FXError.http(-1, nil)
        }
        guard http.statusCode == 200 else {
            throw FXError.http(http.statusCode, String(data: data, encoding: .utf8))
        }
        let quote: Quote
        do {
            quote = try JSONDecoder().decode(Quote.self, from: data)
        } catch {
            throw FXError.decode(error)
        }
        cache[key] = quote.rate
        return quote.rate
    }

    /// Test / settings-toggle hook — wipe the cache when the user
    /// switches backend URL or wants a forced refresh.
    func clear() {
        cache.removeAll()
    }
}
