import SwiftUI

/// Funny rotating Chinese sentences shown while the assistant is waiting
/// for the first delta from the backend. Cache-miss path can be 5-10s
/// (LLM cold start + retrieval + first token), so we keep the user
/// entertained instead of staring at three lonely dots.
///
/// To add / edit phrases: just append to `Self.phrases` below — Git diff
/// stays tiny and there's no Xcode resource-bundle dance.
struct LoadingSentence: View {
    @State private var index: Int = Int.random(in: 0..<Self.phrases.count)
    @State private var visible: Bool = true

    /// 12 phrases. Rotate every 1.5s with a quick fade.
    static let phrases: [String] = [
        "🦁 狮子小哥正在认真比价中…",
        "📦 翻箱倒柜帮你找好物…",
        "✨ AI 思考中,请勿按地球的快进键",
        "🛒 让狮子去逛逛淘宝再回来",
        "🎯 锁定目标商品,扣动扳机…",
        "🍵 沏壶茶,马上就好",
        "📚 翻一下产品手册先",
        "🦴 别催,狮子在啃骨头思考",
        "🤔 这个问题有点意思…",
        "🐾 狮爪轻点屏幕,马上呈现",
        "🔍 放大镜里全是答案",
        "💫 加载中,想象一下狮子在跳舞"
    ]

    var body: some View {
        HStack(spacing: 6) {
            TypingDots()
            Text(Self.phrases[index])
                .font(.appCaption)
                .foregroundStyle(Color.appTextSecondary)
                .opacity(visible ? 1 : 0)
                .animation(.easeInOut(duration: 0.35), value: visible)
        }
        .task {
            // Fade-cycle every 1.5s. Stops cleanly when the bubble disappears.
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                if Task.isCancelled { break }
                withAnimation(.easeInOut(duration: 0.25)) { visible = false }
                try? await Task.sleep(nanoseconds: 280_000_000)
                if Task.isCancelled { break }
                index = (index + 1) % Self.phrases.count
                withAnimation(.easeInOut(duration: 0.25)) { visible = true }
            }
        }
    }
}

/// Three softly-bouncing dots. SwiftUI-only, no third-party Lottie.
private struct TypingDots: View {
    @State private var phase: Int = 0

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3) { i in
                Circle()
                    .fill(Color.appAccent)
                    .frame(width: 6, height: 6)
                    .opacity(phase == i ? 1.0 : 0.35)
            }
        }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 280_000_000)
                if Task.isCancelled { break }
                withAnimation(.easeInOut(duration: 0.25)) {
                    phase = (phase + 1) % 3
                }
            }
        }
    }
}

#Preview {
    LoadingSentence()
        .padding()
        .background(Color.appBackground)
}
