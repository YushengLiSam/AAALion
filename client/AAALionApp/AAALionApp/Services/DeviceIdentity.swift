import Foundation
import UIKit

/// Stable per-device opaque identifier used as `user_id` against the
/// backend. Backend treats it as an opaque string; we don't transmit
/// any PII.
///
/// Source preference:
///   1. **Previously stored UserDefaults value** — once we've ever
///      computed an id, keep using it for the life of this install.
///   2. **`UIDevice.current.identifierForVendor`** (IDFV) — the iOS
///      "this device + this app vendor" UUID.
///   3. **Locally-generated UUID** — fallback when IDFV is unavailable
///      (rare; only during early boot).
///
/// **Known caveat (documented per REPURCHASE_PLAN §4.1)**: both IDFV
/// and UserDefaults are wiped when the user uninstalls the app, so
/// repurchase history does not survive an uninstall. This is an
/// acceptable demo-stage tradeoff. Production would switch to Sign-in
/// with Apple to anchor to an Apple ID.
enum DeviceIdentity {
    private static let userDefaultsKey = "lionpick.repurchase.userId"

    /// The opaque, stable identifier to send as `user_id` to the backend.
    /// Computed once and cached in UserDefaults.
    static var userId: String {
        // R10: prefer the signed-in account id so every feature re-keys to
        // the account (cross-device once Sam's cloud store lands). Falls
        // back to the anonymous device id when not signed in.
        if let account = AuthState.shared.user?.userId, !account.isEmpty {
            return account
        }
        return rawDeviceId
    }

    /// The anonymous per-device id (IDFV-backed), independent of sign-in.
    /// Used as the migrate-from id when the user first signs in.
    static var rawDeviceId: String {
        if let cached = UserDefaults.standard.string(forKey: userDefaultsKey),
           !cached.isEmpty {
            return cached
        }
        let raw = UIDevice.current.identifierForVendor?.uuidString
            ?? UUID().uuidString
        UserDefaults.standard.set(raw, forKey: userDefaultsKey)
        return raw
    }

    /// For tests / "switch user" debug flows only. Not surfaced in the UI.
    static func _resetForTests() {
        UserDefaults.standard.removeObject(forKey: userDefaultsKey)
    }
}
