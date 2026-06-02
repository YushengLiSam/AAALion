import Foundation
import Observation

/// R10 / accounts — client for the /auth/* endpoints. Talks to whichever
/// user store the backend is configured with (local SQLite now, Sam's
/// cloud later) — the client is unaware of which.
struct AuthService {
    var baseURL: URL = Config.backendURL

    enum AuthError: LocalizedError {
        case http(Int, String?)
        case decode(Error)
        case transport(Error)
        var errorDescription: String? {
            switch self {
            case .http(let code, let detail): return detail ?? "HTTP \(code)"
            case .decode(let e): return "解析失败: \(e.localizedDescription)"
            case .transport(let e): return e.localizedDescription
            }
        }
    }

    // MARK: - Sign in with Apple

    /// Exchange an Apple identity token for an account.
    func signInWithApple(identityToken: String, displayName: String?) async throws -> AuthUser {
        try await postUser("auth/apple", [
            "identity_token": identityToken,
            "display_name": displayName as Any,
        ])
    }

    // MARK: - 微信 (R11 DEMO — mock, not real WeChat OAuth)

    /// Demo-only WeChat sign-in. The backend returns a stable `wechat:demo`
    /// account; real WeChat OAuth needs 企业资质 + the official SDK, so the
    /// UI button is labelled 「演示」. Production swaps the SDK in here.
    func signInWithWechat() async throws -> AuthUser {
        try await postUser("auth/wechat", [:])
    }

    // MARK: - 手机号 + 验证码

    /// Request an SMS code. In the local demo backend the response carries
    /// `devCode` so the flow completes without a real SMS; the cloud
    /// backend omits it (real SMS sent).
    func startPhone(_ phone: String) async throws -> PhoneStartResult {
        let url = baseURL.appendingPathComponent("auth/phone/start")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        req.httpBody = try JSONSerialization.data(withJSONObject: ["phone": phone])
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw AuthError.http(-1, nil) }
        guard http.statusCode == 200 else { throw AuthError.http(http.statusCode, detail(data)) }
        return (try? JSONDecoder().decode(PhoneStartResult.self, from: data)) ?? PhoneStartResult(sent: true, devCode: nil)
    }

    /// Verify the SMS code → account.
    func verifyPhone(_ phone: String, code: String) async throws -> AuthUser {
        try await postUser("auth/phone/verify", ["phone": phone, "code": code])
    }

    // MARK: - 邮箱 / 手机号 + 密码 (R10.bugfix — simpler than SMS for the demo)

    func registerPassword(identifier: String, password: String, displayName: String?) async throws -> AuthUser {
        try await postUser("auth/register", [
            "identifier": identifier,
            "password": password,
            "display_name": displayName as Any,
        ])
    }

    func loginPassword(identifier: String, password: String) async throws -> AuthUser {
        try await postUser("auth/password/login", [
            "identifier": identifier,
            "password": password,
        ])
    }

    // MARK: - migrate anonymous data on first sign-in

    func migrate(from: String, to: String) async {
        let url = baseURL.appendingPathComponent("auth/migrate")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["from_user_id": from, "to_user_id": to])
        _ = try? await URLSession.shared.data(for: req)
    }

    // MARK: - helpers

    private func postUser(_ path: String, _ body: [String: Any]) async throws -> AuthUser {
        let url = baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        req.httpBody = try JSONSerialization.data(withJSONObject: body.compactMapValues { $0 is NSNull ? nil : $0 })
        do {
            let (data, response) = try await URLSession.shared.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw AuthError.http(-1, nil) }
            guard http.statusCode == 200 else { throw AuthError.http(http.statusCode, detail(data)) }
            do { return try JSONDecoder().decode(AuthUser.self, from: data) }
            catch { throw AuthError.decode(error) }
        } catch let e as AuthError {
            throw e
        } catch {
            throw AuthError.transport(error)
        }
    }

    private func detail(_ data: Data) -> String? {
        (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
    }
}

struct PhoneStartResult: Codable {
    let sent: Bool
    let devCode: String?
    enum CodingKeys: String, CodingKey { case sent; case devCode = "dev_code" }
}

struct AuthUser: Codable, Hashable {
    let userId: String
    let provider: String
    let displayName: String?
    let token: String?
    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case provider
        case displayName = "display_name"
        case token
    }
}

/// App-wide auth state. Persists the signed-in account in UserDefaults
/// (the user_id is an opaque identifier, not a secret). `DeviceIdentity`
/// reads this so all features re-key to the account when signed in.
@Observable
final class AuthState {
    static let shared = AuthState()

    private let key = "lionpick.auth.user"

    private(set) var user: AuthUser?

    var isSignedIn: Bool { user != nil }
    var displayName: String { user?.displayName ?? (user?.provider == "phone" ? "手机用户" : "已登录用户") }

    private init() {
        if let raw = UserDefaults.standard.data(forKey: key),
           let u = try? JSONDecoder().decode(AuthUser.self, from: raw) {
            user = u
        }
    }

    /// Record a successful sign-in. Migrates the device's prior anonymous
    /// rows to the account once, then switches the active id.
    @MainActor
    func signIn(_ u: AuthUser) {
        let previousAnon = DeviceIdentity.rawDeviceId
        user = u
        persist()
        // Re-key anonymous data to the account (fire-and-forget).
        if previousAnon != u.userId {
            Task { await AuthService().migrate(from: previousAnon, to: u.userId) }
        }
    }

    @MainActor
    func signOut() {
        user = nil
        UserDefaults.standard.removeObject(forKey: key)
    }

    private func persist() {
        if let u = user, let raw = try? JSONEncoder().encode(u) {
            UserDefaults.standard.set(raw, forKey: key)
        }
    }
}
