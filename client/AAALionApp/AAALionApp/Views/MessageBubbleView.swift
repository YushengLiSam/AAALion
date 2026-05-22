import SwiftUI

struct MessageBubbleView: View {
    let message: Message
    var onEdit: (() -> Void)? = nil
    var onCopy: (() -> Void)? = nil
    var onSpeak: (() -> Void)? = nil

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
                if let data = message.imageData,
                   let uiImage = UIImage(data: data) {
                    Image(uiImage: uiImage)
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: 200, maxHeight: 200)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                if !message.text.isEmpty {
                    Text(message.text)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(message.role == .user ? Color.accentColor.opacity(0.15) : Color.gray.opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .contextMenu {
                            if message.role == .user, let onEdit {
                                Button {
                                    onEdit()
                                } label: {
                                    Label("编辑 / Edit", systemImage: "pencil")
                                }
                            }
                            if let onCopy {
                                Button {
                                    onCopy()
                                } label: {
                                    Label("复制 / Copy", systemImage: "doc.on.doc")
                                }
                            }
                            if message.role == .assistant, let onSpeak {
                                Button {
                                    onSpeak()
                                } label: {
                                    Label("朗读 / Speak", systemImage: "speaker.wave.2")
                                }
                            }
                        }
                }
                if !message.products.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(message.products) { product in
                                NavigationLink(value: product) {
                                    ProductCardView(product: product)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            }
            if message.role != .user { Spacer(minLength: 40) }
        }
        .navigationDestination(for: ProductCard.self) { product in
            ProductDetailView(product: product)
        }
    }
}
