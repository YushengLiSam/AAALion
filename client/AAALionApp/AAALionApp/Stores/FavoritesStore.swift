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

    private let userDefaultsKey = "lionpick.favorites"
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
            UserDefaults.standard.set(data, forKey: userDefaultsKey)
        }
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: userDefaultsKey) else { return }
        if let decoded = try? JSONDecoder().decode([String].self, from: data) {
            ids = Set(decoded)
        }
    }
}
