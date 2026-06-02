import SwiftUI

// ===========================================================================
// R11 — account management UI: change password, forgot-password reset, and a
// (dev-only) admin user list. All reuse AuthService + AuthState.
// ===========================================================================

// MARK: - Change password

/// Presented from ProfileView for password accounts. Verifies the current
/// password server-side and sets a new one (≥6). The signed-in identity is
/// unchanged (user_id stays the same), so no re-login is needed.
struct ChangePasswordView: View {
    @Environment(\.dismiss) private var dismiss
    let userId: String

    @State private var oldPassword = ""
    @State private var newPassword = ""
    @State private var confirm = ""
    @State private var busy = false
    @State private var errorText: String?
    @State private var done = false
    private let auth = AuthService()

    private var valid: Bool {
        !busy && !oldPassword.isEmpty && newPassword.count >= 6 && newPassword == confirm
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("当前密码 / Current", text: $oldPassword)
                    SecureField("新密码 / New (≥ 6 位)", text: $newPassword)
                    SecureField("确认新密码 / Confirm", text: $confirm)
                    if !confirm.isEmpty && newPassword != confirm {
                        Text("两次新密码不一致").font(.caption).foregroundStyle(.red)
                    }
                } footer: {
                    if let err = errorText {
                        Text(err).font(.caption).foregroundStyle(.red)
                    }
                }
                Section {
                    Button {
                        change()
                    } label: {
                        HStack {
                            if busy { ProgressView().controlSize(.small) }
                            Text("确认修改 / Change")
                        }
                    }
                    .disabled(!valid)
                }
            }
            .navigationTitle("修改密码")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
            }
            .alert("密码已修改", isPresented: $done) {
                Button("好") { dismiss() }
            } message: {
                Text("下次请用新密码登录。")
            }
        }
    }

    private func change() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                _ = try await auth.changePassword(
                    userId: userId, oldPassword: oldPassword, newPassword: newPassword
                )
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                done = true
            } catch {
                errorText = error.localizedDescription
            }
        }
    }
}

// MARK: - Forgot password / reset

/// Presented from LoginView. Two steps: request a reset code (the demo
/// backend shows it on screen), then verify + set a new password — which
/// signs the user in. Mirrors the SMS-code flow.
struct PasswordResetView: View {
    @Environment(\.dismiss) private var dismiss

    @State private var identifier = ""
    @State private var code = ""
    @State private var newPassword = ""
    @State private var codeSent = false
    @State private var devCode: String?
    @State private var busy = false
    @State private var errorText: String?
    private let auth = AuthService()

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("邮箱 或 手机号 / Email or phone", text: $identifier)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled(true)
                        .keyboardType(.emailAddress)
                        .disabled(codeSent)
                    if codeSent {
                        HStack {
                            TextField("重置码 / Code", text: $code)
                                .keyboardType(.numberPad)
                            if let dc = devCode {
                                Text("演示码 \(dc)")
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(Color.appAccent)
                            }
                        }
                        SecureField("新密码 / New password (≥ 6 位)", text: $newPassword)
                    }
                    Button {
                        codeSent ? verify() : start()
                    } label: {
                        HStack {
                            if busy { ProgressView().controlSize(.small) }
                            Text(codeSent ? "重置并登录 / Reset" : "获取重置码 / Get code")
                        }
                    }
                    .disabled(busy || (codeSent ? (code.count < 4 || newPassword.count < 6) : identifier.count < 3))
                    if let err = errorText {
                        Text(err).font(.caption).foregroundStyle(.red)
                    }
                } header: {
                    Text("忘记密码 / Reset password")
                } footer: {
                    Text("演示后端不发真实邮件/短信:重置码直接显示在上方,输入即可重置。生产环境由云端下发。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("找回密码")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
            }
        }
    }

    private func start() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let r = try await auth.startPasswordReset(identifier.trimmingCharacters(in: .whitespaces))
                codeSent = r.sent
                devCode = r.devCode
            } catch {
                errorText = error.localizedDescription
            }
        }
    }

    private func verify() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let user = try await auth.verifyPasswordReset(
                    identifier: identifier.trimmingCharacters(in: .whitespaces),
                    code: code, newPassword: newPassword
                )
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                AuthState.shared.signIn(user)
                dismiss()
            } catch {
                errorText = error.localizedDescription
            }
        }
    }
}

// MARK: - Admin user list (dev only)

/// Presented from Settings (dev mode). Lists all accounts and allows delete.
/// Requires the admin token (sent as `X-Admin-Token`, compared to the backend
/// `LIONPICK_ADMIN_TOKEN` env var); the backend returns 503 if that env is
/// unset, so the API is off by default.
struct AdminUsersView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("lionpick.dev.adminToken") private var token = ""
    @State private var users: [AdminUser] = []
    @State private var busy = false
    @State private var errorText: String?
    private let auth = AuthService()

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    SecureField("Admin token (X-Admin-Token)", text: $token)
                    Button {
                        load()
                    } label: {
                        HStack {
                            if busy { ProgressView().controlSize(.small) }
                            Text("加载用户 / Load")
                        }
                    }
                    .disabled(busy || token.isEmpty)
                    if let err = errorText {
                        Text(err).font(.caption).foregroundStyle(.red)
                    }
                } header: {
                    Text("管理员 / Admin")
                } footer: {
                    Text("需后端设置 LIONPICK_ADMIN_TOKEN 环境变量;未设置时接口返回 503。左滑可删除用户。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if !users.isEmpty {
                    Section("\(users.count) 个账号 / accounts") {
                        ForEach(users) { u in
                            VStack(alignment: .leading, spacing: 2) {
                                HStack {
                                    Text(u.displayName ?? u.userId)
                                        .font(.subheadline)
                                    if u.hasPassword == 1 {
                                        Image(systemName: "key.fill")
                                            .font(.caption2).foregroundStyle(Color.appAccent)
                                    }
                                }
                                Text("\(u.provider) · \(u.userId)")
                                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }
                        .onDelete { deleteAt($0) }
                    }
                }
            }
            .navigationTitle("用户管理")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } }
            }
        }
    }

    private func load() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                users = try await auth.adminListUsers(token: token)
            } catch {
                errorText = error.localizedDescription
                users = []
            }
        }
    }

    private func deleteAt(_ idx: IndexSet) {
        let targets = idx.map { users[$0] }
        Task { @MainActor in
            for u in targets {
                try? await auth.adminDeleteUser(token: token, userId: u.userId)
            }
            load()
        }
    }
}
