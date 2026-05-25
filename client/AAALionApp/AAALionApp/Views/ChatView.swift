import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

struct ChatView: View {
    @Bindable var viewModel: ChatViewModel
    @State private var cart = CartStore.shared
    @State private var pickerItem: PhotosPickerItem?
    @State private var showCamera = false
    @State private var showFileImporter = false
    @State private var showSettings = false
    @State private var showCart = false
    @FocusState private var inputFocused: Bool

    // R8.D: dev-mode toggle. Long-press the gear icon for 1.5 s to flip
    // this — that unlocks the backend-URL editor in Settings. Default
    // OFF so users never see infra config.
    @AppStorage("lionpick.devMode") private var devMode: Bool = false
    @State private var devModeToast: String?

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
            .overlay(alignment: .top) {
                if let toast = devModeToast {
                    Text(toast)
                        .font(.appCaption)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(.top, 8)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 14) {
                        Button {
                            showCart = true
                        } label: {
                            ZStack(alignment: .topTrailing) {
                                Image(systemName: "cart")
                                if cart.totalQuantity > 0 {
                                    Text("\(cart.totalQuantity)")
                                        .font(.system(size: 10, weight: .bold, design: .rounded))
                                        .foregroundStyle(.white)
                                        .padding(.horizontal, 5)
                                        .padding(.vertical, 1)
                                        .background(Color.appAccent)
                                        .clipShape(Capsule())
                                        .offset(x: 10, y: -8)
                                }
                            }
                        }
                        .accessibilityLabel("Cart")
                        Button {
                            showSettings = true
                        } label: {
                            Image(systemName: devMode ? "gearshape.fill" : "gearshape")
                                .foregroundStyle(devMode ? Color.appAccent : .primary)
                        }
                        .accessibilityLabel("Settings")
                        // Long-press 1.5 s to toggle developer mode (exposes
                        // the backend-URL editor in Settings).
                        .simultaneousGesture(
                            LongPressGesture(minimumDuration: 1.5)
                                .onEnded { _ in
                                    devMode.toggle()
                                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                                    devModeToast = devMode ? "开发者模式已开启 / Dev mode ON" : "开发者模式已关闭 / Dev mode OFF"
                                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.8) {
                                        withAnimation { devModeToast = nil }
                                    }
                                }
                        )
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .sheet(isPresented: $showCart) {
                CartSheet(cart: cart)
            }
            .onChange(of: viewModel.cartIntent) { _, intent in
                guard let intent else { return }
                switch intent {
                case "add":
                    // Auto-add last assistant's product cards (if any).
                    if let lastAssistant = viewModel.messages.reversed().first(where: { $0.role == .assistant }) {
                        for p in lastAssistant.products {
                            cart.add(p)
                        }
                    }
                case "checkout":
                    showCart = true
                default: break
                }
                viewModel.cartIntent = nil
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
            // Only auto-scroll when a new bubble appears (user just sent
            // a message, or assistant placeholder shows up). Don't scroll
            // on every streaming token — that yanks the user back to the
            // bottom every time they try to scroll up to read history.
            .onChange(of: viewModel.messages.count) { _, _ in
                guard let last = viewModel.messages.last else { return }
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
            // When streaming finishes, do a final scroll so the user lands
            // at the end of the now-complete reply.
            .onChange(of: viewModel.isStreaming) { oldVal, newVal in
                guard oldVal, !newVal, let last = viewModel.messages.last else { return }
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
