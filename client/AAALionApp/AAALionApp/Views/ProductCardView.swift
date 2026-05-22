import SwiftUI

struct ProductCardView: View {
    let product: ProductCard

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            AsyncImage(url: product.imageURL) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().scaledToFill()
                case .failure:
                    Color.appAccentMuted.opacity(0.3)
                        .overlay(Image(systemName: "photo")
                            .foregroundStyle(Color.appTextSecondary))
                case .empty:
                    Color.appAccentMuted.opacity(0.2)
                        .overlay(ProgressView().tint(Color.appAccent))
                @unknown default:
                    Color.appAccentMuted.opacity(0.2)
                }
            }
            .frame(width: 130, height: 130)
            .clipShape(RoundedRectangle(cornerRadius: 14))

            Text(product.title)
                .font(.appCaption)
                .foregroundStyle(Color.appTextPrimary)
                .lineLimit(2)
                .frame(width: 130, alignment: .leading)

            Text("¥\(String(format: "%.0f", product.basePrice))")
                .font(.system(size: 15, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.appAccent)
        }
        .padding(Theme.cardPadding)
        .background(Color.appSurfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: Theme.cardCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cardCornerRadius)
                .stroke(Color.appBorder, lineWidth: 0.5)
        )
        .shadow(color: Color.black.opacity(0.04), radius: 6, x: 0, y: 2)
    }
}
