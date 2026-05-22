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

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        productId = try c.decode(String.self, forKey: .productId)
        title = try c.decode(String.self, forKey: .title)
        brand = try c.decode(String.self, forKey: .brand)
        basePrice = try c.decode(Double.self, forKey: .basePrice)

        // Backend may send the image URL as either a relative path
        // (e.g. "/static/1_美妆护肤/images/p_beauty_001_live.jpg") or
        // an absolute URL. Resolve relative paths against the configured
        // backend URL so AsyncImage can actually fetch them.
        if let raw = try c.decodeIfPresent(String.self, forKey: .imageURL) {
            if let absolute = URL(string: raw), absolute.scheme != nil {
                imageURL = absolute
            } else {
                imageURL = URL(string: raw, relativeTo: Config.backendURL)?.absoluteURL
            }
        } else {
            imageURL = nil
        }
    }

    init(productId: String, title: String, brand: String, basePrice: Double, imageURL: URL?) {
        self.productId = productId
        self.title = title
        self.brand = brand
        self.basePrice = basePrice
        self.imageURL = imageURL
    }
}
