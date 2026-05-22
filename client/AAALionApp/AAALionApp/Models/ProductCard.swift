import Foundation

struct ProductCard: Codable, Hashable, Identifiable {
    let productId: String
    let title: String
    let brand: String
    let basePrice: Double
    let imageURL: URL?

    var id: String { productId }

    enum CodingKeys: String, CodingKey {
        case productId = "product_id"
        case title
        case brand
        case basePrice = "base_price"
        case imageURL = "image_url"
    }
}
