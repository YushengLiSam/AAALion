import SwiftUI

/// R12 — shared default shipping address, persisted per account in
/// UserDefaults (mirrors CartStore/FavoritesStore keying). Falls back to the
/// original hardcoded demo values, so nothing breaks if the user never edits.
enum OrderDefaults {
    private static func key(_ field: String) -> String {
        "lionpick.order.\(field).\(DeviceIdentity.userId)"
    }
    static var recipient: String {
        get { UserDefaults.standard.string(forKey: key("recipient")) ?? L("陈澍枫") }
        set { UserDefaults.standard.set(newValue, forKey: key("recipient")) }
    }
    static var phone: String {
        get { UserDefaults.standard.string(forKey: key("phone")) ?? "138-0000-0000" }
        set { UserDefaults.standard.set(newValue, forKey: key("phone")) }
    }
    static var address: String {
        get { UserDefaults.standard.string(forKey: key("address")) ?? L("北京市海淀区中关村大街 1 号") }
        set { UserDefaults.standard.set(newValue, forKey: key("address")) }
    }
}

/// R12 — "Agent 一键下单" confirm sheet (DEMO). One shared flow for both
/// entry points: the chat "帮我下单/结算" intent (whole cart) and the
/// product-page 「立即购买」 button (single item). Default address + default
/// payment → one tap → mock order. **Payment is a demo — no real charge.**
struct InstantOrderSheet: View {
    let items: [CartItem]
    /// Whole-cart orders empty the cart on success; a single-product
    /// "立即购买" leaves the cart untouched.
    var clearCartOnOrder: Bool = false

    @Environment(\.dismiss) private var dismiss
    @State private var cart = CartStore.shared
    @State private var placed = false
    @State private var editingAddress = false
    @State private var recipient = OrderDefaults.recipient
    @State private var phone = OrderDefaults.phone
    @State private var address = OrderDefaults.address

    private var total: Double {
        items.reduce(0) { $0 + $1.displayedUnitPrice * Double($1.quantity) }
    }
    private var symbol: String { items.first?.displayedCurrencySymbol ?? "¥" }
    private var totalQty: Int { items.reduce(0) { $0 + $1.quantity } }

    var body: some View {
        NavigationStack {
            Group {
                if placed { successView } else { confirmView }
            }
            .navigationTitle(placed ? L("下单成功 / Done") : L("确认下单 / Checkout"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(placed ? L("完成") : L("取消")) { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    // MARK: - Confirm

    private var confirmView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                addressCard
                itemsCard
                disclaimer
            }
            .padding(18)
        }
        .safeAreaInset(edge: .bottom) { placeButton }
    }

    private var addressCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(L("收货地址 / Ship to"), systemImage: "location.fill")
                    .font(.appCaption).foregroundStyle(Color.appTextSecondary)
                Spacer()
                Button(editingAddress ? L("完成") : L("修改")) {
                    if editingAddress {            // persist edits
                        OrderDefaults.recipient = recipient
                        OrderDefaults.phone = phone
                        OrderDefaults.address = address
                    }
                    withAnimation { editingAddress.toggle() }
                }
                .font(.appCaption).foregroundStyle(Color.appAccent)
            }
            if editingAddress {
                field($recipient, L("收件人"))
                field($phone, L("手机号"))
                field($address, L("地址"))
            } else {
                Text("\(recipient)  ·  \(phone)").font(.appBody.weight(.semibold))
                    .foregroundStyle(Color.appTextPrimary)
                Text(address).font(.appCaption).foregroundStyle(Color.appTextSecondary)
            }
            Text(L("默认支付:零钱(演示) · Default payment: demo wallet"))
                .font(.caption2).foregroundStyle(Color.appTextSecondary)
        }
        .padding(16)
        .background(Color.appSurface)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.appBorder, lineWidth: 1))
    }

    private func field(_ text: Binding<String>, _ placeholder: String) -> some View {
        TextField(placeholder, text: text)
            .font(.appCaption)
            .padding(.horizontal, 10).padding(.vertical, 9)
            .background(Color(.tertiarySystemBackground))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private var itemsCard: some View {
        VStack(spacing: 10) {
            ForEach(items) { item in
                HStack(spacing: 10) {
                    Text(item.title).font(.appCaption).lineLimit(2)
                        .foregroundStyle(Color.appTextPrimary)
                    Spacer(minLength: 8)
                    Text("×\(item.quantity)").font(.caption2).foregroundStyle(Color.appTextSecondary)
                    Text("\(item.displayedCurrencySymbol)\(String(format: "%.2f", item.displayedUnitPrice * Double(item.quantity)))")
                        .font(.appCaption.monospacedDigit()).foregroundStyle(Color.appTextPrimary)
                }
            }
            Divider()
            HStack {
                Text("合计 \(totalQty) 件 / Total").font(.appCaption).foregroundStyle(Color.appTextSecondary)
                Spacer()
                Text("\(symbol)\(String(format: "%.2f", total))")
                    .font(.appBody.bold().monospacedDigit()).foregroundStyle(Color.appAccent)
            }
        }
        .padding(16)
        .background(Color.appSurface)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.appBorder, lineWidth: 1))
    }

    private var disclaimer: some View {
        Label(L("演示下单:不接入真实支付,仅记录购买用于复购提醒。"), systemImage: "info.circle")
            .font(.caption2).foregroundStyle(Color.appTextSecondary)
    }

    private var placeButton: some View {
        Button(action: place) {
            Text(L("确认下单 · 演示  /  Place order"))
                .font(.appBody.bold()).frame(maxWidth: .infinity).padding(.vertical, 15)
                .background(Color.appAccent).foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .padding(.horizontal, 18).padding(.vertical, 12)
        .background(.ultraThinMaterial)
        .disabled(items.isEmpty)
        .opacity(items.isEmpty ? 0.5 : 1)
    }

    // MARK: - Success

    private var successView: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 56)).foregroundStyle(Color.green)
            Text(L("下单成功(演示)")).font(.title3.bold()).foregroundStyle(Color.appTextPrimary)
            Text("已下单 \(totalQty) 件 · \(symbol)\(String(format: "%.2f", total))\n寄往:\(address)")
                .font(.appCaption).foregroundStyle(Color.appTextSecondary)
                .multilineTextAlignment(.center)
            Spacer()
            Button(L("完成 / Done")) { dismiss() }
                .font(.appBody.bold()).frame(maxWidth: .infinity).padding(.vertical, 14)
                .background(Color.appAccent).foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 14))
                .padding(.horizontal, 18).padding(.bottom, 12)
        }
        .padding(18)
    }

    // MARK: - Action

    private func place() {
        OrderDefaults.recipient = recipient
        OrderDefaults.phone = phone
        OrderDefaults.address = address
        cart.placeOrder(productIds: items.map(\.productId))
        if clearCartOnOrder { cart.clear() }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        withAnimation { placed = true }
    }
}
