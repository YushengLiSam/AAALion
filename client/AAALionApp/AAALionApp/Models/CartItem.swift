import Foundation

struct CartItem: Codable, Hashable, Identifiable {
    let productId: String
    let title: String
    let brand: String
    let unitPrice: Double
    let imageURLString: String?
    let provenance: Provenance
    var quantity: Int

    var id: String { productId }

    var lineTotal: Double { unitPrice * Double(quantity) }

    var imageURL: URL? {
        guard let s = imageURLString else { return nil }
        if let u = URL(string: s), u.scheme != nil { return u }
        return URL(string: s, relativeTo: Config.backendURL)?.absoluteURL
    }

    enum CodingKeys: String, CodingKey {
        case productId, title, brand, unitPrice, imageURLString, provenance, quantity
    }

    init(
        productId: String,
        title: String,
        brand: String,
        unitPrice: Double,
        imageURLString: String?,
        provenance: Provenance = .demo,
        quantity: Int = 1
    ) {
        self.productId = productId
        self.title = title
        self.brand = brand
        self.unitPrice = unitPrice
        self.imageURLString = imageURLString
        self.provenance = provenance
        self.quantity = quantity
    }

    init(from product: ProductCard, quantity: Int = 1) {
        self.productId = product.productId
        self.title = product.title
        self.brand = product.brand
        self.unitPrice = product.basePrice
        self.imageURLString = product.imageURL?.absoluteString
        self.provenance = product.provenance
        self.quantity = quantity
    }

    /// Custom decoder for backward-compat: pre-Round-6 carts persisted to
    /// UserDefaults didn't have a `provenance` field; treat them as demo.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        productId = try c.decode(String.self, forKey: .productId)
        title = try c.decode(String.self, forKey: .title)
        brand = try c.decode(String.self, forKey: .brand)
        unitPrice = try c.decode(Double.self, forKey: .unitPrice)
        imageURLString = try c.decodeIfPresent(String.self, forKey: .imageURLString)
        provenance = (try? c.decode(Provenance.self, forKey: .provenance)) ?? .demo
        quantity = try c.decode(Int.self, forKey: .quantity)
    }
}
