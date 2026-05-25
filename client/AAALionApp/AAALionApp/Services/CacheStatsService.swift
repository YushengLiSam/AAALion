import Foundation

/// Polls the backend's `GET /cache/stats` endpoint (Sam shipped it in R7e
/// commit a49abdf). Surfaces live cache observability in the Settings sheet:
/// hits / misses / hit-rate %, plus capacity (`size / max_size`) and uptime.
struct CacheStats: Decodable, Equatable {
    let size: Int
    let maxSize: Int
    let ttlSec: Int
    let hits: Int
    let misses: Int
    let expiredMisses: Int
    let evictions: Int
    let totalRequests: Int
    let hitRate: Double
    let uptimeSec: Double

    enum CodingKeys: String, CodingKey {
        case size
        case maxSize = "max_size"
        case ttlSec = "ttl_sec"
        case hits
        case misses
        case expiredMisses = "expired_misses"
        case evictions
        case totalRequests = "total_requests"
        case hitRate = "hit_rate"
        case uptimeSec = "uptime_sec"
    }
}

struct CacheStatsService {
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

    /// Fetch current cache stats. Lightweight; safe to poll every 5-10s.
    func fetch() async throws -> CacheStats {
        let url = baseURL.appendingPathComponent("cache/stats")
        var req = URLRequest(url: url)
        req.timeoutInterval = 5
        do {
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse else {
                throw FetchError.http(-1)
            }
            guard http.statusCode == 200 else { throw FetchError.http(http.statusCode) }
            do {
                return try JSONDecoder().decode(CacheStats.self, from: data)
            } catch {
                throw FetchError.decode(error)
            }
        } catch let e as FetchError {
            throw e
        } catch {
            throw FetchError.transport(error)
        }
    }
}
