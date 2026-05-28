import SwiftUI

struct MessageBubbleView: View {
    let message: Message
    var onEdit: (() -> Void)? = nil
    var onCopy: (() -> Void)? = nil
    var onSpeak: (() -> Void)? = nil

    @State private var tappedProduct: ProductCard?

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
                // R8.E: render up to 10 attachments as a wrapping grid
                // above the text bubble (ChatGPT pattern). Images inline;
                // file kinds get a doc-icon chip + filename.
                if !message.attachments.isEmpty {
                    attachmentGrid
                }
                if !message.text.isEmpty {
                    // R9.A.3 — assistant messages render with claim-provenance
                    // badges inline. [目录✓] turns green, [推断?] turns amber.
                    // User messages render plain. Defensive: if the LLM
                    // didn't emit markers, output is identical to plain text.
                    Text(renderedMessageText)
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
                } else if message.role == .assistant && message.isStreaming {
                    // Empty placeholder bubble + funny waiting sentence.
                    LoadingSentence()
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Color.gray.opacity(0.12))
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                }
                if !message.products.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(message.products) { product in
                                // Use a plain Button (not NavigationLink) so the
                                // inline + pill inside ProductCardView can have
                                // its own tap target without SwiftUI's stacked
                                // hit-test eating it.
                                Button {
                                    tappedProduct = product
                                } label: {
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
        .navigationDestination(item: $tappedProduct) { product in
            ProductDetailView(product: product)
        }
    }

    /// 2-row wrapping grid of attachment thumbnails. Mirrors the ChatGPT
    /// composer: image attachments inline as 96-pt thumbnails; file kinds
    /// (PDF / unknown) get a doc icon + filename chip of the same footprint
    /// so the row stays visually even.
    private var attachmentGrid: some View {
        // Flow-layout shim: max 5 per row, wrap to a second row, cap at
        // Attachment.maxCount (10). LazyVGrid keeps the layout aligned
        // regardless of how many items there are.
        let columns = Array(
            repeating: GridItem(.fixed(96), spacing: 6),
            count: min(message.attachments.count, 5)
        )
        return LazyVGrid(columns: columns, alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
            ForEach(message.attachments) { attachment in
                attachmentThumbnail(attachment)
            }
        }
        .frame(maxWidth: 96 * 5 + 6 * 4, alignment: message.role == .user ? .trailing : .leading)
    }

    // R9.A.3 — convert raw LLM text into AttributedString with claim
    // provenance badges. The LLM is instructed to emit `[目录✓]` /
    // `[推断?]` immediately after each fact (see _PROMPT in chat.py).
    // We color those markers green / amber so the user sees per-claim
    // provenance at a glance. Non-assistant messages and assistants that
    // forgot to emit markers come through unchanged.
    private var renderedMessageText: AttributedString {
        var attr = AttributedString(message.text)
        guard message.role == .assistant else { return attr }

        // Highlight verified markers in green.
        var search = attr.startIndex..<attr.endIndex
        while let range = attr[search].range(of: "[目录✓]") {
            attr[range].foregroundColor = Color.green
            attr[range].font = .system(size: 11, weight: .bold)
            search = range.upperBound..<attr.endIndex
        }

        // Highlight inferred markers in amber.
        search = attr.startIndex..<attr.endIndex
        while let range = attr[search].range(of: "[推断?]") {
            attr[range].foregroundColor = Color.orange
            attr[range].font = .system(size: 11, weight: .bold)
            search = range.upperBound..<attr.endIndex
        }

        return attr
    }

    @ViewBuilder
    private func attachmentThumbnail(_ attachment: Attachment) -> some View {
        if attachment.isImage, let uiImage = UIImage(data: attachment.data) {
            Image(uiImage: uiImage)
                .resizable()
                .scaledToFill()
                .frame(width: 96, height: 96)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(Color.gray.opacity(0.15), lineWidth: 0.5)
                )
        } else {
            VStack(spacing: 4) {
                Image(systemName: "doc.fill")
                    .font(.system(size: 26))
                    .foregroundStyle(Color.appAccent)
                Text(attachment.filename ?? "文件")
                    .font(.caption2)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .foregroundStyle(.primary)
            }
            .frame(width: 96, height: 96)
            .background(Color.gray.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }
}
