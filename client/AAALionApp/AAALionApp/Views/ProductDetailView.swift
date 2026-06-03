import SwiftUI

struct ProductDetailView: View {
    let product: ProductCard
    @State private var cart = CartStore.shared
    @State private var addedToast = false
    /// Drives the button's "just-tapped" morph — true for ~1.5s after
    /// adding to cart, then resets so the user could add another one.
    @State private var justAdded = false
    @Environment(\.openURL) private var openURL
    // R9.A.4 — price-watch state. Modal collects the target price; the
    // success/error toast is reused via `addedToast` to keep the UX
    // consistent.
    @State private var showPriceWatchSheet = false
    @State private var priceWatchTargetText: String = ""
    @State private var priceWatchSubmitting = false
    @State private var priceWatchError: String?
    private let priceWatchService = PriceWatchService()
    // R9.B / #12 — closed-loop preference. nil = no feedback yet this view,
    // +1 = liked, -1 = disliked. Drives the button highlight.
    //
    // R10.bugfix: persisted in UserDefaults per (userId, productId) so leaving
    // and re-entering the product page keeps the button state (the server's
    // dimension scores persist regardless; this is purely UX continuity).
    @State private var prefSignal: Int? = nil
    private let preferenceService = PreferenceService()

    private func prefStorageKey(_ uid: String, _ pid: String) -> String {
        "lionpick.pref.signal.\(uid).\(pid)"
    }

    private func loadStoredPrefSignal() {
        let key = prefStorageKey(DeviceIdentity.userId, product.productId)
        if UserDefaults.standard.object(forKey: key) == nil {
            prefSignal = nil
            return
        }
        let v = UserDefaults.standard.integer(forKey: key)   // 0 if unset → coerced to nil above
        prefSignal = (v == 1 || v == -1) ? v : nil
    }

    private func persistPrefSignal(_ signal: Int?) {
        let key = prefStorageKey(DeviceIdentity.userId, product.productId)
        if let s = signal {
            UserDefaults.standard.set(s, forKey: key)
        } else {
            UserDefaults.standard.removeObject(forKey: key)
        }
    }
    // R9.B / #11 — group-buy modal.
    @State private var showGroupBuy = false
    // R12 — one-tap order (single product) confirm sheet.
    @State private var showInstantOrder = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Color.clear.frame(height: 0).onAppear { loadStoredPrefSignal() }
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

                priceBlock
                provenanceCard

                addToCartButton

                // R12 — one-tap "立即购买" (agentic order close, demo).
                instantBuyButton

                // R9.B / #11 — start a group buy (拼单).
                groupBuyButton

                // R9.B / #12 — 👍 / 👎 feedback. Taps train the on-backend
                // per-device preference prior that re-ranks future results.
                preferenceButtons

                if let url = product.provenance.externalURL {
                    storeLinkButton(url: url)
                } else {
                    disabledStoreLink
                }

                // R9.A.4 — "Notify me on price drop" button.
                priceWatchButton

                // R9.A.2 — "Why this is recommended" debug card. Renders only
                // when the backend attached retrieval_signals (skipped for
                // cached responses or pre-R9 product cards). Defensive on
                // every field — anything nil just doesn't render.
                if let signals = product.retrievalSignals {
                    whyRecommendedCard(signals: signals)
                }
            }
            .padding()
        }
        .background(Color.appBackground.ignoresSafeArea())
        .navigationTitle(product.brand)
        .navigationBarTitleDisplayMode(.inline)
        // R8.F.5 fix: float the "已加入购物车" toast at the top of the
        // screen as an overlay (was inside ScrollView so it could land
        // off-screen if the user was scrolled down). Same look as the
        // ChatView dev-mode toast — feels native.
        .overlay(alignment: .top) {
            if addedToast {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Color.appAccent)
                    Text("已加入购物车 / Added to cart")
                        .font(.appCaption)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(.ultraThinMaterial, in: Capsule())
                .padding(.top, 8)
                .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.easeOut(duration: 0.2), value: addedToast)
    }

    private var priceBlock: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(product.displayedCurrencySymbol)\(String(format: "%.2f", product.displayedPrice))")
                .font(.system(size: 28, weight: .bold, design: .rounded))
                .foregroundStyle(Color.appAccent)
            if let originalPrice = product.originalPriceText {
                Text("原价 \(originalPrice)")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            } else if product.priceCNY == nil, let hint = product.provenance.currencyHint {
                Text("\(hint)原价，人民币汇率暂不可用")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
    }

    /// Grouped section: origin / platform / currency / shipping. Hidden for demo items.
    private var provenanceCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(product.provenance.flag)
                Text("来源 / Source")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            }
            .padding(.bottom, 2)
            row("origin",  "产地",        product.provenance.originCountry)
            row("storefront",  "平台",     product.provenance.sourcePlatform)
            row("creditcard.fill", "原始币种", "\(product.provenance.currency) (\(product.provenance.currencySymbol))")
            if let quote = product.exchangeRate, let text = product.exchangeRateText {
                row("arrow.triangle.2.circlepath", "参考汇率", text)
                row("calendar", "汇率来源", quote.provider)
            }
            if let ship = product.provenance.shippingNote {
                row("shippingbox.fill", "配送", ship)
            }
            if product.provenance.isDemo {
                Text("⚠️ 此商品为演示数据。外部链接为商品标题搜索。")
                    .font(.appCaption)
                    .foregroundStyle(.orange)
                    .padding(.top, 4)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.appSurface)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.appBorder, lineWidth: 0.5)
        )
    }

    private func row(_ icon: String, _ label: String, _ value: String) -> some View {
        HStack {
            Image(systemName: icon)
                .foregroundStyle(Color.appTextSecondary)
                .frame(width: 18)
            Text(label)
                .font(.appCaption)
                .foregroundStyle(Color.appTextSecondary)
                .frame(width: 60, alignment: .leading)
            Text(value)
                .font(.appCaption)
                .foregroundStyle(Color.appTextPrimary)
            Spacer()
        }
    }

    private var addToCartButton: some View {
        Button {
            cart.add(product)
            // R8.F.5: double-up the haptic — medium impact PLUS success
            // notification. iOS users feel the impact "thunk" reliably even
            // through a case; the success notification adds a gentle
            // texture afterward. Together it reads as a real "click +
            // confirmation" rather than a single subtle pulse.
            let impact = UIImpactFeedbackGenerator(style: .medium)
            impact.impactOccurred()
            let success = UINotificationFeedbackGenerator()
            success.notificationOccurred(.success)
            // Morph the button itself for ~1.5s — confirmation right where
            // the user's finger is.
            withAnimation(.spring(response: 0.3, dampingFraction: 0.6)) {
                justAdded = true
                addedToast = true
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                withAnimation(.easeOut(duration: 0.2)) {
                    justAdded = false
                    addedToast = false
                }
            }
        } label: {
            HStack(spacing: 8) {
                Image(systemName: justAdded ? "checkmark.circle.fill" : "cart.badge.plus")
                Text(justAdded ? "已加入购物车 / Added" : "加入购物车 / Add to Cart")
            }
            .font(.appBody.bold())
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            // Slightly darker accent during the "just added" state so the
            // morph reads as confirmation, not a regular state.
            .background(justAdded ? Color.appAccent.opacity(0.85) : Color.appAccent)
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .scaleEffect(justAdded ? 0.97 : 1.0)
        }
        // Disable double-tap-add by greying out for the morph window.
        .disabled(justAdded)
    }

    private func storeLinkButton(url: URL) -> some View {
        Button {
            openURL(url)
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "arrow.up.right.square")
                Text("去原页 / View on \(product.provenance.sourcePlatform)")
            }
            .font(.appBody.bold())
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(Color.appAccent, lineWidth: 1.5)
            )
            .foregroundStyle(Color.appAccent)
        }
    }

    private var disabledStoreLink: some View {
        HStack(spacing: 8) {
            Image(systemName: "link.badge.plus")
            Text("演示商品 · 无原页链接")
        }
        .font(.appCaption)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Color.appAccentMuted.opacity(0.3))
        .foregroundStyle(Color.appTextSecondary)
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    // R9.A.2 — "Why this is recommended" expandable debug card.
    // Defense-grade transparency: shows the retrieval signals that
    // ranked this product, plus the source citation (proposal #5).
    @ViewBuilder
    private func whyRecommendedCard(signals: RetrievalSignals) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 10) {
                // Source citation (proposal #5).
                if !product.provenance.isDemo {
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: "book.closed")
                            .foregroundStyle(Color.appTextSecondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("信源 / Source")
                                .font(.system(size: 10, weight: .regular, design: .rounded))
                                .foregroundStyle(Color.appTextSecondary)
                            Text(product.provenance.sourcePlatform)
                                .font(.appCaption)
                            if let urlText = product.provenance.externalURL?.absoluteString,
                               !urlText.isEmpty {
                                Text(urlText)
                                    .font(.system(size: 10, weight: .regular, design: .rounded))
                                    .foregroundStyle(Color.appAccent)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                        }
                        Spacer()
                    }
                    Divider()
                }
                // Plain-language summary line.
                if !signals.humanSummary.isEmpty {
                    Text(signals.humanSummary)
                        .font(.appCaption)
                        .foregroundStyle(Color.appTextPrimary)
                }
                // Detailed rows. Each only renders if non-nil.
                if let rerankRank = signals.rerankRank {
                    signalRow(label: "最终排名", value: "#\(rerankRank + 1)")
                }
                if let rerankScore = signals.rerankScore {
                    signalRow(label: "精排得分", value: String(format: "%.3f", rerankScore))
                }
                if let denseRank = signals.denseRank {
                    signalRow(label: "语义检索排名", value: "#\(denseRank + 1)")
                }
                if let bm25Rank = signals.bm25Rank {
                    signalRow(label: "关键词检索排名", value: "#\(bm25Rank + 1)")
                }
                if let rrfScore = signals.rrfScore {
                    signalRow(label: "RRF 融合得分", value: String(format: "%.4f", rrfScore))
                }
                if let rerankModel = signals.rerankModel {
                    signalRow(label: "精排模型", value: rerankModel)
                }
                Text("说明: 越靠前(数字小)越相关。语义检索抓「意思」, 关键词检索抓「字面」, 精排是 cross-encoder 做最终重排。")
                    .font(.system(size: 10, weight: .regular, design: .rounded))
                    .foregroundStyle(Color.appTextSecondary)
                    .padding(.top, 4)
            }
            .padding(.top, 8)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "info.circle")
                    .foregroundStyle(Color.appAccent)
                Text("为何推荐这款 / Why this?")
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextPrimary)
            }
        }
        .padding(12)
        .background(Color.appSurfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func signalRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(.appCaption)
                .foregroundStyle(Color.appTextSecondary)
            Spacer()
            Text(value)
                .font(.appCaption.monospacedDigit())
                .foregroundStyle(Color.appTextPrimary)
        }
    }

    // R12 — one-tap "立即购买 / Buy now": opens the shared InstantOrderSheet
    // for this single product (default address + default payment, demo order).
    private var instantBuyButton: some View {
        Button {
            showInstantOrder = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "bolt.fill")
                Text("立即购买 · 一键下单 / Buy now")
            }
            .font(.appBody.bold())
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background(Color.appAccent.opacity(0.14))
            .foregroundStyle(Color.appAccent)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .sheet(isPresented: $showInstantOrder) {
            InstantOrderSheet(items: [CartItem(from: product)], clearCartOnOrder: false)
        }
    }

    // R9.B / proposal #11 — group-buy entry button.
    private var groupBuyButton: some View {
        Button {
            showGroupBuy = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "person.2.fill")
                Text("发起拼单 · 立省 15% / Start a group buy")
            }
            .font(.appCaption)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .background(Color.appAccent.opacity(0.12))
            .foregroundStyle(Color.appAccent)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .sheet(isPresented: $showGroupBuy) {
            GroupBuyView(product: product)
        }
    }

    // R9.B / proposal #12 — closed-loop preference feedback.
    private var preferenceButtons: some View {
        HStack(spacing: 10) {
            Button {
                sendPreference(1)
            } label: {
                Label("喜欢", systemImage: prefSignal == 1 ? "hand.thumbsup.fill" : "hand.thumbsup")
                    .font(.appCaption)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 9)
                    .background(prefSignal == 1 ? Color.green.opacity(0.18) : Color.appAccentMuted.opacity(0.3))
                    .foregroundStyle(prefSignal == 1 ? Color.green : Color.appTextSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            Button {
                sendPreference(-1)
            } label: {
                Label("不喜欢", systemImage: prefSignal == -1 ? "hand.thumbsdown.fill" : "hand.thumbsdown")
                    .font(.appCaption)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 9)
                    .background(prefSignal == -1 ? Color.orange.opacity(0.18) : Color.appAccentMuted.opacity(0.3))
                    .foregroundStyle(prefSignal == -1 ? Color.orange : Color.appTextSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    private func sendPreference(_ signal: Int) {
        // Toggle off if tapping the same signal again.
        let newSignal: Int? = (prefSignal == signal) ? nil : signal
        withAnimation { prefSignal = newSignal }
        persistPrefSignal(newSignal)         // R10.bugfix — survive view recycle
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        // Only POST a concrete +1/-1 (not the toggle-off). Fire-and-forget;
        // a network hiccup shouldn't block the UI.
        guard let s = newSignal else { return }
        let userId = DeviceIdentity.userId
        let productId = product.productId
        Task { try? await preferenceService.sendFeedback(userId: userId, productId: productId, signal: s) }
    }

    // R9.A.4 — proposal #7 price-tracking.
    private var priceWatchButton: some View {
        Button {
            // Default the modal text to 90% of current price as a sensible
            // starting point — most users want a small discount, not 50% off.
            let suggested = max(1, Int((product.displayedPrice * 0.9).rounded()))
            priceWatchTargetText = String(suggested)
            priceWatchError = nil
            showPriceWatchSheet = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "bell.badge")
                Text("提醒我降价 / Notify me on price drop")
            }
            .font(.appCaption)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .background(Color.appAccentMuted.opacity(0.5))
            .foregroundStyle(Color.appTextPrimary)
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .sheet(isPresented: $showPriceWatchSheet) {
            priceWatchSheet
                .presentationDetents([.medium])
        }
    }

    private var priceWatchSheet: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        Text("当前价格").foregroundStyle(Color.appTextSecondary)
                        Spacer()
                        Text("¥\(String(format: "%.2f", product.displayedPrice))")
                            .font(.appBody.monospacedDigit())
                    }
                    HStack(spacing: 4) {
                        Text("¥")
                            .foregroundStyle(Color.appTextSecondary)
                        TextField("目标价 (例如 \(Int(product.displayedPrice * 0.9))) ", text: $priceWatchTargetText)
                            .keyboardType(.numberPad)
                            .textFieldStyle(.plain)
                            .font(.appBody.monospacedDigit())
                    }
                    if let err = priceWatchError {
                        Text(err).font(.appCaption).foregroundStyle(.red)
                    }
                } header: {
                    Text("设置目标价")
                } footer: {
                    Text("当该商品价格 ≤ 你设置的目标价,我们会在你下次打开 App 时提醒你。")
                }
            }
            .navigationTitle("提醒我降价")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { showPriceWatchSheet = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        submitPriceWatch()
                    } label: {
                        if priceWatchSubmitting {
                            ProgressView()
                        } else {
                            Text("保存")
                        }
                    }
                    .disabled(priceWatchSubmitting)
                }
            }
        }
    }

    private func submitPriceWatch() {
        guard let target = Double(priceWatchTargetText.trimmingCharacters(in: .whitespaces)),
              target > 0 else {
            priceWatchError = "请输入大于 0 的目标价"
            return
        }
        priceWatchError = nil
        priceWatchSubmitting = true
        let userId = DeviceIdentity.userId
        let productId = product.productId
        Task { @MainActor in
            defer { priceWatchSubmitting = false }
            do {
                _ = try await priceWatchService.startWatch(
                    userId: userId,
                    productId: productId,
                    targetPriceCNY: target
                )
                showPriceWatchSheet = false
                // Reuse the addedToast pattern.
                withAnimation { addedToast = true }
                Task { @MainActor in
                    try? await Task.sleep(for: .seconds(1.6))
                    withAnimation { addedToast = false }
                }
            } catch {
                priceWatchError = error.localizedDescription
            }
        }
    }
}
