import SwiftUI

@main
struct AAALionAppApp: App {
    var body: some Scene {
        WindowGroup {
            // CI screenshot hook: `simctl launch … -lionpickShot <name>` maps
            // to UserDefaults (NSArgumentDomain) and routes straight to one
            // screen so it can be captured headlessly. Inert in normal use —
            // no such launch argument means the real app launches.
            if let shot = UserDefaults.standard.string(forKey: "lionpickShot"), !shot.isEmpty {
                ScreenshotHost(name: shot)
            } else {
                ChatView(viewModel: ChatViewModel())
            }
        }
    }
}

/// CI-only: present a single screen by name so the GitHub Actions runner can
/// screenshot screens that normally sit behind a tap (login / profile /
/// change-password / reset / admin). Never reached in normal use.
private struct ScreenshotHost: View {
    let name: String

    var body: some View {
        switch name {
        case "login":
            LoginView()
        case "reset":
            PasswordResetView()
        case "changePassword":
            ChangePasswordView(userId: "pw:demo@example.com")
        case "admin":
            AdminUsersView()
        case "profile":
            ProfileView().onAppear {
                AuthState.shared._setDemoUser(
                    AuthUser(userId: "pw:demo@example.com",
                             provider: "password",
                             displayName: "演示用户",
                             token: "pw:demo@example.com")
                )
            }
        default:
            ChatView(viewModel: ChatViewModel())
        }
    }
}
