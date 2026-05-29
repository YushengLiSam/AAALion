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

    var body: some View {
        NavigationStack {
            Form {
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
                    Text("快捷登录 / Quick sign-in")
                } footer: {
                    Text("Sign in with Apple 需付费开发者账号方可启用；演示请用下方手机号登录。")
                        .font(.caption).foregroundStyle(.secondary)
                }

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
                    Text("手机号登录 / Phone sign-in")
                } footer: {
                    Text("演示后端不发送真实短信:验证码直接显示在上方,输入即可登录。生产环境由云端短信服务下发。")
                        .font(.caption).foregroundStyle(.secondary)
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
