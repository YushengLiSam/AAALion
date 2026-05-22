import SwiftUI

struct ProductDetailView: View {
    let product: ProductCard

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                AsyncImage(url: product.imageURL) { image in
                    image.resizable().scaledToFit()
                } placeholder: {
                    Color.gray.opacity(0.1).frame(height: 280)
                }

                Text(product.title).font(.title3.bold())
                Text(product.brand).font(.subheadline).foregroundStyle(.secondary)
                Text("¥\(String(format: "%.0f", product.basePrice))").font(.title.bold())

                Text("详情正在加载…")
                    .font(.footnote)
                    .foregroundStyle(.tertiary)
            }
            .padding()
        }
        .navigationTitle(product.brand)
        .navigationBarTitleDisplayMode(.inline)
    }
}
