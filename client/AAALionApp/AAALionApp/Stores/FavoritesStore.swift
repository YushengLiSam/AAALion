import Foundation
import Observation

/// R10 #4.4⭐⭐⭐ — observable wishlist/收藏 store. Mirrors CartStore:
/// a singleton persisted to UserDefaults as a JSON-encoded set of
/// product IDs. The heart toggle on a product card reads/writes here,
/// so the favorited state survives relaunches and is consistent across
/// every place the same product appears (chat cards, detail, cart).
@Observable
final class FavoritesStore {
    static let shared = FavoritesStore()

    // R11 — favorites are PER-ACCOUNT, keyed by DeviceIdentity.userId, so
    // they follow the user across sign-in / sign-out / account switch.
    private static let keyPrefix = "lionpick.favorites."
    private func storageKey(for userId: String) -> String { Self.keyPrefix + userId }
    private var currentKey: String { storageKey(for: DeviceIdentity.userId) }
    private(set) var ids: Set<String> = []

    init() {
        load()
    }

    var count: Int { ids.count }

    func isFavorite(_ productId: String) -> Bool {
        ids.contains(productId)
    }

    /// Toggle membership; returns the new state (true = now favorited).
    /// The caller drives the bounce animation off this return value.
    @discardableResult
    func toggle(_ productId: String) -> Bool {
        let nowFavorited: Bool
        if ids.contains(productId) {
            ids.remove(productId)
            nowFavorited = false
        } else {
            ids.insert(productId)
            nowFavorited = true
        }
        persist()
        return nowFavorited
    }

    private func persist() {
        if let data = try? JSONEncoder().encode(Array(ids)) {
            UserDefaults.standard.set(data, forKey: currentKey)
        }
    }

    private func load() {
        // Resets to [] when the current user has none, so switching accounts
        // doesn't leak the previous account's favorites.
        let decoded = UserDefaults.standard.data(forKey: currentKey)
            .flatMap { try? JSONDecoder().decode([String].self, from: $0) } ?? []
        ids = Set(decoded)
    }

    /// R11 — reload favorites for the current user (call on account change).
    func reloadForCurrentUser() { load() }

    /// R11 — on first sign-in, carry the anonymous favorites into the account
    /// (move, not copy) when the account has none yet. Then reload.
    func handleSignIn(previousAnon: String, account: String) {
        if previousAnon != account {
            let anonKey = storageKey(for: previousAnon)
            let accountKey = storageKey(for: account)
            if UserDefaults.standard.data(forKey: accountKey) == nil,
               let anonData = UserDefaults.standard.data(forKey: anonKey) {
                UserDefaults.standard.set(anonData, forKey: accountKey)
                UserDefaults.standard.removeObject(forKey: anonKey)
            }
        }
        load()
    }
}
