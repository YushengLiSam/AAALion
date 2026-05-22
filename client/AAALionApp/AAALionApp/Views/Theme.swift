import SwiftUI

/// Visual identity for 狮选 LionPick. Tokens informed by a Claude design
/// consult (see `design-tokens.json`). Warm ivory + amber-gold accent +
/// deep espresso text, with SF Pro Rounded for the playful-but-premium feel.
extension Color {
    init(light: UInt32, dark: UInt32) {
        self = Color(UIColor { trait in
            let hex = trait.userInterfaceStyle == .dark ? dark : light
            return UIColor(
                red: CGFloat((hex >> 16) & 0xFF) / 255.0,
                green: CGFloat((hex >> 8) & 0xFF) / 255.0,
                blue: CGFloat(hex & 0xFF) / 255.0,
                alpha: 1.0
            )
        })
    }

    static let appBackground         = Color(light: 0xFBF7F1, dark: 0x161310)
    static let appSurface            = Color(light: 0xFFFFFF, dark: 0x1F1B16)
    static let appSurfaceElevated    = Color(light: 0xFFFDF8, dark: 0x2A241D)
    static let appTextPrimary        = Color(light: 0x1F1A14, dark: 0xF5EFE3)
    static let appTextSecondary      = Color(light: 0x7A6E5F, dark: 0xA89C88)
    static let appAccent             = Color(light: 0xE89A3C, dark: 0xF2B25C)
    static let appAccentMuted        = Color(light: 0xF6D8A8, dark: 0x5C4525)
    static let appUserBubble         = Color(light: 0xFCE4BE, dark: 0x3A2C18)
    static let appAssistantBubble    = Color(light: 0xFFFFFF, dark: 0x231E18)
    static let appBorder             = Color(light: 0xECE3D2, dark: 0x332C23)
}

extension Font {
    static var appTitle: Font { .system(size: 22, weight: .semibold, design: .rounded) }
    static var appBody: Font { .system(size: 16, weight: .regular, design: .rounded) }
    static var appCaption: Font { .system(size: 12, weight: .medium, design: .rounded) }
}

enum Theme {
    static let bubbleCornerRadius: CGFloat = 18
    static let bubblePaddingX: CGFloat = 14
    static let bubblePaddingY: CGFloat = 10
    static let cardCornerRadius: CGFloat = 20
    static let cardPadding: CGFloat = 14
}
