import SwiftUI

struct ProductDetailView: View {
    let product: ProductCard
    @State private var cart = CartStore.shared
    @State private var addedToast = false
    @Environment(\.openURL) private var openURL

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                AsyncImage(url: product.imageURL) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFit()
                    case .failure:
                        Color.appAccentMuted.opacity(0.3)
                            .overlay(Image(systemName: "photo").foregroundStyle(Color.appTextSecondary))
                            .frame(height: 280)
                    case .empty:
                        Color.appAccentMuted.opacity(0.2)
                            .overlay(ProgressView().tint(Color.appAccent))
                            .frame(height: 280)
                    @unknown default:
                        Color.appAccentMuted.opacity(0.2).frame(height: 280)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 16))

                Text(product.title)
                    .font(.appTitle)
                    .foregroundStyle(Color.appTextPrimary)
                Text(product.brand)
                    .font(.appBody)
                    .foregroundStyle(Color.appTextSecondary)

                priceBlock
                provenanceCard

                addToCartButton

                if let url = product.provenance.externalURL {
                    storeLinkButton(url: url)
                } else {
                    disabledStoreLink
                }

                if addedToast {
                    Label("已加入购物车 / Added to cart", systemImage: "checkmark.circle.fill")
                        .font(.appCaption)
                        .foregroundStyle(Color.appAccent)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            .padding()
        }
        .background(Color.appBackground.ignoresSafeArea())
        .navigationTitle(product.brand)
        .navigationBarTitleDisplayMode(.inline)
    }

    private var priceBlock: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(product.displayedCurrencySymbol)\(String(format: "%.2f", product.displayedPrice))")
                .font(.system(size: 28, weight: .bold, design: .rounded))
                .foregroundStyle(Color.appAccent)
            if let originalPrice = product.originalPriceText {
                Text("原价 \(originalPrice)")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            } else if product.priceCNY == nil, let hint = product.provenance.currencyHint {
                Text("\(hint)原价，人民币汇率暂不可用")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
    }

    /// Grouped section: origin / platform / currency / shipping. Hidden for demo items.
    private var provenanceCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(product.provenance.flag)
                Text("来源 / Source")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            }
            .padding(.bottom, 2)
            row("origin",  "产地",        product.provenance.originCountry)
            row("storefront",  "平台",     product.provenance.sourcePlatform)
            row("creditcard.fill", "原始币种", "\(product.provenance.currency) (\(product.provenance.currencySymbol))")
            if let quote = product.exchangeRate, let text = product.exchangeRateText {
                row("arrow.triangle.2.circlepath", "参考汇率", text)
                row("calendar", "汇率来源", quote.provider)
            }
            if let ship = product.provenance.shippingNote {
                row("shippingbox.fill", "配送", ship)
            }
            if product.provenance.isDemo {
                Text("⚠️ 此商品为演示数据。外部链接为商品标题搜索。")
                    .font(.appCaption)
                    .foregroundStyle(.orange)
                    .padding(.top, 4)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.appSurface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.appBorder, lineWidth: 0.5)
        )
    }

    private func row(_ icon: String, _ label: String, _ value: String) -> some View {
        HStack {
            Image(systemName: icon)
                .foregroundStyle(Color.appTextSecondary)
                .frame(width: 18)
            Text(label)
                .font(.appCaption)
                .foregroundStyle(Color.appTextSecondary)
                .frame(width: 60, alignment: .leading)
            Text(value)
                .font(.appCaption)
                .foregroundStyle(Color.appTextPrimary)
            Spacer()
        }
    }

    private var addToCartButton: some View {
        Button {
            cart.add(product)
            let gen = UINotificationFeedbackGenerator()
            gen.notificationOccurred(.success)
            withAnimation { addedToast = true }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) {
                withAnimation { addedToast = false }
            }
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "cart.badge.plus")
                Text("加入购物车 / Add to Cart")
            }
            .font(.appBody.bold())
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(Color.appAccent)
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
    }

    private func storeLinkButton(url: URL) -> some View {
        Button {
            openURL(url)
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "arrow.up.right.square")
                Text("去原页 / View on \(product.provenance.sourcePlatform)")
            }
            .font(.appBody.bold())
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(Color.appAccent, lineWidth: 1.5)
            )
            .foregroundStyle(Color.appAccent)
        }
    }

    private var disabledStoreLink: some View {
        HStack(spacing: 8) {
            Image(systemName: "link.badge.plus")
            Text("演示商品 · 无原页链接")
        }
        .font(.appCaption)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Color.appAccentMuted.opacity(0.3))
        .foregroundStyle(Color.appTextSecondary)
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}
