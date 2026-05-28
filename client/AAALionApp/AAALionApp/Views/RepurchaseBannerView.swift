import SwiftUI

/// Proactive repurchase reminder shown at the top of the chat stream
/// when the user opens the app. Reuses the catalog colors and corner
/// radius from Theme.swift so it feels native to the rest of the app,
/// but uses a distinct accent bell icon + tinted background so it
/// reads as "system-initiated", not as a normal product card.
///
/// Renders horizontally — one card per due reminder, scroll if 2+.
/// Tap "再来一单" to fire `POST /repurchase/purchase` via the
/// ChatViewModel and locally dismiss the card.
///
/// Empty-state policy: the parent ChatView simply does NOT instantiate
/// this view when the reminders list is empty — there is no "no
/// reminders" placeholder, by design (REPURCHASE_PLAN §3.2).
struct RepurchaseBannerView: View {
    let reminders: [RepurchaseReminder]
    /// Fires when the user taps "再来一单" on a specific card.
    let onReorder: (RepurchaseReminder) -> Void
    /// Fires when the user explicitly closes a card (X button).
    let onDismiss: (RepurchaseReminder) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(reminders) { reminder in
                        card(for: reminder)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 4)
            }
        }
        .padding(.top, 8)
        .background(
            Color.appAccentMuted.opacity(0.15)
                .ignoresSafeArea(edges: .horizontal)
        )
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 6) {
            Image(systemName: "bell.badge.fill")
                .foregroundStyle(Color.appAccent)
                .imageScale(.small)
            Text("该补货啦")
                .font(.appCaption)
                .foregroundStyle(Color.appTextSecondary)
            Spacer()
        }
        .padding(.horizontal, 16)
    }

    // MARK: - Single reminder card

    private func card(for reminder: RepurchaseReminder) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                thumb(for: reminder.product)
                VStack(alignment: .leading, spacing: 4) {
                    Text(reminder.product.title)
                        .font(.appCaption)
                        .foregroundStyle(Color.appTextPrimary)
                        .lineLimit(2)
                    Text(reminder.product.provenance.brandLine(brand: reminder.product.brand))
                        .font(.system(size: 11, weight: .regular, design: .rounded))
                        .foregroundStyle(Color.appTextSecondary)
                        .lineLimit(1)
                    overdueChip(reminder.daysOverdue)
                }
                Spacer(minLength: 0)
                Button {
                    onDismiss(reminder)
                } label: {
                    Image(systemName: "xmark")
                        .imageScale(.small)
                        .foregroundStyle(Color.appTextSecondary)
                        .padding(4)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("忽略")
            }
            Text(reminder.reminderText)
                .font(.system(size: 13, design: .rounded))
                .foregroundStyle(Color.appTextPrimary)
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
            reorderButton(reminder)
        }
        .padding(Theme.cardPadding)
        .frame(width: 260, alignment: .leading)
        .background(Color.appSurfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: Theme.cardCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cardCornerRadius)
                .stroke(Color.appAccent.opacity(0.35), lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.05), radius: 4, x: 0, y: 1)
    }

    private func thumb(for product: ProductCard) -> some View {
        AsyncImage(url: product.imageURL) { phase in
            switch phase {
            case .success(let image):
                image.resizable().scaledToFill()
            case .failure:
                Color.appAccentMuted.opacity(0.3)
                    .overlay(Image(systemName: "photo").foregroundStyle(Color.appTextSecondary))
            case .empty:
                Color.appAccentMuted.opacity(0.2)
                    .overlay(ProgressView().tint(Color.appAccent).scaleEffect(0.6))
            @unknown default:
                Color.appAccentMuted.opacity(0.2)
            }
        }
        .frame(width: 60, height: 60)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func overdueChip(_ days: Int) -> some View {
        let (text, bg, fg): (String, Color, Color) = {
            if days <= 0 {
                return ("到期", Color.appAccentMuted.opacity(0.4), Color.appAccent)
            }
            if days <= 7 {
                return ("超期\(days)天", Color.appAccentMuted.opacity(0.55), Color.appAccent)
            }
            return ("超期\(days)天", Color.appAccent.opacity(0.2), Color.appAccent)
        }()
        return Text(text)
            .font(.system(size: 10, weight: .semibold, design: .rounded))
            .foregroundStyle(fg)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(bg)
            .clipShape(Capsule())
    }

    private func reorderButton(_ reminder: RepurchaseReminder) -> some View {
        Button {
            onReorder(reminder)
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "arrow.clockwise")
                    .imageScale(.small)
                Text("再来一单")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 7)
            .foregroundStyle(.white)
            .background(Color.appAccent)
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
    }
}
