import SwiftUI

struct CartSheet: View {
    @Bindable var cart: CartStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var showCheckout = false
    @State private var editMode: EditMode = .inactive

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
                    .environment(\.editMode, $editMode)
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
                        Button {
                            withAnimation {
                                editMode = editMode == .active ? .inactive : .active
                            }
                        } label: {
                            Text(editMode == .active ? "完成 / Done" : "管理 / Edit")
                        }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            cart.clear()
                            editMode = .inactive
                        } label: {
                            Text("清空 / Clear")
                        }
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
            .overlay(alignment: .topLeading) {
                Text(item.provenance.flag)
                    .font(.system(size: 12))
                    .padding(.horizontal, 4)
                    .padding(.vertical, 1)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(3)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(item.title)
                    .font(.appBody)
                    .lineLimit(2)
                Text(item.provenance.brandLine(brand: item.brand))
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
                    .lineLimit(1)
                HStack(spacing: 4) {
                    Text("\(item.provenance.currencySymbol)\(String(format: "%.0f", item.unitPrice))")
                        .font(.system(size: 14, weight: .semibold, design: .rounded))
                        .foregroundStyle(Color.appAccent)
                    if let hint = item.provenance.currencyHint {
                        Text("(\(hint))")
                            .font(.system(size: 10))
                            .foregroundStyle(Color.appTextSecondary)
                    }
                }
            }
            Spacer()
            quantityStepper(for: item)
            Button {
                cart.remove(productId: item.productId)
            } label: {
                Image(systemName: "trash")
                    .foregroundStyle(.red)
                    .font(.system(size: 16, weight: .medium))
                    .padding(8)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(item.title)")
        }
        .padding(.vertical, 6)
        .listRowBackground(Color.appSurface)
        .contextMenu {
            if let url = item.provenance.externalURL {
                Button {
                    openURL(url)
                } label: {
                    Label("在商店中查看 / View on \(item.provenance.sourcePlatform)", systemImage: "arrow.up.right.square")
                }
            }
            Button(role: .destructive) {
                cart.remove(productId: item.productId)
            } label: {
                Label("删除 / Remove", systemImage: "trash")
            }
        }
    }

    private func quantityStepper(for item: CartItem) -> some View {
        HStack(spacing: 8) {
            Button {
                cart.decrement(productId: item.productId)
            } label: {
                Image(systemName: "minus.circle.fill")
                    .foregroundStyle(Color.appAccent)
                    .font(.title3)
            }
            .buttonStyle(.plain)
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
            .buttonStyle(.plain)
        }
    }

    // MARK: - Multi-currency summary.

    /// Subtotals grouped by currency symbol so a mixed-currency cart
    /// shows e.g. "¥1,200 · $35.00" rather than a fake unified sum.
    private var totalsByCurrency: [(symbol: String, hint: String?, total: Double)] {
        var sums: [String: (symbol: String, hint: String?, total: Double)] = [:]
        for item in cart.items {
            let key = item.provenance.currency.uppercased()
            let symbol = item.provenance.currencySymbol
            let hint = item.provenance.currencyHint
            sums[key, default: (symbol, hint, 0)].total += item.lineTotal
        }
        // Stable display order: CNY first, then alphabetic.
        return sums
            .sorted { lhs, rhs in
                if lhs.key == "CNY" { return true }
                if rhs.key == "CNY" { return false }
                return lhs.key < rhs.key
            }
            .map { $0.value }
    }

    private var summary: some View {
        VStack(spacing: 10) {
            VStack(spacing: 4) {
                HStack {
                    Text("合计 / Total")
                        .font(.appBody)
                        .foregroundStyle(Color.appTextSecondary)
                    Spacer()
                }
                ForEach(totalsByCurrency, id: \.symbol) { entry in
                    HStack {
                        if let hint = entry.hint {
                            Text(hint)
                                .font(.appCaption)
                                .foregroundStyle(Color.appTextSecondary)
                        }
                        Spacer()
                        Text("\(entry.symbol)\(String(format: "%.2f", entry.total))")
                            .font(.system(size: 18, weight: .semibold, design: .rounded))
                            .foregroundStyle(Color.appTextPrimary)
                    }
                }
                if totalsByCurrency.count > 1 {
                    Text("⚠️ 多币种购物车不做汇率合计")
                        .font(.system(size: 10))
                        .foregroundStyle(Color.appTextSecondary)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                }
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
