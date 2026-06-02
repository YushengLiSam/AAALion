import Foundation
import Observation

/// Observable cart store, persisted to UserDefaults as JSON-encoded list.
/// Survives app relaunches. Cleared by `clear()` or explicit re-init.
@Observable
final class CartStore {
    static let shared = CartStore()

    // R11 — the cart is PER-ACCOUNT: keyed by DeviceIdentity.userId (the
    // signed-in account id, or the anonymous device id when logged out) so
    // the cart follows the user across sign-in / sign-out / account switch.
    private static let keyPrefix = "lionpick.cart."
    private func storageKey(for userId: String) -> String { Self.keyPrefix + userId }
    private var currentKey: String { storageKey(for: DeviceIdentity.userId) }
    private(set) var items: [CartItem] = []

    init() {
        load()
    }

    var totalQuantity: Int { items.reduce(0) { $0 + $1.quantity } }
    var grandTotal: Double { items.reduce(0) { $0 + $1.lineTotal } }
    var isEmpty: Bool { items.isEmpty }

    func add(_ product: ProductCard, quantity: Int = 1) {
        if let idx = items.firstIndex(where: { $0.productId == product.productId }) {
            items[idx] = CartItem(from: product, quantity: items[idx].quantity + quantity)
        } else {
            items.append(CartItem(from: product, quantity: quantity))
        }
        persist()
    }

    func remove(productId: String) {
        items.removeAll { $0.productId == productId }
        persist()
    }

    func increment(productId: String) {
        if let idx = items.firstIndex(where: { $0.productId == productId }) {
            items[idx].quantity += 1
            persist()
        }
    }

    func decrement(productId: String) {
        if let idx = items.firstIndex(where: { $0.productId == productId }) {
            items[idx].quantity -= 1
            if items[idx].quantity <= 0 {
                items.remove(at: idx)
            }
            persist()
        }
    }

    /// R10 #4.1⭐⭐ — set an exact quantity (conversational "把数量改成2").
    /// Clamps to ≥1; a request for 0 or less removes the line instead.
    func setQuantity(productId: String, quantity: Int) {
        guard let idx = items.firstIndex(where: { $0.productId == productId }) else { return }
        if quantity <= 0 {
            items.remove(at: idx)
        } else {
            items[idx].quantity = quantity
        }
        persist()
    }

    func clear() {
        items = []
        persist()
    }

    private func persist() {
        if let data = try? JSONEncoder().encode(items) {
            UserDefaults.standard.set(data, forKey: currentKey)
        }
    }

    private func load() {
        // Resets to [] when the current user has no saved cart — important so
        // switching to a different account clears the previous one's items.
        items = UserDefaults.standard.data(forKey: currentKey)
            .flatMap { try? JSONDecoder().decode([CartItem].self, from: $0) } ?? []
    }

    /// R11 — reload the cart for whoever is the current user. Call when the
    /// signed-in account changes so the cart follows the user.
    func reloadForCurrentUser() { load() }

    /// R11 — on first sign-in, carry the anonymous cart into the account
    /// (move, not copy) when the account has no cart yet — mirrors the
    /// backend anonymous-data migration. Then reload to the account's cart.
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
