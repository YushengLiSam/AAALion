import SwiftUI

struct ProductCardView: View {
    let product: ProductCard

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            AsyncImage(url: product.imageURL) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().scaledToFill()
                case .failure:
                    Color.gray.opacity(0.1)
                        .overlay(Image(systemName: "photo").foregroundStyle(.tertiary))
                case .empty:
                    Color.gray.opacity(0.1)
                @unknown default:
                    Color.gray.opacity(0.1)
                }
            }
            .frame(width: 120, height: 120)
            .clipShape(RoundedRectangle(cornerRadius: 12))

            Text(product.title)
                .font(.caption)
                .lineLimit(2)
                .frame(width: 120, alignment: .leading)

            Text("¥\(String(format: "%.0f", product.basePrice))")
                .font(.subheadline.bold())
        }
        .padding(8)
        .background(Color(.secondarySystemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}
