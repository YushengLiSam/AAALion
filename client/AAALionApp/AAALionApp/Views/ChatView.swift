import SwiftUI

struct ChatView: View {
    @Bindable var viewModel: ChatViewModel

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
        HStack(spacing: 8) {
            TextField("输入你的问题…", text: $viewModel.draft, axis: .vertical)
                .lineLimit(1...4)
                .textFieldStyle(.roundedBorder)
            Button {
                viewModel.send()
            } label: {
                Image(systemName: "paperplane.fill")
                    .padding(8)
            }
            .disabled(viewModel.draft.trimmingCharacters(in: .whitespaces).isEmpty || viewModel.isStreaming)
        }
        .padding()
        .background(.bar)
    }
}
