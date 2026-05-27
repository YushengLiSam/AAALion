import SwiftUI

/// Currencies the checkout view can settle in. Demo scope = ¥ + $;
/// extending to 日元 / 欧元 is one line each but unnecessary now.
enum SettlementCurrency: String, CaseIterable, Identifiable {
    case cny = "CNY"
    case usd = "USD"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .cny: return "¥ 人民币"
        case .usd: return "$ 美元"
        }
    }
    var symbol: String {
        switch self {
        case .cny: return "¥"
        case .usd: return "$"
        }
    }
    var hint: String {
        switch self {
        case .cny: return "RMB"
        case .usd: return "USD"
        }
    }
}

struct CheckoutView: View {
    @Bindable var cart: CartStore
    @State private var addressLine: String = "北京市海淀区中关村大街 1 号"
    @State private var recipient: String = "陈澍枫"
    @State private var phone: String = "138-0000-0000"
    @State private var confirmed = false
    /// Persisted across sessions so the user's preference sticks.
    @AppStorage("lionpick.checkout.settleCurrency") private var settleCurrencyRaw: String = SettlementCurrency.cny.rawValue
    /// Per-line converted amount, keyed by CartItem.id. Recomputed when
    /// the picker changes or the cart changes. nil = "still resolving"
    /// or "FX unavailable for this line".
    @State private var convertedAmount: [String: Double] = [:]
    @State private var resolving: Bool = false
    @State private var fxErrorLines: Set<String> = []
    @Environment(\.dismiss) private var dismiss

    private var settleCurrency: SettlementCurrency {
        SettlementCurrency(rawValue: settleCurrencyRaw) ?? .cny
    }

    var body: some View {
        Group {
            if confirmed {
                successView
            } else {
                checkoutForm
            }
        }
        .background(Color.appBackground.ignoresSafeArea())
        .navigationTitle("确认下单 / Confirm Order")
        .navigationBarTitleDisplayMode(.inline)
        // Recompute converted amounts whenever picker flips or cart mutates.
        .task(id: recomputeKey) {
            await recomputeAmounts()
        }
    }

    /// Combined key so `.task(id:)` fires for either input change.
    private var recomputeKey: String {
        let ids = cart.items.map(\.id).sorted().joined(separator: ",")
        return "\(settleCurrencyRaw)|\(ids)"
    }

    // MARK: - Derived UI state

    private var hasForeignLine: Bool {
        cart.items.contains { $0.provenance.currency.uppercased() != "CNY" }
    }

    private var hasNonTargetCurrencyLine: Bool {
        let target = settleCurrency.rawValue
        return cart.items.contains { $0.provenance.currency.uppercased() != target }
    }

    /// Sum of all line conversions actually resolved. Skips lines that
    /// failed FX (those show a "汇率暂不可用" note inline).
    private var totalInSettleCurrency: Double {
        cart.items.reduce(0.0) { acc, item in
            acc + (convertedAmount[item.id] ?? 0.0)
        }
    }

    private var unresolvedCount: Int { fxErrorLines.count }

    // MARK: - Checkout form body

    private var checkoutForm: some View {
        Form {
            Section("收货地址 / Shipping") {
                TextField("收件人 / Recipient", text: $recipient)
                TextField("电话 / Phone", text: $phone).keyboardType(.phonePad)
                TextField("地址 / Address", text: $addressLine, axis: .vertical)
            }
            if hasForeignLine {
                Section {
                    Picker("结算货币 / Settle in", selection: $settleCurrencyRaw) {
                        ForEach(SettlementCurrency.allCases) { c in
                            Text(c.label).tag(c.rawValue)
                        }
                    }
                    .pickerStyle(.menu)
                } header: {
                    Text("结算货币 / Settle in")
                } footer: {
                    Text("商品按 Frankfurter 参考汇率统一换算到此币种结算。")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.appTextSecondary)
                }
            }
            Section("订单明细 / Items") {
                ForEach(cart.items) { item in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.title).lineLimit(1).font(.appBody)
                            HStack(spacing: 4) {
                                Text(item.provenance.flag)
                                    .font(.system(size: 11))
                                Text("× \(item.quantity)")
                                    .font(.appCaption)
                                    .foregroundStyle(Color.appTextSecondary)
                                if fxErrorLines.contains(item.id) {
                                    Text("· 汇率暂不可用")
                                        .font(.system(size: 10))
                                        .foregroundStyle(Color.red.opacity(0.7))
                                }
                            }
                        }
                        Spacer()
                        lineAmount(for: item)
                    }
                }
            }
            Section("合计 / Total") {
                totalSection
            }
            Section {
                Button {
                    placeOrder()
                } label: {
                    Text("确认下单 / Place Order (mock)")
                        .font(.appBody.bold())
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .listRowBackground(Color.appAccent)
                .foregroundStyle(.white)
                .disabled(resolving && totalInSettleCurrency == 0)
            } footer: {
                Text("演示用模拟下单。No real payment. No real shipping.")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
        .scrollContentBackground(.hidden)
    }

    // MARK: - Per-line price label

    @ViewBuilder
    private func lineAmount(for item: CartItem) -> some View {
        if let converted = convertedAmount[item.id] {
            Text("\(settleCurrency.symbol)\(String(format: "%.2f", converted))")
                .font(.system(size: 14, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.appAccent)
        } else if fxErrorLines.contains(item.id) {
            // FX call failed → keep the user honest by showing the
            // native price they'd be paying if we could still settle it.
            Text("\(item.provenance.currencySymbol)\(String(format: "%.2f", item.lineTotal))")
                .font(.system(size: 14, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.red.opacity(0.7))
        } else {
            // Still resolving — small dotted spinner.
            ProgressView().tint(Color.appAccent).scaleEffect(0.6)
        }
    }

    // MARK: - Total section (single-currency only — no more mixed total)

    @ViewBuilder
    private var totalSection: some View {
        HStack {
            Text(settleCurrency.hint).font(.appBody)
            Spacer()
            if resolving && totalInSettleCurrency == 0 {
                ProgressView().tint(Color.appAccent)
            } else {
                Text("\(settleCurrency.symbol)\(String(format: "%.2f", totalInSettleCurrency))")
                    .font(.system(size: 20, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.appAccent)
            }
        }
        if unresolvedCount > 0 {
            Text("⚠️ \(unresolvedCount) 件商品汇率暂不可用,未计入合计")
                .font(.system(size: 11))
                .foregroundStyle(Color.red.opacity(0.7))
        }
    }

    // MARK: - Conversion engine

    /// Walk every cart line and resolve its amount in the selected
    /// settlement currency. Strategy per line:
    ///   1. If item.provenance.currency == target → use unitPrice * qty
    ///      directly (no FX needed).
    ///   2. If item already carries unitPriceCNY AND target == CNY →
    ///      use it (Tujie's pre-converted value from chat path).
    ///   3. Otherwise hit `/currency/rate?source=<item>&target=<settle>`
    ///      and multiply.
    /// Failures land in `fxErrorLines` and skip the total — we never
    /// quietly hide a price the user might be about to pay.
    @MainActor
    private func recomputeAmounts() async {
        resolving = true
        defer { resolving = false }

        var newAmounts: [String: Double] = [:]
        var newErrors: Set<String> = []
        let target = settleCurrency.rawValue

        for item in cart.items {
            let src = item.provenance.currency.uppercased()
            let lineNative = item.lineTotal  // unitPrice * quantity in source currency

            if src == target {
                newAmounts[item.id] = lineNative
                continue
            }
            // Tujie pre-converted CNY value is the fast path for CNY target.
            if target == "CNY", let preCNY = item.lineTotalCNY {
                newAmounts[item.id] = preCNY
                continue
            }
            // Otherwise: live FX lookup.
            do {
                let rate = try await FXService.shared.rate(from: src, to: target)
                newAmounts[item.id] = (lineNative * rate * 100).rounded() / 100
            } catch {
                print("[checkout] FX \(src)->\(target) failed for \(item.productId): \(error.localizedDescription)")
                newErrors.insert(item.id)
            }
        }
        convertedAmount = newAmounts
        fxErrorLines = newErrors
    }

    // MARK: - Success view

    private var successView: some View {
        VStack(spacing: 18) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 80))
                .foregroundStyle(Color.appAccent)
            Text("已下单 / Order placed")
                .font(.appTitle)
            Text("感谢使用 狮选 LionPick ✨\n商品将由 AAALion 物流团队配送（演示）")
                .font(.appBody)
                .foregroundStyle(Color.appTextSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Button {
                cart.clear()
                dismiss()
            } label: {
                Text("继续购物 / Continue shopping")
                    .font(.appBody.bold())
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(Color.appAccent)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }

    // R8.F.2: "下单" is the moment a purchase truly happens. Persist each
    // cart line as a repurchase record so the snooze / cycle clock
    // starts ticking. We fire-and-forget per line (no need to block the
    // UI on these — checkout is mock anyway), but log failures so a
    // network blip doesn't silently break the reminder loop later.
    private func placeOrder() {
        let userId = DeviceIdentity.userId
        let service = RepurchaseService()
        let snapshot = cart.items
        Task { @MainActor in
            for line in snapshot {
                do {
                    _ = try await service.recordPurchase(
                        userId: userId,
                        productId: line.productId
                    )
                } catch {
                    print("[repurchase] checkout record_purchase failed for \(line.productId): \(error.localizedDescription)")
                }
            }
        }
        confirmed = true
    }
}
