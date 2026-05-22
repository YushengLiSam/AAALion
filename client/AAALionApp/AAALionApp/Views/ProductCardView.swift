import SwiftUI

struct ProductCardView: View {
    let product: ProductCard
    /// Called when the user taps the inline + pill. Defaults to adding
    /// the product to the shared CartStore; override for tests / preview.
    var onAddToCart: (() -> Void)? = nil

    @State private var justAdded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            thumbnail
            Text(product.title)
                .font(.appCaption)
                .foregroundStyle(Color.appTextPrimary)
                .lineLimit(2)
                .frame(width: 130, alignment: .leading)
            brandRow
            priceRow
        }
        .padding(Theme.cardPadding)
        .background(Color.appSurfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: Theme.cardCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cardCornerRadius)
                .stroke(Color.appBorder, lineWidth: 0.5)
        )
        .shadow(color: Color.black.opacity(0.04), radius: 6, x: 0, y: 2)
    }

    // MARK: - Thumbnail with flag badge + add button overlay.

    private var thumbnail: some View {
        AsyncImage(url: product.imageURL) { phase in
            switch phase {
            case .success(let image):
                image.resizable().scaledToFill()
            case .failure:
                Color.appAccentMuted.opacity(0.3)
                    .overlay(Image(systemName: "photo")
                        .foregroundStyle(Color.appTextSecondary))
            case .empty:
                Color.appAccentMuted.opacity(0.2)
                    .overlay(ProgressView().tint(Color.appAccent))
            @unknown default:
                Color.appAccentMuted.opacity(0.2)
            }
        }
        .frame(width: 130, height: 130)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(alignment: .topLeading) {
            flagBadge.padding(6)
        }
        .overlay(alignment: .topTrailing) {
            addPill.padding(6)
        }
        .overlay(alignment: .bottomTrailing) {
            if justAdded {
                Label("已加入", systemImage: "checkmark.circle.fill")
                    .font(.system(size: 11, weight: .semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(.ultraThinMaterial, in: Capsule())
                    .foregroundStyle(Color.appAccent)
                    .padding(6)
                    .transition(.scale.combined(with: .opacity))
            }
        }
    }

    private var flagBadge: some View {
        Text(product.provenance.flag)
            .font(.system(size: 14))
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(.ultraThinMaterial, in: Capsule())
            .opacity(product.provenance.isDemo ? 0.55 : 1.0)
            .accessibilityLabel("Origin \(product.provenance.originCountry)")
    }

    private var addPill: some View {
        Button {
            // Haptic feedback (light tap) for the inline add.
            let gen = UIImpactFeedbackGenerator(style: .light)
            gen.impactOccurred()

            if let onAddToCart {
                onAddToCart()
            } else {
                CartStore.shared.add(product)
            }
            withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) {
                justAdded = true
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.3) {
                withAnimation(.easeOut(duration: 0.2)) {
                    justAdded = false
                }
            }
        } label: {
            Image(systemName: "plus")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 24, height: 24)
                .background(Color.appAccent, in: Circle())
                .shadow(color: Color.black.opacity(0.18), radius: 3, x: 0, y: 1)
        }
        .buttonStyle(.plain)
        // Stop tap from bubbling up to the parent navigation Button.
        .simultaneousGesture(TapGesture().onEnded { })
        .accessibilityLabel("加入购物车")
    }

    // MARK: - Brand line + price.

    private var brandRow: some View {
        HStack(spacing: 4) {
            Text(product.provenance.brandLine(brand: product.brand))
                .font(.system(size: 11, weight: .medium, design: .rounded))
                .foregroundStyle(Color.appTextSecondary)
                .lineLimit(1)
            if product.provenance.isDemo {
                Text("演示")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1)
                    .background(Color.appAccentMuted.opacity(0.7), in: Capsule())
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
        .frame(width: 130, alignment: .leading)
    }

    private var priceRow: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("\(product.provenance.currencySymbol)\(String(format: "%.0f", product.basePrice))")
                .font(.system(size: 15, weight: .semibold, design: .rounded))
                .foregroundStyle(Color.appAccent)
            if let hint = product.provenance.currencyHint {
                Text(hint)
                    .font(.system(size: 9, weight: .medium, design: .rounded))
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
        .frame(width: 130, alignment: .leading)
    }
}
