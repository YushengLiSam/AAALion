import Foundation

struct ProductCard: Codable, Hashable, Identifiable {
    let productId: String
    let title: String
    let brand: String
    let basePrice: Double
    let priceCNY: Double?
    let exchangeRate: ExchangeRateQuote?
    let imageURL: URL?
    let provenance: Provenance
    /// R9.A.2 — retrieval signals surfaced for the "why this is recommended"
    /// debug card. Nil for cached or legacy products that don't carry the
    /// new field. Renders only inside ProductDetailView's expandable
    /// section; never on the compact card.
    let retrievalSignals: RetrievalSignals?

    var id: String { productId }

    enum CodingKeys: String, CodingKey {
        case productId = "product_id"
        case title
        case brand
        case basePrice = "base_price"
        case priceCNY = "price_cny"
        case exchangeRate = "exchange_rate"
        case imageURL = "image_url"
        case provenance
        case retrievalSignals = "retrieval_signals"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        productId = try c.decode(String.self, forKey: .productId)
        title = try c.decode(String.self, forKey: .title)
        brand = try c.decode(String.self, forKey: .brand)
        basePrice = try c.decode(Double.self, forKey: .basePrice)
        priceCNY = try c.decodeIfPresent(Double.self, forKey: .priceCNY)
        exchangeRate = try c.decodeIfPresent(ExchangeRateQuote.self, forKey: .exchangeRate)

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

        // Provenance defaults to AI-gen marker so legacy products keep working.
        provenance = (try? c.decode(Provenance.self, forKey: .provenance)) ?? .demo
        retrievalSignals = try? c.decodeIfPresent(RetrievalSignals.self, forKey: .retrievalSignals)
    }

    init(
        productId: String,
        title: String,
        brand: String,
        basePrice: Double,
        priceCNY: Double? = nil,
        exchangeRate: ExchangeRateQuote? = nil,
        imageURL: URL?,
        provenance: Provenance = .demo,
        retrievalSignals: RetrievalSignals? = nil
    ) {
        self.productId = productId
        self.title = title
        self.brand = brand
        self.basePrice = basePrice
        self.priceCNY = priceCNY
        self.exchangeRate = exchangeRate
        self.imageURL = imageURL
        self.provenance = provenance
        self.retrievalSignals = retrievalSignals
    }

    var displayedPrice: Double { priceCNY ?? basePrice }

    var displayedCurrencySymbol: String {
        if priceCNY != nil || provenance.currency.uppercased() == "CNY" { return "¥" }
        return provenance.currencySymbol
    }

    var hasConvertedForeignPrice: Bool {
        provenance.currency.uppercased() != "CNY" && priceCNY != nil
    }

    var originalPriceText: String? {
        guard hasConvertedForeignPrice else { return nil }
        return "\(provenance.currencySymbol)\(String(format: "%.2f", basePrice)) \(provenance.currency.uppercased())"
    }

    var exchangeRateText: String? {
        guard let quote = exchangeRate else { return nil }
        let suffix = quote.stale ? " · 缓存汇率" : ""
        return "1 \(quote.sourceCurrency) = ¥\(String(format: "%.4f", quote.rate)) · \(quote.rateDate)\(suffix)"
    }
}

/// The latest reference rate used for user-facing CNY conversion.
struct ExchangeRateQuote: Codable, Hashable {
    let sourceCurrency: String
    let targetCurrency: String
    let rate: Double
    let rateDate: String
    let provider: String
    let stale: Bool

    enum CodingKeys: String, CodingKey {
        case sourceCurrency = "source_currency"
        case targetCurrency = "target_currency"
        case rate
        case rateDate = "rate_date"
        case provider
        case stale
    }
}

/// Where this product comes from — origin, platform, currency, shipping.
/// Drives the flag badge, original currency, and brand-line in the UI.
struct Provenance: Codable, Hashable {
    let originCountry: String        // "CN", "US", "JP", "DE", "FR"...
    let sourcePlatform: String       // "Tmall", "JD", "Amazon US", "Amazon JP", "AI-gen (demo)"
    let currency: String             // "CNY", "USD", "JPY", "EUR"
    let externalURL: URL?            // real product page when known
    let shippingNote: String?        // "国内现货", "海外直邮", "美亚转运", nil

    enum CodingKeys: String, CodingKey {
        case originCountry = "origin_country"
        case sourcePlatform = "source_platform"
        case currency
        case externalURL = "external_url"
        case shippingNote = "shipping_note"
    }

    init(originCountry: String, sourcePlatform: String, currency: String, externalURL: URL?, shippingNote: String?) {
        self.originCountry = originCountry
        self.sourcePlatform = sourcePlatform
        self.currency = currency
        self.externalURL = externalURL
        self.shippingNote = shippingNote
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        originCountry = (try? c.decode(String.self, forKey: .originCountry)) ?? "CN"
        sourcePlatform = (try? c.decode(String.self, forKey: .sourcePlatform)) ?? "AI-gen (demo)"
        currency = (try? c.decode(String.self, forKey: .currency)) ?? "CNY"
        if let raw = try c.decodeIfPresent(String.self, forKey: .externalURL) {
            externalURL = URL(string: raw)
        } else {
            externalURL = nil
        }
        shippingNote = try c.decodeIfPresent(String.self, forKey: .shippingNote)
    }

    /// Default for legacy/AI-generated products.
    static let demo = Provenance(
        originCountry: "CN",
        sourcePlatform: "AI-gen (demo)",
        currency: "CNY",
        externalURL: nil,
        shippingNote: nil
    )

    // MARK: - UI helpers.

    /// Flag emoji from ISO 2-letter country code. Falls back to 🏷 for
    /// demo / unknown codes. Built from Unicode regional indicator scalars
    /// (e.g. "US" → 🇺🇸 = U+1F1FA + U+1F1F8).
    var flag: String {
        if sourcePlatform == "AI-gen (demo)" { return "🏷" }
        let code = originCountry.uppercased()
        guard code.count == 2 else { return "🏷" }
        let base: UInt32 = 0x1F1E6
        var result = ""
        for scalar in code.unicodeScalars {
            let v = scalar.value
            guard v >= 0x41, v <= 0x5A,
                  let regional = UnicodeScalar(base + (v - 0x41)) else { return "🏷" }
            result.unicodeScalars.append(regional)
        }
        return result.unicodeScalars.count == 2 ? result : "🏷"
    }

    /// Currency symbol. JPY-CNY collision is disambiguated below.
    var currencySymbol: String {
        switch currency.uppercased() {
        case "CNY": return "¥"
        case "USD": return "$"
        case "JPY": return "¥"
        case "EUR": return "€"
        case "GBP": return "£"
        default:    return currency
        }
    }

    /// Localized currency hint ("(美元)" / "(日元)") for non-CNY items, so the
    /// `$` / `¥` symbol isn't misread when the app's primary language is Chinese.
    var currencyHint: String? {
        switch currency.uppercased() {
        case "CNY": return nil
        case "USD": return "美元"
        case "JPY": return "日元"
        case "EUR": return "欧元"
        case "GBP": return "英镑"
        default: return currency
        }
    }

    /// True for products with no real source URL — the "AI-gen (demo)" badge
    /// is rendered in the UI so judges aren't misled.
    var isDemo: Bool { sourcePlatform == "AI-gen (demo)" }

    /// Brand-line prefix shown above the price in card / detail views.
    /// e.g. "Tmall · 雅诗兰黛", "Amazon US · Apple", "演示数据".
    func brandLine(brand: String) -> String {
        if isDemo { return "演示 · \(brand)" }
        return "\(sourcePlatform) · \(brand)"
    }
}

/// R9.A.2 — retrieval-pipeline signals attached to each product card so the
/// iOS UI can render a "why this is recommended" debug expansion. Each
/// field is optional because:
///   - cached replies skip rerank entirely (no rerank_score)
///   - the fast-path (specific-query) also skips rerank
///   - a product matched by only one of dense / BM25 has one rank as nil
/// All values are best-effort and informational — the recommendation
/// itself is the same regardless of whether the user expands the panel.
struct RetrievalSignals: Codable, Hashable {
    /// Reciprocal Rank Fusion combined score (dense + BM25). Higher is better.
    let rrfScore: Double?
    /// Position in the dense (semantic) embedding ranking. 0 = top. Nil if
    /// the product was found only by BM25.
    let denseRank: Int?
    /// Position in the BM25 (keyword) ranking. 0 = top. Nil if dense-only.
    let bm25Rank: Int?
    /// Cross-encoder rerank score (higher = better match for query+product).
    let rerankScore: Double?
    /// Position in the final rerank ranking. 0 = top recommendation.
    let rerankRank: Int?
    /// Which cross-encoder model produced the rerank score.
    let rerankModel: String?

    enum CodingKeys: String, CodingKey {
        case rrfScore = "rrf_score"
        case denseRank = "dense_rank"
        case bm25Rank = "bm25_rank"
        case rerankScore = "rerank_score"
        case rerankRank = "rerank_rank"
        case rerankModel = "rerank_model"
    }

    /// Plain-Chinese one-line summary, e.g. "排名 #1 · 关键词命中 · 语义相似度高".
    /// Empty string if there are no signals to display.
    var humanSummary: String {
        var parts: [String] = []
        if let rerankRank, rerankRank >= 0 {
            parts.append("排名 #\(rerankRank + 1)")
        }
        if let denseRank, denseRank >= 0, denseRank < 5 {
            parts.append("语义相似")
        }
        if let bm25Rank, bm25Rank >= 0, bm25Rank < 5 {
            parts.append("关键词命中")
        }
        if let rerankScore {
            parts.append("精排得分 \(String(format: "%.2f", rerankScore))")
        }
        return parts.joined(separator: " · ")
    }
}
