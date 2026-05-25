import Foundation
import Observation

/// Observable cart store, persisted to UserDefaults as JSON-encoded list.
/// Survives app relaunches. Cleared by `clear()` or explicit re-init.
@Observable
final class CartStore {
    static let shared = CartStore()

    private let userDefaultsKey = "lionpick.cart"
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

    func clear() {
        items = []
        persist()
    }

    private func persist() {
        if let data = try? JSONEncoder().encode(items) {
            UserDefaults.standard.set(data, forKey: userDefaultsKey)
        }
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: userDefaultsKey) else { return }
        if let decoded = try? JSONDecoder().decode([CartItem].self, from: data) {
            items = decoded
        }
    }
}
