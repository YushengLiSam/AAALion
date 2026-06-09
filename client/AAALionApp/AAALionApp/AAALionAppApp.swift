import SwiftUI

@main
struct AAALionAppApp: App {
    // In-app language toggle. `LanguageManager` is @Observable and `L(_:)` reads
    // its `bundle`, so switching language re-evaluates ONLY the views that render
    // localized strings — navigation stack, sheets and scroll position are all
    // preserved. (Earlier builds used `.id(lang)` here, which rebuilt the whole
    // tree and bounced the user back to the home screen — removed for smoothness.)
    @State private var languageManager = LanguageManager.shared

    var body: some Scene {
        WindowGroup {
            Group {
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
            .environment(languageManager)
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
                             displayName: L("演示用户"),
                             token: "pw:demo@example.com")
                )
            }
        default:
            ChatView(viewModel: ChatViewModel())
        }
    }
}
