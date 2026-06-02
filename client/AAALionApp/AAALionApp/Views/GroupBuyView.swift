import SwiftUI

/// R9.B / proposal #11 — group-buy (拼单) modal. Pinduoduo-style
/// "邀 N 人拼单立省 15%" flow. Opens a group on appear, shows the
/// discounted price + progress + countdown + member avatars, and polls
/// the backend so simulated neighbours visibly join over time. The user
/// completes the last seat by tapping "我也来拼" (or shares an invite).
///
/// Honest framing: a small "演示" note states neighbours are simulated —
/// we have no real social/friend backend.
struct GroupBuyView: View {
    let product: ProductCard
    @Environment(\.dismiss) private var dismiss

    @State private var group: GroupBuy?
    @State private var errorText: String?
    @State private var pollTask: Task<Void, Never>?
    @State private var joining = false
    @State private var showCheckout = false      // R9.B-FIX B1
    @State private var copiedToast = false        // R9.B-FIX B2
    private let service = GroupBuyService()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    if let g = group {
                        priceHeader(g)
                        progressBlock(g)
                        membersRow(g)
                        countdown(g)
                        actionButtons(g)
                        disclaimer
                    } else if let err = errorText {
                        ContentUnavailableView("无法发起拼单", systemImage: "person.2.slash", description: Text(err))
                    } else {
                        ProgressView("正在发起拼单…").padding(.top, 60)
                    }
                }
                .padding()
            }
            .navigationTitle("拼单 / Group buy")
            .navigationBarTitleDisplayMode(.inline)
            .background(Color.appBackground.ignoresSafeArea())
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
            .task { await startGroup() }
            .onDisappear { pollTask?.cancel() }
        }
    }

    // MARK: - Sections

    private func priceHeader(_ g: GroupBuy) -> some View {
        VStack(spacing: 4) {
            Text(product.title)
                .font(.appBody)
                .multilineTextAlignment(.center)
                .lineLimit(2)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let gp = g.groupPriceCNY {
                    Text("¥\(String(format: "%.2f", gp))")
                        .font(.system(size: 30, weight: .bold, design: .rounded))
                        .foregroundStyle(Color.appAccent)
                }
                Text("¥\(String(format: "%.2f", product.displayedPrice))")
                    .font(.appCaption)
                    .strikethrough()
                    .foregroundStyle(Color.appTextSecondary)
            }
            Text("拼单立省 \(g.discountPct)%")
                .font(.appCaption)
                .foregroundStyle(.white)
                .padding(.horizontal, 10).padding(.vertical, 3)
                .background(Color.appAccent, in: Capsule())
        }
    }

    private func progressBlock(_ g: GroupBuy) -> some View {
        VStack(spacing: 6) {
            HStack {
                Text(g.isComplete ? "已拼成！" : "已 \(g.filled)/\(g.targetSize) 人, 还差 \(g.remaining) 人")
                    .font(.appBody.weight(.semibold))
                    .foregroundStyle(g.isComplete ? Color.green : Color.appTextPrimary)
                Spacer()
            }
            ProgressView(value: Double(g.filled), total: Double(g.targetSize))
                .tint(g.isComplete ? Color.green : Color.appAccent)
        }
    }

    private func membersRow(_ g: GroupBuy) -> some View {
        HStack(spacing: 14) {
            ForEach(g.members) { m in
                VStack(spacing: 3) {
                    Image(systemName: m.glyph)
                        .font(.system(size: 34))
                        .foregroundStyle(m.kind == "simulated" ? Color.appTextSecondary : Color.appAccent)
                    Text(m.label).font(.system(size: 10)).lineLimit(1)
                }
            }
            // Empty seats.
            ForEach(0..<max(0, g.targetSize - g.members.count), id: \.self) { _ in
                VStack(spacing: 3) {
                    Image(systemName: "plus.circle.dashed")
                        .font(.system(size: 34))
                        .foregroundStyle(Color.appTextSecondary.opacity(0.5))
                    Text("待加入").font(.system(size: 10)).foregroundStyle(Color.appTextSecondary)
                }
            }
        }
    }

    private func countdown(_ g: GroupBuy) -> some View {
        let h = g.secondsLeft / 3600
        let m = (g.secondsLeft % 3600) / 60
        return Text(g.status == "expired" ? "拼单已过期" : "剩 \(h) 小时 \(m) 分 截止")
            .font(.appCaption)
            .foregroundStyle(Color.appTextSecondary)
    }

    @ViewBuilder
    private func actionButtons(_ g: GroupBuy) -> some View {
        VStack(spacing: 10) {
            if g.isComplete {
                // R9.B-FIX B1: real checkout button (was a static label).
                // Adds the product to the cart at the group price, then
                // opens the existing CheckoutView.
                Button {
                    CartStore.shared.add(product)
                    showCheckout = true
                } label: {
                    Label("拼单成功 · 去支付（演示）", systemImage: "checkmark.seal.fill")
                        .font(.appBody.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(Color.green)
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                }
                .sheet(isPresented: $showCheckout) {
                    CheckoutView(cart: CartStore.shared)
                }
            } else {
                // "I'll join too" fills a seat (acts as the real user tap).
                Button {
                    Task { await joinSelf(g) }
                } label: {
                    HStack {
                        if joining { ProgressView().tint(.white) }
                        Text("我也来拼 / Join")
                    }
                    .font(.appBody.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(Color.appAccent)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                }
                // R9.B-FIX B2: share clean, human-readable invite TEXT —
                // NOT the backend JSON URL (which rendered as mojibake in
                // WeChat). A real Universal Link awaits the Phase-2 cloud
                // domain; until then text + a copyable code is the clean
                // path. Copy button is the reliable fallback if a given
                // share target is finicky.
                HStack(spacing: 10) {
                    ShareLink(item: inviteText(g)) {
                        Label("邀请好友", systemImage: "square.and.arrow.up")
                            .font(.appBody)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 11)
                            .background(Color.appAccentMuted.opacity(0.4))
                            .foregroundStyle(Color.appTextPrimary)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                    Button {
                        UIPasteboard.general.string = inviteText(g)
                        withAnimation { copiedToast = true }
                        UINotificationFeedbackGenerator().notificationOccurred(.success)
                        Task { @MainActor in
                            try? await Task.sleep(for: .seconds(1.4))
                            withAnimation { copiedToast = false }
                        }
                    } label: {
                        Label(copiedToast ? "已复制" : "复制邀请", systemImage: copiedToast ? "checkmark" : "doc.on.doc")
                            .font(.appBody)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 11)
                            .background(Color.appAccentMuted.opacity(0.4))
                            .foregroundStyle(Color.appTextPrimary)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                }
            }
        }
    }

    private var disclaimer: some View {
        Text("演示：为展示拼单玩法，名额会随时间由模拟「邻居」自动加入；本 App 无真实社交后端,不会真实下单。")
            .font(.system(size: 10))
            .foregroundStyle(Color.appTextSecondary)
            .multilineTextAlignment(.center)
            .padding(.top, 4)
    }

    private func inviteText(_ g: GroupBuy) -> String {
        let price = String(format: "%.2f", g.groupPriceCNY ?? product.displayedPrice)
        // Short, friendly join code derived from the group id (the friend
        // will type this into the join screen once accounts ship; for now
        // it makes the invite feel real).
        let code = "LP-" + g.groupId.uppercased().suffix(5)
        return """
        🦁 我在「狮选 LionPick」发起了拼单！
        \(product.title)
        拼单价 ¥\(price)（立省\(g.discountPct)%）
        还差 \(g.remaining) 人就拼成，一起来吧～
        拼单码：\(code)
        """
    }

    // MARK: - Networking

    private func startGroup() async {
        guard group == nil else { return }
        do {
            let g = try await service.createGroup(
                userId: DeviceIdentity.userId,
                productId: product.productId,
                targetSize: 3
            )
            group = g
            startPolling(groupId: g.groupId)
        } catch {
            errorText = error.localizedDescription
        }
    }

    private func startPolling(groupId: String) {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000) // 3s
                if Task.isCancelled { break }
                if let g = try? await service.getGroup(groupId: groupId) {
                    await MainActor.run { withAnimation { self.group = g } }
                    if g.isComplete { break }
                }
            }
        }
    }

    private func joinSelf(_ g: GroupBuy) async {
        joining = true
        defer { joining = false }
        // A second tap from the same device is idempotent server-side; to
        // actually fill a seat in the demo we send a distinct synthetic
        // member id so "我也来拼" advances the count by one.
        let selfJoinId = "self-" + DeviceIdentity.userId.prefix(10)
        if let updated = try? await service.joinGroup(groupId: g.groupId, userId: String(selfJoinId)) {
            withAnimation { group = updated }
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }
}
