import SwiftUI
import AuthenticationServices

/// R11 — login / sign-up page. Promoted from the R10 Settings `Form` sheet
/// to a **branded, full-screen page** (lion header + value prop + segmented
/// method picker + polished fields + primary CTA + "先逛逛/Skip").
///
/// The service layer is reused as-is — `AuthService` + `AuthState.shared`,
/// with the same three methods we shipped in R10:
///   * 密码  — 邮箱/手机号 + 密码 (register ↔ login). The "feels real" path.
///   * 短信  — 手机号 + 验证码 (demo code shown on screen; cloud sends real SMS).
///   * Apple — Sign in with Apple (needs a PAID team; surfaces a friendly
///             message on the free Personal Team — see note below).
///
/// Login is **optional** — browse + chat always work anonymously. The Skip
/// button just dismisses; sign-in only unlocks social 拼单 + cross-device
/// preferences/favorites.
///
/// NOTE on Sign in with Apple: the button is fully wired, but the capability
/// requires a PAID Apple Developer membership + the
/// `com.apple.developer.applesignin` entitlement. On the current free
/// Personal Team that entitlement can't be enabled, so on a free build the
/// Apple button surfaces a friendly "需要付费开发者账号" message and the
/// password path is the working one.
struct LoginView: View {
    @Environment(\.dismiss) private var dismiss

    // MARK: - Method selection
    enum AuthMode: String, CaseIterable, Hashable {
        case password, phone, apple, wechat
        var label: String {
            switch self {
            case .password: return L("密码")
            case .phone: return L("短信")
            case .apple: return "Apple"
            case .wechat: return L("微信")
            }
        }
    }
    @State private var mode: AuthMode = .password

    // Password path
    @State private var pwIdentifier: String = ""
    @State private var pwPassword: String = ""
    @State private var pwDisplayName: String = ""
    @State private var pwIsRegister: Bool = false

    // Phone path
    @State private var phone: String = ""
    @State private var code: String = ""
    @State private var codeSent = false
    @State private var devCode: String?

    // Shared
    @State private var busy = false
    @State private var errorText: String?
    @State private var showReset = false
    @FocusState private var focused: Field?
    private enum Field { case identifier, password, displayName, phone, code }

    private let auth = AuthService()

    var body: some View {
        ZStack {
            Color.appBackground.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 24) {
                    header
                    methodPicker
                    methodCard
                    if let err = errorText {
                        Label(err, systemImage: "exclamationmark.circle.fill")
                            .font(.appCaption)
                            .foregroundStyle(.red)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .transition(.opacity)
                    }
                    skipButton
                }
                .padding(.horizontal, 22)
                .padding(.top, 28)
                .padding(.bottom, 40)
            }
            .scrollDismissesKeyboard(.interactively)

            if busy {
                Color.black.opacity(0.06).ignoresSafeArea()
                ProgressView().controlSize(.large)
                    .padding(20)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
            }
        }
        .animation(.easeOut(duration: 0.2), value: mode)
        .animation(.easeOut(duration: 0.2), value: errorText)
        .animation(.easeOut(duration: 0.2), value: codeSent)
        .overlay(alignment: .topTrailing) {
            Button { dismiss() } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Color.appTextSecondary)
                    .padding(10)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .padding(.top, 14)
            .padding(.trailing, 16)
            .accessibilityLabel(L("关闭"))
        }
        .sheet(isPresented: $showReset) {
            PasswordResetView()
        }
    }

    // MARK: - Header (the brand)

    private var header: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [Color.appAccent, Color.appAccent.opacity(0.65)],
                            startPoint: .topLeading, endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 84, height: 84)
                    .shadow(color: Color.appAccent.opacity(0.35), radius: 12, x: 0, y: 6)
                Text("🦁").font(.system(size: 44))
            }
            Text(L("狮选 LionPick"))
                .font(.system(size: 26, weight: .bold, design: .rounded))
                .foregroundStyle(Color.appTextPrimary)
            Text(L("登录后,你的偏好 / 收藏 / 拼单 跟着账号走"))
                .font(.appCaption)
                .foregroundStyle(Color.appTextSecondary)
                .multilineTextAlignment(.center)
        }
        .padding(.bottom, 4)
    }

    private var methodPicker: some View {
        Picker(L("登录方式 / Method"), selection: $mode) {
            ForEach(AuthMode.allCases, id: \.self) { Text($0.label).tag($0) }
        }
        .pickerStyle(.segmented)
    }

    // MARK: - Per-method card

    @ViewBuilder
    private var methodCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            switch mode {
            case .password: passwordFields
            case .phone:    phoneFields
            case .apple:    appleFields
            case .wechat:   wechatFields
            }
        }
        .padding(18)
        .background(Color.appSurface)
        .clipShape(RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.appBorder, lineWidth: 1))
    }

    // MARK: Password

    @ViewBuilder
    private var passwordFields: some View {
        Text(pwIsRegister ? L("注册新账号") : L("邮箱 / 手机号 + 密码"))
            .font(.appBody.weight(.semibold))
            .foregroundStyle(Color.appTextPrimary)

        roundedField {
            TextField(L("邮箱 或 手机号 / Email or phone"), text: $pwIdentifier)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled(true)
                .keyboardType(.emailAddress)
                .focused($focused, equals: .identifier)
                .submitLabel(.next)
        }
        if pwIsRegister, !pwIdentifier.isEmpty, !identifierLooksValid {
            fieldHint(L("请输入有效的邮箱(含 @)或手机号(≥6 位数字)"))
        }

        roundedField {
            SecureField(L("密码 / Password (≥ 6 位)"), text: $pwPassword)
                .focused($focused, equals: .password)
                .submitLabel(pwIsRegister ? .next : .go)
        }
        if pwIsRegister, !pwPassword.isEmpty, pwPassword.count < 6 {
            fieldHint(L("密码至少 6 位"))
        }

        if pwIsRegister {
            roundedField {
                TextField(L("昵称(可选) / Display name"), text: $pwDisplayName)
                    .autocorrectionDisabled(true)
                    .focused($focused, equals: .displayName)
            }
        }

        primaryButton(pwIsRegister ? L("注册并登录") : L("登录"), enabled: passwordCTAEnabled) {
            focused = nil
            pwIsRegister ? doRegister() : doLogin()
        }

        Button {
            withAnimation { pwIsRegister.toggle(); errorText = nil }
        } label: {
            Text(pwIsRegister ? L("已有账号? 直接登录") : L("没账号? 立即注册"))
                .font(.appCaption)
                .foregroundStyle(Color.appAccent)
        }
        .frame(maxWidth: .infinity)

        if !pwIsRegister {
            Button { showReset = true } label: {
                Text(L("忘记密码? / Forgot password"))
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            }
            .frame(maxWidth: .infinity)
        }

        Text(L("无需短信验证,直接注册 + 密码登录。密码本地以 PBKDF2-SHA256 哈希存储。"))
            .font(.system(size: 11))
            .foregroundStyle(Color.appTextSecondary)
    }

    // MARK: Phone

    @ViewBuilder
    private var phoneFields: some View {
        Text(L("手机号 + 验证码"))
            .font(.appBody.weight(.semibold))
            .foregroundStyle(Color.appTextPrimary)

        roundedField {
            TextField(L("手机号 / Phone"), text: $phone)
                .keyboardType(.numberPad)
                .focused($focused, equals: .phone)
        }

        if codeSent {
            roundedField {
                HStack {
                    TextField(L("验证码 / Code"), text: $code)
                        .keyboardType(.numberPad)
                        .focused($focused, equals: .code)
                    if let dc = devCode {
                        Text("演示码 \(dc)")
                            .font(.appCaption.monospacedDigit())
                            .foregroundStyle(Color.appAccent)
                    }
                }
            }
        }

        primaryButton(codeSent ? L("验证并登录") : L("获取验证码"), enabled: !busy && phone.count >= 6) {
            focused = nil
            codeSent ? verifyPhone() : startPhone()
        }

        Text(L("演示后端不发送真实短信:验证码直接显示在上方,输入即可登录。生产环境由云端短信服务下发。"))
            .font(.system(size: 11))
            .foregroundStyle(Color.appTextSecondary)
    }

    // MARK: Apple

    @ViewBuilder
    private var appleFields: some View {
        Text("Sign in with Apple")
            .font(.appBody.weight(.semibold))
            .foregroundStyle(Color.appTextPrimary)

        SignInWithAppleButton(.signIn) { request in
            request.requestedScopes = [.fullName]
        } onCompletion: { result in
            handleApple(result)
        }
        .signInWithAppleButtonStyle(.black)
        .frame(height: 48)
        .clipShape(RoundedRectangle(cornerRadius: 12))

        Text(L("Sign in with Apple 需付费开发者账号方可启用,免费开发者账号会失败。演示请使用「密码」登录。"))
            .font(.system(size: 11))
            .foregroundStyle(Color.appTextSecondary)
    }

    // MARK: - 微信 (R11 DEMO — labelled mock, not real WeChat OAuth)

    @ViewBuilder
    private var wechatFields: some View {
        Button {
            runWechat()
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "message.fill")
                Text(L("微信登录")).font(.appBody.weight(.semibold))
                if busy {
                    ProgressView().tint(.white).padding(.leading, 4)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .foregroundStyle(.white)
            // WeChat brand green.
            .background(Color(red: 0.02, green: 0.76, blue: 0.30),
                        in: RoundedRectangle(cornerRadius: 12))
        }
        .disabled(busy)

        // Honest label — this is a mock, not real OAuth.
        HStack(alignment: .top, spacing: 6) {
            Text(L("演示"))
                .font(.system(size: 10, weight: .bold))
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(Color.orange.opacity(0.15), in: Capsule())
                .foregroundStyle(.orange)
            Text(L("演示版:真实微信授权需企业资质 + 官方 SDK + 审核;生产环境接入同一接口即可。"))
                .font(.system(size: 11))
                .foregroundStyle(Color.appTextSecondary)
        }
    }

    private func runWechat() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let user = try await auth.signInWithWechat()
                succeed(user)
            } catch {
                errorText = "微信登录失败:\(error.localizedDescription)"
            }
        }
    }

    // MARK: - Skip

    private var skipButton: some View {
        Button { dismiss() } label: {
            Text(L("先逛逛 / Skip"))
                .font(.appBody)
                .foregroundStyle(Color.appTextSecondary)
        }
        .padding(.top, 2)
    }

    // MARK: - Reusable bits

    @ViewBuilder
    private func roundedField<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
            .font(.appBody)
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Color.appBackground)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.appBorder, lineWidth: 1))
    }

    private func fieldHint(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11))
            .foregroundStyle(.red)
    }

    private func primaryButton(_ title: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.appBody.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(enabled ? Color.appAccent : Color.appAccent.opacity(0.4))
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .disabled(!enabled)
        .padding(.top, 2)
    }

    // MARK: - Validation

    /// Loose email/phone shape check for the register path.
    private var identifierLooksValid: Bool {
        let s = pwIdentifier.trimmingCharacters(in: .whitespaces)
        if s.contains("@") { return s.contains(".") && s.count >= 5 }
        return s.filter(\.isNumber).count >= 6
    }

    private var passwordCTAEnabled: Bool {
        guard !busy else { return false }
        if pwIsRegister {
            return identifierLooksValid && pwPassword.count >= 6
        }
        return pwIdentifier.trimmingCharacters(in: .whitespaces).count >= 3 && !pwPassword.isEmpty
    }

    // MARK: - Actions (R10 service layer, unchanged + success haptic)

    @MainActor private func succeed(_ user: AuthUser) {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        AuthState.shared.signIn(user)
        dismiss()
    }

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
                succeed(user)
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
                succeed(user)
            } catch { errorText = error.localizedDescription }
        }
    }

    private func startPhone() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let res = try await auth.startPhone(phone)
                codeSent = res.sent
                devCode = res.devCode
            } catch { errorText = error.localizedDescription }
        }
    }

    private func verifyPhone() {
        busy = true; errorText = nil
        Task { @MainActor in
            defer { busy = false }
            do {
                let user = try await auth.verifyPhone(phone, code: code)
                succeed(user)
            } catch { errorText = error.localizedDescription }
        }
    }

    private func handleApple(_ result: Result<ASAuthorization, Error>) {
        switch result {
        case .failure(let e):
            errorText = "Apple 登录失败: \(e.localizedDescription)（免费开发者账号不支持，请用密码登录）"
        case .success(let authResult):
            guard
                let cred = authResult.credential as? ASAuthorizationAppleIDCredential,
                let tokenData = cred.identityToken,
                let token = String(data: tokenData, encoding: .utf8)
            else {
                errorText = L("无法读取 Apple 身份令牌")
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
                    succeed(user)
                } catch { errorText = error.localizedDescription }
            }
        }
    }
}

/// R11 — first-launch soft prompt content. Shown once (skippable) as a
/// compact sheet from `ChatView`; never gates the app. The caller owns the
/// presentation + the "shown once" UserDefaults flag and decides what
/// `onLogin` / `onSkip` do.
struct LoginPromptCard: View {
    var onLogin: () -> Void
    var onSkip: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            ZStack {
                Circle()
                    .fill(Color.appAccentMuted.opacity(0.5))
                    .frame(width: 60, height: 60)
                Text("🦁").font(.system(size: 32))
            }
            .padding(.top, 8)

            Text(L("登录解锁拼单 + 跨设备偏好"))
                .font(.appBody.weight(.semibold))
                .foregroundStyle(Color.appTextPrimary)
                .multilineTextAlignment(.center)

            Text(L("登录后,拼单、收藏、偏好都跟着账号走。浏览和聊天始终免登录。"))
                .font(.appCaption)
                .foregroundStyle(Color.appTextSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 8)

            Button(action: onLogin) {
                Text(L("登录 / 注册"))
                    .font(.appBody.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 13)
                    .background(Color.appAccent)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            }

            Button(action: onSkip) {
                Text(L("先逛逛 / Skip"))
                    .font(.appCaption)
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity)
        .background(Color.appBackground.ignoresSafeArea())
    }
}
