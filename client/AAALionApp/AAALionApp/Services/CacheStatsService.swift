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

    // R10 — retrieval cache (the hybrid+rerank memo; the dominant
    // first-token win, ~8s→0.3s on repeats). Optional so older backends
    // that don't emit these keys still decode cleanly.
    let retrievalCacheHits: Int?
    let retrievalCacheMisses: Int?
    let retrievalCacheHitRate: Double?
    let retrievalCacheSize: Int?

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
        case retrievalCacheHits = "retrieval_cache_hits"
        case retrievalCacheMisses = "retrieval_cache_misses"
        case retrievalCacheHitRate = "retrieval_cache_hit_rate"
        case retrievalCacheSize = "retrieval_cache_size"
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
            case .decode(let e): return Lf("解析失败:%@", e.localizedDescription)
            case .transport(let e): return e.localizedDescription
            }
        }
    }

    /// Fetch current cache stats. Lightweight; safe to poll every 5-10s.
    /// Timeout = 60s because the iPhone's first call over LAN may hit a
    /// fresh TCP socket and a cold backend response path (model isn't on
    /// the hot path so prewarm doesn't pay forward to /cache/stats). After
    /// the first successful call URLSession reuses the connection and
    /// follow-up polls return in <200ms.
    /// R8.E.3: bumped from 15s to 60s because while a multi-image chat
    /// request is in-flight, the (single-worker) uvicorn serializes ALL
    /// requests behind it — `/cache/stats` can wait up to ~30s on the
    /// FastAPI event loop. 60s gives ample headroom; the fast path
    /// (no concurrent heavy request) still returns sub-second.
    func fetch() async throws -> CacheStats {
        let url = baseURL.appendingPathComponent("cache/stats")
        var req = URLRequest(url: url)
        req.timeoutInterval = 60
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
