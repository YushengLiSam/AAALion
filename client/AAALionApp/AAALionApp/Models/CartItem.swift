import Foundation

struct CartItem: Codable, Hashable, Identifiable {
    let productId: String
    let title: String
    let brand: String
    let unitPrice: Double
    let unitPriceCNY: Double?
    let exchangeRate: ExchangeRateQuote?
    let imageURLString: String?
    let provenance: Provenance
    var quantity: Int

    var id: String { productId }

    var lineTotal: Double { unitPrice * Double(quantity) }
    var lineTotalCNY: Double? {
        if let unitPriceCNY { return unitPriceCNY * Double(quantity) }
        if provenance.currency.uppercased() == "CNY" { return lineTotal }
        return nil
    }
    var displayedUnitPrice: Double { unitPriceCNY ?? unitPrice }
    var displayedCurrencySymbol: String {
        if unitPriceCNY != nil || provenance.currency.uppercased() == "CNY" { return "¥" }
        return provenance.currencySymbol
    }
    var originalPriceText: String? {
        guard unitPriceCNY != nil, provenance.currency.uppercased() != "CNY" else { return nil }
        return "\(provenance.currencySymbol)\(String(format: "%.2f", unitPrice)) \(provenance.currency.uppercased())"
    }

    var imageURL: URL? {
        guard let s = imageURLString else { return nil }
        if let u = URL(string: s), u.scheme != nil { return u }
        return URL(string: s, relativeTo: Config.backendURL)?.absoluteURL
    }

    enum CodingKeys: String, CodingKey {
        case productId, title, brand, unitPrice, unitPriceCNY, exchangeRate, imageURLString, provenance, quantity
    }

    init(
        productId: String,
        title: String,
        brand: String,
        unitPrice: Double,
        unitPriceCNY: Double? = nil,
        exchangeRate: ExchangeRateQuote? = nil,
        imageURLString: String?,
        provenance: Provenance = .demo,
        quantity: Int = 1
    ) {
        self.productId = productId
        self.title = title
        self.brand = brand
        self.unitPrice = unitPrice
        self.unitPriceCNY = unitPriceCNY
        self.exchangeRate = exchangeRate
        self.imageURLString = imageURLString
        self.provenance = provenance
        self.quantity = quantity
    }

    init(from product: ProductCard, quantity: Int = 1) {
        self.productId = product.productId
        self.title = product.title
        self.brand = product.brand
        self.unitPrice = product.basePrice
        self.unitPriceCNY = product.priceCNY
        self.exchangeRate = product.exchangeRate
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
        unitPriceCNY = try c.decodeIfPresent(Double.self, forKey: .unitPriceCNY)
        exchangeRate = try c.decodeIfPresent(ExchangeRateQuote.self, forKey: .exchangeRate)
        imageURLString = try c.decodeIfPresent(String.self, forKey: .imageURLString)
        provenance = (try? c.decode(Provenance.self, forKey: .provenance)) ?? .demo
        quantity = try c.decode(Int.self, forKey: .quantity)
    }
}
