import SwiftUI

/// R12.fix (@Sam) — dedicated 我的收藏 page, surfaced from the chat top-bar ❤️
/// so favorites are a first-class entry instead of being buried inside the
/// profile page. Resolves the favorited product IDs to cards (ProductService)
/// and reuses ProductCardView; tapping ❤️ on a card un-favorites it and it
/// drops out live (the list re-filters against the store).
struct FavoritesView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var favorites = FavoritesStore.shared
    @State private var products: [ProductCard] = []
    @State private var loading = false

    private let columns = [GridItem(.adaptive(minimum: 150), spacing: 12)]

    var body: some View {
        NavigationStack {
            ZStack {
                Color.appBackground.ignoresSafeArea()
                // Re-filter against the live store so un-favoriting removes
                // the card immediately, without a reload.
                let shown = products.filter { favorites.isFavorite($0.productId) }
                if loading && shown.isEmpty {
                    ProgressView("加载中…").tint(Color.appAccent)
                } else if shown.isEmpty {
                    ContentUnavailableView {
                        Label("还没有收藏", systemImage: "heart")
                    } description: {
                        Text("在商品卡片或详情页点 ❤️ 收藏,这里随时回看。")
                    }
                } else {
                    ScrollView {
                        LazyVGrid(columns: columns, alignment: .leading, spacing: 12) {
                            ForEach(shown) { product in
                                ProductCardView(product: product)
                            }
                        }
                        .padding()
                    }
                }
            }
            .navigationTitle("我的收藏 / Favorites")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } }
            }
            .task { await load() }
            .refreshable { await load() }
        }
    }

    @MainActor
    private func load() async {
        let ids = Array(favorites.ids)
        guard !ids.isEmpty else { products = []; return }
        loading = true
        defer { loading = false }
        var out: [ProductCard] = []
        await withTaskGroup(of: ProductCard?.self) { group in
            let svc = ProductService()
            for id in ids {
                group.addTask { try? await svc.fetch(productId: id) }
            }
            for await product in group {
                if let product { out.append(product) }
            }
        }
        products = out.sorted { $0.title < $1.title }
    }
}
