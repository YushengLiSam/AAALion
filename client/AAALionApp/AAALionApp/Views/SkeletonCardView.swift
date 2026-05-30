import SwiftUI

/// R10 #4.4⭐⭐⭐ — skeleton (骨架屏) placeholder shown while a query is
/// in flight and product cards haven't arrived yet. Matches the real
/// ProductCardView footprint (130-pt wide) so the layout doesn't jump
/// when the real cards stream in. A moving highlight gives the classic
/// "shimmer" loading feel of a商业-grade app.
struct SkeletonCardView: View {
    @State private var phase: CGFloat = -1

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            block(width: 130, height: 130, corner: 14)   // thumbnail
            block(width: 120, height: 12, corner: 4)      // title line 1
            block(width: 80, height: 12, corner: 4)       // title line 2
            block(width: 60, height: 11, corner: 4)       // brand
            block(width: 50, height: 15, corner: 4)       // price
        }
        .padding(Theme.cardPadding)
        .background(Color.appSurfaceElevated)
        .clipShape(RoundedRectangle(cornerRadius: Theme.cardCornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cardCornerRadius)
                .stroke(Color.appBorder, lineWidth: 0.5)
        )
        .onAppear {
            withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: false)) {
                phase = 2
            }
        }
        .accessibilityLabel("加载中")
    }

    private func block(width: CGFloat, height: CGFloat, corner: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: corner)
            .fill(Color.appAccentMuted.opacity(0.25))
            .frame(width: width, height: height)
            .overlay(shimmer.clipShape(RoundedRectangle(cornerRadius: corner)))
    }

    /// A diagonal highlight band that sweeps left → right across each block.
    private var shimmer: some View {
        GeometryReader { geo in
            LinearGradient(
                colors: [.clear, Color.white.opacity(0.55), .clear],
                startPoint: .leading,
                endPoint: .trailing
            )
            .frame(width: geo.size.width)
            .offset(x: phase * geo.size.width)
        }
    }
}
