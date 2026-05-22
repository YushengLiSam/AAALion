import SwiftUI
import PhotosUI

struct ChatView: View {
    @Bindable var viewModel: ChatViewModel
    @State private var pickerItem: PhotosPickerItem?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if let err = viewModel.errorMessage {
                    Text("⚠️ \(err)")
                        .font(.caption)
                        .foregroundStyle(.red)
                        .padding(.horizontal)
                        .padding(.vertical, 6)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.red.opacity(0.08))
                }
                messageList
                composer
            }
            .navigationTitle("智能导购")
            .navigationBarTitleDisplayMode(.inline)
            .task {
                viewModel.runScriptedQueryIfAny()
            }
        }
    }

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(viewModel.messages) { message in
                        MessageBubbleView(message: message)
                            .id(message.id)
                    }
                }
                .padding()
            }
            .onChange(of: viewModel.messages.last?.text) { _, _ in
                guard let last = viewModel.messages.last else { return }
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
    }

    private var composer: some View {
        VStack(spacing: 8) {
            if let data = viewModel.pendingImage,
               let uiImage = UIImage(data: data) {
                HStack {
                    Image(uiImage: uiImage)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 64, height: 64)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    Spacer()
                    Button {
                        viewModel.pendingImage = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                            .font(.title3)
                    }
                }
                .padding(.horizontal, 4)
            }
            HStack(spacing: 8) {
                PhotosPicker(selection: $pickerItem, matching: .images, photoLibrary: .shared()) {
                    Image(systemName: "photo.on.rectangle")
                        .font(.title3)
                }
                .onChange(of: pickerItem) { _, newItem in
                    Task { @MainActor in
                        guard let item = newItem,
                              let data = try? await item.loadTransferable(type: Data.self) else { return }
                        viewModel.pendingImage = data
                        pickerItem = nil
                    }
                }
                TextField("输入你的问题…", text: $viewModel.draft, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
                Button {
                    viewModel.send()
                } label: {
                    Image(systemName: "paperplane.fill")
                        .padding(8)
                }
                .disabled(
                    (viewModel.draft.trimmingCharacters(in: .whitespaces).isEmpty && viewModel.pendingImage == nil)
                    || viewModel.isStreaming
                )
            }
        }
        .padding()
        .background(.bar)
    }
}
