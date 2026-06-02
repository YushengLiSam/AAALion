import SwiftUI

/// R11 — the "我的" / account page. Reached from the chat top-bar avatar
/// when signed in. Consolidates account state that used to be scattered:
///   * identity (display name + sign-in method badge)
///   * 我的收藏  — the favorites Sam added (resolved to product cards)
///   * 我的偏好  — the preference table moved out of Settings (R9.B / #12)
///   * 我的拼单  — active group-buys via `GET /groupbuy/active`
///   * 退出登录
///
/// Everything is keyed by `DeviceIdentity.userId`, which returns the
/// signed-in account id when logged in — so preferences / group-buys
/// re-key to the account automatically.
struct ProfileView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var auth = AuthState.shared
    @State private var favorites = FavoritesStore.shared

    @State private var favoriteProducts: [ProductCard] = []
    @State private var favoritesLoading = false
    @State private var prefItems: [PreferenceItem] = []
    @State private var activeGroups: [GroupBuy] = []
    @State private var groupsLoading = false

    // R11 — account management (change password / delete account).
    @State private var showChangePassword = false
    @State private var showDeleteConfirm = false
    @State private var deletePassword = ""
    @State private var deleteError: String?

    var body: some View {
        NavigationStack {
            Form {
                identitySection
                favoritesSection
                preferencesSection
                groupBuySection
                accountSecuritySection
                signOutSection
            }
            .navigationTitle("我的 / Account")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
            .refreshable { await loadAll() }
            .task { await loadAll() }
            .sheet(isPresented: $showChangePassword) {
                ChangePasswordView(userId: auth.user?.userId ?? "")
            }
            .alert("注销账号?", isPresented: $showDeleteConfirm) {
                if auth.user?.provider == "password" {
                    SecureField("输入密码确认", text: $deletePassword)
                }
                Button("注销", role: .destructive) { performDelete() }
                Button("取消", role: .cancel) { deletePassword = "" }
            } message: {
                Text("将删除你的账号及其偏好 / 收藏 / 拼单数据,不可恢复。")
            }
        }
    }

    // MARK: - Identity

    private var identitySection: some View {
        Section {
            HStack(spacing: 14) {
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: [Color.appAccent, Color.appAccent.opacity(0.65)],
                                startPoint: .topLeading, endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 56, height: 56)
                    Text(initial)
                        .font(.system(size: 24, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(auth.displayName)
                        .font(.appBody.weight(.semibold))
                        .foregroundStyle(Color.appTextPrimary)
                    Label(providerLabel, systemImage: providerIcon)
                        .font(.appCaption)
                        .foregroundStyle(Color.appTextSecondary)
                }
                Spacer()
            }
            .padding(.vertical, 4)
        }
    }

    private var initial: String {
        let n = auth.displayName.trimmingCharacters(in: .whitespaces)
        guard let first = n.first else { return "🦁" }
        return String(first).uppercased()
    }

    private var providerLabel: String {
        switch auth.user?.provider {
        case "apple": return "Apple 登录"
        case "phone": return "手机号登录"
        case "password": return "邮箱 / 手机号 登录"
        default: return "已登录"
        }
    }

    private var providerIcon: String {
        switch auth.user?.provider {
        case "apple": return "apple.logo"
        case "phone": return "phone.fill"
        case "password": return "key.fill"
        default: return "person.fill"
        }
    }

    // MARK: - 我的收藏

    @ViewBuilder
    private var favoritesSection: some View {
        // Re-filter against the live store so un-favoriting a card in this
        // view (tap its ❤️) removes it immediately, without a reload.
        let shown = favoriteProducts.filter { favorites.isFavorite($0.productId) }
        Section {
            if favoritesLoading && shown.isEmpty {
                loadingRow
            } else if shown.isEmpty {
                HStack(spacing: 10) {
                    Image(systemName: "heart")
                        .font(.system(size: 18))
                        .foregroundStyle(Color.appAccent.opacity(0.55))
                    Text("还没有收藏。在任意商品卡片点 ❤️ 即可收藏,这里随时回看。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 12) {
                        ForEach(shown) { product in
                            ProductCardView(product: product)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 4)
                    .padding(.bottom, 8)
                }
                .listRowInsets(EdgeInsets())
            }
        } header: {
            HStack {
                Label("我的收藏 / Favorites", systemImage: "heart.fill")
                    .foregroundStyle(Color.appAccent)
                Spacer()
                if !shown.isEmpty {
                    Text("\(shown.count) 件")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(Color.appAccent)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 2)
                        .background(Color.appAccent.opacity(0.12), in: Capsule())
                }
            }
        } footer: {
            if !shown.isEmpty {
                Text("点卡片上的 ❤️ 可取消收藏。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - 我的偏好 (moved out of Settings, R9.B / #12)

    @ViewBuilder
    private var preferencesSection: some View {
        Section {
            if prefItems.isEmpty {
                emptyRow("还没有偏好。在商品详情页点 👍 / 👎 即可训练。")
            } else {
                ForEach(prefItems) { item in
                    HStack {
                        Image(systemName: item.isLiked ? "hand.thumbsup.fill" : "hand.thumbsdown.fill")
                            .foregroundStyle(item.isLiked ? Color.green : Color.orange)
                            .font(.caption)
                        Text("\(item.dimensionLabel) · \(item.value)")
                            .font(.footnote)
                        Spacer()
                        Text(String(format: "%+.0f", item.score))
                            .font(.footnote.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                Button(role: .destructive) {
                    Task {
                        try? await PreferenceService().resetPreferences(userId: DeviceIdentity.userId)
                        await loadPreferences()
                    }
                } label: {
                    Label("重置偏好 / Reset (我变了)", systemImage: "arrow.counterclockwise")
                }
            }
        } header: {
            Text("我的偏好 / My preferences")
        } footer: {
            Text("你的 👍 / 👎 会轻微调整后续推荐排序。登录后跟随账号(未来云端跨设备),可一键清空。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - 我的拼单

    @ViewBuilder
    private var groupBuySection: some View {
        Section {
            if groupsLoading && activeGroups.isEmpty {
                loadingRow
            } else if activeGroups.isEmpty {
                emptyRow("还没有拼单。在商品详情页发起「拼单」即可。")
            } else {
                ForEach(activeGroups, id: \.groupId) { group in
                    groupRow(group)
                }
            }
        } header: {
            Text("我的拼单 / Group buys")
        } footer: {
            Text("你发起的拼单。名额由模拟「邻居」随时间加入(演示),拼满即可去结算。")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func groupRow(_ g: GroupBuy) -> some View {
        HStack(spacing: 12) {
            AsyncImage(url: g.product?.imageURL) { phase in
                switch phase {
                case .success(let image): image.resizable().scaledToFill()
                case .empty: Color.appAccentMuted.opacity(0.2).overlay(ProgressView().controlSize(.small))
                default: Color.appAccentMuted.opacity(0.3).overlay(Image(systemName: "photo").foregroundStyle(.secondary))
                }
            }
            .frame(width: 46, height: 46)
            .clipShape(RoundedRectangle(cornerRadius: 10))

            VStack(alignment: .leading, spacing: 3) {
                Text(g.product?.title ?? g.productId)
                    .font(.footnote.weight(.medium))
                    .lineLimit(1)
                Text("已 \(g.filled)/\(g.targetSize) 人 · 立省 \(g.discountPct)%")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            statusBadge(g.status)
        }
        .padding(.vertical, 2)
    }

    private func statusBadge(_ status: String) -> some View {
        let (text, color): (String, Color) = {
            switch status {
            case "complete": return ("已拼成", .green)
            case "expired": return ("已过期", .gray)
            default: return ("进行中", Color.appAccent)
            }
        }()
        return Text(text)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color, in: Capsule())
    }

    // MARK: - Account security (R11)

    private var accountSecuritySection: some View {
        Section {
            if auth.user?.provider == "password" {
                Button {
                    showChangePassword = true
                } label: {
                    Label("修改密码 / Change password", systemImage: "key.fill")
                }
            }
            Button(role: .destructive) {
                deletePassword = ""
                deleteError = nil
                showDeleteConfirm = true
            } label: {
                Label("注销账号 / Delete account", systemImage: "trash")
            }
        } header: {
            Text("账号安全 / Security")
        } footer: {
            if let err = deleteError {
                Text("注销失败:\(err)").font(.caption).foregroundStyle(.red)
            } else if auth.user?.provider != "password" {
                Text("当前登录方式无密码,注销无需密码确认。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func performDelete() {
        let pw = auth.user?.provider == "password" ? deletePassword : nil
        let uid = auth.user?.userId ?? ""
        deleteError = nil
        Task { @MainActor in
            do {
                try await AuthService().deleteAccount(userId: uid, password: pw)
                deletePassword = ""
                auth.signOut()
                dismiss()
            } catch {
                deleteError = error.localizedDescription
            }
        }
    }

    // MARK: - Sign out

    private var signOutSection: some View {
        Section {
            Button(role: .destructive) {
                auth.signOut()
                dismiss()
            } label: {
                Label("退出登录 / Sign out", systemImage: "rectangle.portrait.and.arrow.right")
            }
        }
    }

    // MARK: - Shared rows

    private var loadingRow: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("加载中 / Loading…").font(.footnote).foregroundStyle(.secondary)
        }
    }

    private func emptyRow(_ text: String) -> some View {
        Text(text).font(.footnote).foregroundStyle(.secondary)
    }

    // MARK: - Loading

    private func loadAll() async {
        async let favs: Void = loadFavorites()
        async let prefs: Void = loadPreferences()
        async let grps: Void = loadGroups()
        _ = await (favs, prefs, grps)
    }

    @MainActor
    private func loadFavorites() async {
        let ids = Array(favorites.ids)
        guard !ids.isEmpty else { favoriteProducts = []; return }
        favoritesLoading = true
        defer { favoritesLoading = false }
        var products: [ProductCard] = []
        await withTaskGroup(of: ProductCard?.self) { group in
            let svc = ProductService()
            for id in ids {
                group.addTask { try? await svc.fetch(productId: id) }
            }
            for await product in group {
                if let product { products.append(product) }
            }
        }
        favoriteProducts = products.sorted { $0.title < $1.title }
    }

    @MainActor
    private func loadPreferences() async {
        prefItems = (try? await PreferenceService().fetchPreferences(userId: DeviceIdentity.userId)) ?? []
    }

    @MainActor
    private func loadGroups() async {
        groupsLoading = true
        defer { groupsLoading = false }
        activeGroups = (try? await GroupBuyService().fetchActive(userId: DeviceIdentity.userId)) ?? []
    }
}
