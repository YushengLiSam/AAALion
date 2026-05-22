import SwiftUI

struct ProductDetailView: View {
    let product: ProductCard
    @State private var cart = CartStore.shared
    @State private var addedToast = false

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
                Text("¥\(String(format: "%.0f", product.basePrice))")
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundStyle(Color.appAccent)

                Button {
                    cart.add(product)
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
                .padding(.top, 4)

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
}
