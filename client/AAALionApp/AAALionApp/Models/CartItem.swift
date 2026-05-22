import Foundation

struct CartItem: Codable, Hashable, Identifiable {
    let productId: String
    let title: String
    let brand: String
    let unitPrice: Double
    let imageURLString: String?
    var quantity: Int

    var id: String { productId }

    var lineTotal: Double { unitPrice * Double(quantity) }

    var imageURL: URL? {
        guard let s = imageURLString else { return nil }
        if let u = URL(string: s), u.scheme != nil { return u }
        return URL(string: s, relativeTo: Config.backendURL)?.absoluteURL
    }

    init(productId: String, title: String, brand: String, unitPrice: Double, imageURLString: String?, quantity: Int = 1) {
        self.productId = productId
        self.title = title
        self.brand = brand
        self.unitPrice = unitPrice
        self.imageURLString = imageURLString
        self.quantity = quantity
    }

    init(from product: ProductCard, quantity: Int = 1) {
        self.productId = product.productId
        self.title = product.title
        self.brand = product.brand
        self.unitPrice = product.basePrice
        self.imageURLString = product.imageURL?.absoluteString
        self.quantity = quantity
    }
}
