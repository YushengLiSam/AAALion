import SwiftUI

struct CheckoutView: View {
    @Bindable var cart: CartStore
    @State private var addressLine: String = "北京市海淀区中关村大街 1 号"
    @State private var recipient: String = "陈澍枫"
    @State private var phone: String = "138-0000-0000"
    @State private var confirmed = false
    @Environment(\.dismiss) private var dismiss

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
    }

    private var cnyTotal: Double {
        cart.items.compactMap(\.lineTotalCNY).reduce(0, +)
    }

    private var hasCNYTotal: Bool {
        cart.items.contains { $0.lineTotalCNY != nil }
    }

    private var unresolvedTotalsByCurrency: [(symbol: String, hint: String?, total: Double)] {
        var sums: [String: (symbol: String, hint: String?, total: Double)] = [:]
        for item in cart.items where item.lineTotalCNY == nil {
            let key = item.provenance.currency.uppercased()
            let symbol = item.provenance.currencySymbol
            let hint = item.provenance.currencyHint
            sums[key, default: (symbol, hint, 0)].total += item.lineTotal
        }
        return sums
            .sorted { $0.key < $1.key }
            .map { $0.value }
    }

    private var checkoutForm: some View {
        Form {
            Section("收货地址 / Shipping") {
                TextField("收件人 / Recipient", text: $recipient)
                TextField("电话 / Phone", text: $phone).keyboardType(.phonePad)
                TextField("地址 / Address", text: $addressLine, axis: .vertical)
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
                            }
                        }
                        Spacer()
                        Text("\(item.displayedCurrencySymbol)\(String(format: "%.2f", item.lineTotalCNY ?? item.lineTotal))")
                            .font(.system(size: 14, weight: .semibold, design: .rounded))
                            .foregroundStyle(Color.appAccent)
                    }
                }
            }
            Section("合计 / Total") {
                if hasCNYTotal {
                    HStack {
                        Text("人民币")
                            .font(.appBody)
                        Spacer()
                        Text("¥\(String(format: "%.2f", cnyTotal))")
                            .font(.system(size: 20, weight: .semibold, design: .rounded))
                            .foregroundStyle(Color.appAccent)
                    }
                }
                ForEach(unresolvedTotalsByCurrency, id: \.symbol) { entry in
                    HStack {
                        Text(entry.hint ?? "外币")
                            .font(.appBody)
                        Spacer()
                        Text("\(entry.symbol)\(String(format: "%.2f", entry.total))")
                            .font(.system(size: 18, weight: .semibold, design: .rounded))
                            .foregroundStyle(Color.appAccent)
                    }
                }
                if !unresolvedTotalsByCurrency.isEmpty {
                    Text("部分外币汇率暂不可用，未计入人民币合计")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.appTextSecondary)
                }
            }
            Section {
                Button {
                    confirmed = true
                } label: {
                    Text("确认下单 / Place Order (mock)")
                        .font(.appBody.bold())
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .listRowBackground(Color.appAccent)
                .foregroundStyle(.white)
            } footer: {
                Text("演示用模拟下单。No real payment. No real shipping.")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
        .scrollContentBackground(.hidden)
    }

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
}
