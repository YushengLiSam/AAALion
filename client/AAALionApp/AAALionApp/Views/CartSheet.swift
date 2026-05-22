import SwiftUI

struct CartSheet: View {
    @Bindable var cart: CartStore
    @Environment(\.dismiss) private var dismiss
    @State private var showCheckout = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if cart.isEmpty {
                    emptyState
                } else {
                    List {
                        ForEach(cart.items) { item in
                            row(for: item)
                        }
                        .onDelete { indexes in
                            for i in indexes {
                                cart.remove(productId: cart.items[i].productId)
                            }
                        }
                    }
                    .listStyle(.plain)
                    summary
                }
            }
            .background(Color.appBackground)
            .navigationTitle("购物车 / Cart")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭 / Close") { dismiss() }
                }
                if !cart.isEmpty {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("清空 / Clear") { cart.clear() }
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationDestination(isPresented: $showCheckout) {
                CheckoutView(cart: cart)
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "cart")
                .font(.system(size: 60))
                .foregroundStyle(Color.appTextSecondary)
            Text("购物车是空的")
                .font(.appTitle)
                .foregroundStyle(Color.appTextPrimary)
            Text("先去聊天里挑几款商品吧 ✨")
                .font(.appBody)
                .foregroundStyle(Color.appTextSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func row(for item: CartItem) -> some View {
        HStack(spacing: 12) {
            AsyncImage(url: item.imageURL) { phase in
                switch phase {
                case .success(let image): image.resizable().scaledToFill()
                case .failure:
                    Color.appAccentMuted.opacity(0.3)
                        .overlay(Image(systemName: "photo").foregroundStyle(Color.appTextSecondary))
                case .empty:
                    Color.appAccentMuted.opacity(0.2)
                @unknown default:
                    Color.appAccentMuted.opacity(0.2)
                }
            }
            .frame(width: 64, height: 64)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            VStack(alignment: .leading, spacing: 4) {
                Text(item.title)
                    .font(.appBody)
                    .lineLimit(2)
                Text(item.brand)
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
                Text("¥\(String(format: "%.0f", item.unitPrice))")
                    .font(.system(size: 14, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.appAccent)
            }
            Spacer()
            HStack(spacing: 8) {
                Button {
                    cart.decrement(productId: item.productId)
                } label: {
                    Image(systemName: "minus.circle.fill")
                        .foregroundStyle(Color.appAccent)
                        .font(.title3)
                }
                Text("\(item.quantity)")
                    .frame(minWidth: 20)
                    .font(.appBody.monospacedDigit())
                Button {
                    cart.increment(productId: item.productId)
                } label: {
                    Image(systemName: "plus.circle.fill")
                        .foregroundStyle(Color.appAccent)
                        .font(.title3)
                }
            }
        }
        .padding(.vertical, 6)
        .listRowBackground(Color.appSurface)
    }

    private var summary: some View {
        VStack(spacing: 12) {
            HStack {
                Text("合计 / Total")
                    .font(.appBody)
                    .foregroundStyle(Color.appTextSecondary)
                Spacer()
                Text("¥\(String(format: "%.2f", cart.grandTotal))")
                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.appTextPrimary)
            }
            Button {
                showCheckout = true
            } label: {
                Text("去结算 / Checkout")
                    .font(.appBody.bold())
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(Color.appAccent)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }
        }
        .padding()
        .background(Color.appSurfaceElevated)
    }
}
