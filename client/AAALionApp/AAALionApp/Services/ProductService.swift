import Foundation

struct ProductService {
    var backendURL: URL = Config.backendURL

    func fetch(productId: String) async throws -> ProductCard {
        let url = backendURL.appendingPathComponent("products").appendingPathComponent(productId)
        let (data, _) = try await URLSession.shared.data(from: url)
        return try JSONDecoder().decode(ProductCard.self, from: data)
    }
}
