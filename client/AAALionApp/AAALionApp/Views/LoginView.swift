import SwiftUI
import AuthenticationServices

/// R10 / accounts — sign-in sheet. Two methods:
///   * Sign in with Apple (native; see entitlement note below).
///   * 手机号 + 验证码 (works on the free dev team without extra infra;
///     in the local demo backend the code is shown on screen so the flow
///     completes without a real SMS).
///
/// NOTE on Sign in with Apple: the button is fully wired, but the
/// capability requires a PAID Apple Developer membership + the
/// `com.apple.developer.applesignin` entitlement. On the current free
/// Personal Team that entitlement can't be enabled (it would break
/// provisioning), so on a free build the Apple button surfaces a friendly
/// "需要付费开发者账号" message and the phone path is the working one.
/// Once we're on a paid team / the cloud, enable the entitlement in
/// project.yml and it works unchanged.
struct LoginView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var phone: String = ""
    @State private var code: String = ""
    @State private var codeSent = false
    @State private var devCode: String?       // shown in demo backend
    @State private var busy = false
    @State private var errorText: String?
    private let auth = AuthService()

    // R10.bugfix — password auth is the "feels real" path; SMS-code-on-screen
    // is preserved as an alternate, Apple stays disabled on free team.
    enum AuthMode: String, CaseIterable, Hashable {
        case password, phone, apple
        var label: String {
            switch self {
            case .password: return "密码"
            case .phone: return "短信"
            case .apple: return "Apple"
            }
        }
    }
    @State private var mode: AuthMode = .password
    @State private var pwIdentifier: String = ""
    @State private var pwPassword: String = ""
    @State private var pwDisplayName: String = ""
    @State private var pwIsRegister: Bool = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("登录方式 / Method", selection: $mode) {
                        ForEach(AuthMode.allCases, id: \.self) { m in
                            Text(m.label).tag(m)
                        }
                    }
                    .pickerStyle(.segmented)
                    .listRowInsets(EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16))
                }

                switch mode {
                case .password: passwordSection
                case .phone:    phoneSection
                case .apple:    appleSection
                }
            }
            .navigationTitle("登录 / Sign in")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
            }
            .overlay {
                if busy { ProgressView().controlSize(.large) }
            }
        }
    }

    // MARK: - Password section (R10.bugfix — the "feels real" path)

    @ViewBuilder
    private var passwordSection: some View {
        Section {
            TextField("邮箱 或 手机号 / Email or phone", text: $pwIdentifier)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled(true)
                .keyboardType(.emailAddress)
            SecureField("密码 / Password (≥ 6 chars)", text: $pwPassword)
            if pwIsRegister {
                TextField("昵称(可选) / Display name", text: $pwDisplayName)
                    .autocorrectionDisabled(true)
            }
            Button(pwIsRegister ? "注册并登录 / Register" : "登录 / Sign in") {
                pwIsRegister ? doRegister() : doLogin()
            }
            .disabled(busy || pwIdentifier.count < 3 || pwPassword.count < (pwIsRegister ? 6 : 1))
            Button(pwIsRegister ? "已有账号? 直接登录" : "没账号? 立即注册") {
                pwIsRegister.toggle(); errorText = nil
            }
            .font(.footnote)
            if let err = errorText { Text(err).font(.caption).foregroundStyle(.red) }
        } header: {
            Text("邮箱/手机 + 密码")
        } footer: {
            Text("没有短信验证,直接注册账号 + 密码登录。账号是邮箱或手机号,密码本地以 PBKDF2-SHA256 哈希存储。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var phoneSection: some View {
        Section {
            TextField("手机号 / Phone", text: $phone)
                .keyboardType(.numberPad)
            if codeSent {
                HStack {
                    TextField("验证码 / Code", text: $code)
                        .keyboardType(.numberPad)
                    if let dc = devCode {
                        Text("演示码 \(dc)")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(Color.appAccent)
                    }
                }
            }
            Button(codeSent ? "验证并登录 / Verify" : "获取验证码 / Get code") {
                codeSent ? verifyPhone() : startPhone()
            }
            .disabled(busy || phone.count < 6)
            if let err = errorText {
                Text(err).font(.caption).foregroundStyle(.red)
            }
        } header: {
            Text("短信验证码 / SMS")
        } footer: {
            Text("演示后端不发送真实短信:验证码直接显示在上方,输入即可登录。生产环境由云端短信服务下发。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var appleSection: some View {
        Section {
            SignInWithAppleButton(.signIn) { request in
                request.requestedScopes = [.fullName]
            } onCompletion: { result in
                handleApple(result)
            }
            .signInWithAppleButtonStyle(.black)
            .frame(height: 46)
            .listRowInsets(EdgeInsets())
        } header: {
            Text("Sign in with Apple")
        } footer: {
            Text("Sign in with Apple 需付费开发者账号方可启用,免费开发者账号会失败。演示请使用上面的密码登录。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: - Password actions

    private func doRegister() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let user = try await auth.registerPassword(
                    identifier: pwIdentifier.trimmingCharacters(in: .whitespaces),
                    password: pwPassword,
                    displayName: pwDisplayName.isEmpty ? nil : pwDisplayName
                )
                AuthState.shared.signIn(user)
                dismiss()
            } catch { errorText = error.localizedDescription }
        }
    }

    private func doLogin() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let user = try await auth.loginPassword(
                    identifier: pwIdentifier.trimmingCharacters(in: .whitespaces),
                    password: pwPassword
                )
                AuthState.shared.signIn(user)
                dismiss()
            } catch { errorText = error.localizedDescription }
        }
    }

    // MARK: - Phone

    private func startPhone() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let res = try await auth.startPhone(phone)
                codeSent = res.sent
                devCode = res.devCode          // nil on the cloud backend
            } catch {
                errorText = error.localizedDescription
            }
        }
    }

    private func verifyPhone() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let user = try await auth.verifyPhone(phone, code: code)
                AuthState.shared.signIn(user)
                dismiss()
            } catch {
                errorText = error.localizedDescription
            }
        }
    }

    // MARK: - Apple

    private func handleApple(_ result: Result<ASAuthorization, Error>) {
        switch result {
        case .failure(let e):
            errorText = "Apple 登录失败: \(e.localizedDescription)（免费开发者账号不支持，请用手机号登录）"
        case .success(let authResult):
            guard
                let cred = authResult.credential as? ASAuthorizationAppleIDCredential,
                let tokenData = cred.identityToken,
                let token = String(data: tokenData, encoding: .utf8)
            else {
                errorText = "无法读取 Apple 身份令牌"
                return
            }
            let name = [cred.fullName?.familyName, cred.fullName?.givenName]
                .compactMap { $0 }.joined()
            busy = true; errorText = nil
            Task { @MainActor in
                defer { busy = false }
                do {
                    let user = try await auth.signInWithApple(
                        identityToken: token,
                        displayName: name.isEmpty ? nil : name
                    )
                    AuthState.shared.signIn(user)
                    dismiss()
                } catch {
                    errorText = error.localizedDescription
                }
            }
        }
    }
}
