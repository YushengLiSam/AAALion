import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

struct ChatView: View {
    @Bindable var viewModel: ChatViewModel
    @State private var pickerItem: PhotosPickerItem?
    @State private var showCamera = false
    @State private var showFileImporter = false
    @State private var showSettings = false
    @FocusState private var inputFocused: Bool

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
            .navigationTitle("狮选")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel("Settings")
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .sheet(isPresented: $showCamera) {
                CameraPicker { data in
                    viewModel.pendingImage = data
                    showCamera = false
                }
                .ignoresSafeArea()
            }
            .fileImporter(
                isPresented: $showFileImporter,
                allowedContentTypes: [.image, .jpeg, .png, .heic],
                allowsMultipleSelection: false
            ) { result in
                Task { @MainActor in
                    switch result {
                    case .failure(let error):
                        viewModel.errorMessage = "文件选择失败 / File pick failed: \(error.localizedDescription)"
                    case .success(let urls):
                        guard let url = urls.first else {
                            viewModel.errorMessage = "未选中任何文件 / No file selected"
                            return
                        }
                        let access = url.startAccessingSecurityScopedResource()
                        defer { if access { url.stopAccessingSecurityScopedResource() } }
                        // NSFileCoordinator handles iCloud-Drive files that aren't yet downloaded.
                        let coordinator = NSFileCoordinator()
                        var coordError: NSError?
                        var loaded: Data?
                        var readError: Error?
                        coordinator.coordinate(readingItemAt: url, options: .withoutChanges, error: &coordError) { effectiveURL in
                            do {
                                loaded = try Data(contentsOf: effectiveURL)
                            } catch {
                                readError = error
                            }
                        }
                        if let coordError {
                            viewModel.errorMessage = "文件读取失败 / Coordinator: \(coordError.localizedDescription)"
                            return
                        }
                        if let readError {
                            viewModel.errorMessage = "文件读取失败 / \(readError.localizedDescription)"
                            return
                        }
                        if let data = loaded {
                            viewModel.pendingImage = data
                        } else {
                            viewModel.errorMessage = "文件为空 / file empty"
                        }
                    }
                }
            }
            .task {
                viewModel.runScriptedQueryIfAny()
            }
        }
    }

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                if viewModel.messages.isEmpty {
                    emptyState
                        .padding(.top, 80)
                }
                LazyVStack(spacing: 12) {
                    ForEach(viewModel.messages) { message in
                        MessageBubbleView(
                            message: message,
                            onEdit: { viewModel.editMessage(id: message.id); inputFocused = true },
                            onCopy: { UIPasteboard.general.string = message.text },
                            onSpeak: { viewModel.speakAssistant(text: message.text) }
                        )
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

    private var emptyState: some View {
        VStack(spacing: 12) {
            Text("👋")
                .font(.system(size: 60))
            Text("狮选 LionPick")
                .font(.title2.weight(.semibold))
            Text("试试问：\n推荐一款适合油皮的洗面奶\n或者上传 / 拍一张商品照片")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
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
            HStack(spacing: 10) {
                attachmentMenu
                TextField("输入你的问题…", text: $viewModel.draft, axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
                    .focused($inputFocused)
                micButton
                Button {
                    inputFocused = false
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

    private var micButton: some View {
        Button {
            if viewModel.isRecording {
                viewModel.stopListening()
            } else {
                viewModel.startListening()
            }
        } label: {
            Image(systemName: viewModel.isRecording ? "mic.fill" : "mic")
                .foregroundStyle(viewModel.isRecording ? Color.red : Color.accentColor)
                .font(.title3)
        }
        .accessibilityLabel(viewModel.isRecording ? "Stop voice input" : "Start voice input")
    }

    private var attachmentMenu: some View {
        Menu {
            PhotosPicker(selection: $pickerItem, matching: .images, photoLibrary: .shared()) {
                Label("照片库 / Photo library", systemImage: "photo.on.rectangle")
            }
            if UIImagePickerController.isSourceTypeAvailable(.camera) {
                Button {
                    showCamera = true
                } label: {
                    Label("相机 / Camera", systemImage: "camera")
                }
            }
            Button {
                showFileImporter = true
            } label: {
                Label("文件 / Files", systemImage: "folder")
            }
        } label: {
            Image(systemName: "plus.circle.fill")
                .font(.title2)
                .foregroundStyle(Color.accentColor)
        }
        .onChange(of: pickerItem) { _, newItem in
            Task { @MainActor in
                guard let item = newItem,
                      let data = try? await item.loadTransferable(type: Data.self) else { return }
                viewModel.pendingImage = data
                pickerItem = nil
            }
        }
    }
}
