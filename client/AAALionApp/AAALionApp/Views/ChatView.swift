import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

struct ChatView: View {
    @Bindable var viewModel: ChatViewModel
    @State private var cart = CartStore.shared
    // R8.E: PhotosPicker selection is now plural — user can pick up to
    // `remaining` images in one go, and the picker stays usable until
    // the message hits `Attachment.maxCount`.
    @State private var pickerItems: [PhotosPickerItem] = []
    // R8.E.1: PhotosPicker is presented via the .photosPicker modifier
    // (NOT inline inside the Menu). The inline form has a known SwiftUI
    // bug where plural-selection bindings don't fire reliably after the
    // host Menu dismisses. The modifier form is the iOS 17+ blessed path
    // and works correctly.
    @State private var showPhotosPicker = false
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
    /// Drives the cart-icon bounce when a new item is added anywhere in
    /// the app. Watches cart.totalQuantity for upward transitions only —
    /// remove / decrement does not animate. R8.F.5.
    @State private var cartIconBounce = false

    // R11 — accounts. The top-bar avatar routes to login (logged out) or
    // the profile page (logged in). Login never gates browse / chat.
    @State private var auth = AuthState.shared
    @State private var showLogin = false
    @State private var showProfile = false
    // First-launch soft prompt: shown once, skippable, then never nags.
    @AppStorage("lionpick.loginPromptShown") private var loginPromptShown = false
    @State private var showLoginPrompt = false
    @State private var pendingLoginAfterPrompt = false

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
                // Proactive repurchase reminder banner — only renders when
                // the server returned ≥ 1 due item. Empty list → no UI at
                // all (no placeholder), so the open-app experience is
                // unchanged for users who have nothing due.
                if !viewModel.repurchaseReminders.isEmpty {
                    RepurchaseBannerView(
                        reminders: viewModel.repurchaseReminders,
                        onReorder: { viewModel.reorderFromReminder($0) },
                        onDismiss: { viewModel.dismissReminder($0) }
                    )
                    .transition(.move(edge: .top).combined(with: .opacity))
                }
                messageList
                composer
            }
            .animation(.easeInOut(duration: 0.2), value: viewModel.repurchaseReminders.count)
            .task {
                viewModel.fetchRepurchaseRemindersIfNeeded()
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
                } else if let cartToast = viewModel.repurchaseToast {
                    // R8.F.2: "已加入购物车" feedback after "再来一单" tap.
                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(Color.appAccent)
                        Text(cartToast)
                            .font(.appCaption)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(.top, 8)
                    .padding(.horizontal, 24)
                    .transition(.move(edge: .top).combined(with: .opacity))
                }
            }
            .animation(.easeOut(duration: 0.2), value: viewModel.repurchaseToast)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 14) {
                        // R11 — account avatar. Logged out → login page;
                        // logged in → the account initial → profile page.
                        Button {
                            if auth.isSignedIn { showProfile = true } else { showLogin = true }
                        } label: {
                            if auth.isSignedIn {
                                Text(profileInitial)
                                    .font(.system(size: 13, weight: .bold, design: .rounded))
                                    .foregroundStyle(.white)
                                    .frame(width: 26, height: 26)
                                    .background(Color.appAccent, in: Circle())
                            } else {
                                Image(systemName: "person.crop.circle")
                            }
                        }
                        .accessibilityLabel(auth.isSignedIn ? "我的账号" : "登录")
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
                                        // The badge gets its own little pop so the count
                                        // change is also obviously animated.
                                        .scaleEffect(cartIconBounce ? 1.25 : 1.0)
                                }
                            }
                            // R8.F.5: spring-scale the whole cart cluster when a new
                            // item lands. Provides feedback even if the user added from
                            // ProductDetailView (where they can't see the button morph
                            // because they're scrolled away) or from the repurchase
                            // banner's "再来一单".
                            .scaleEffect(cartIconBounce ? 1.18 : 1.0)
                            .animation(.spring(response: 0.32, dampingFraction: 0.55), value: cartIconBounce)
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
            // R11 — account flows. Login is a branded full-screen page;
            // the profile page is a sheet.
            .fullScreenCover(isPresented: $showLogin) {
                LoginView()
            }
            .sheet(isPresented: $showProfile) {
                ProfileView()
            }
            // First-launch soft prompt (once, skippable). Presenting the
            // login cover is deferred to the prompt's onDismiss so the
            // sheet→cover hand-off doesn't collide.
            .sheet(isPresented: $showLoginPrompt, onDismiss: {
                if pendingLoginAfterPrompt {
                    pendingLoginAfterPrompt = false
                    showLogin = true
                }
            }) {
                LoginPromptCard(
                    onLogin: { pendingLoginAfterPrompt = true; showLoginPrompt = false },
                    onSkip: { showLoginPrompt = false }
                )
                .presentationDetents([.height(360)])
                .presentationDragIndicator(.visible)
            }
            // R8.F.5: bounce the cart toolbar icon whenever totalQuantity
            // *increases* (item added from anywhere — chat, ProductDetail,
            // reminder banner). Decrement / remove doesn't animate.
            .onChange(of: cart.totalQuantity) { oldQty, newQty in
                guard newQty > oldQty else { return }
                cartIconBounce = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
                    cartIconBounce = false
                }
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
                case "clear":
                    // R11.demo-fix — conversational "把购物车清空". Drop every
                    // line and toast, so the cart-management demo works by voice.
                    if !cart.items.isEmpty {
                        cart.clear()
                        viewModel.repurchaseToast = "已清空购物车"
                        Task { @MainActor in
                            try? await Task.sleep(for: .seconds(1.6))
                            if viewModel.repurchaseToast == "已清空购物车" {
                                viewModel.repurchaseToast = nil
                            }
                        }
                    }
                case "remove":
                    // R10 — conversational delete "删掉第二个". index is
                    // 1-based; -1 means "last". Convert to a 0-based cart
                    // position and remove that line, with a toast.
                    if let ord = viewModel.cartIntentIndex, !cart.items.isEmpty {
                        let pos = ord == -1 ? cart.items.count - 1 : ord - 1
                        if cart.items.indices.contains(pos) {
                            let removed = cart.items[pos]
                            cart.remove(productId: removed.productId)
                            viewModel.repurchaseToast = "已删除 · \(removed.title)"
                            Task { @MainActor in
                                try? await Task.sleep(for: .seconds(1.6))
                                if viewModel.repurchaseToast?.contains(removed.title) == true {
                                    viewModel.repurchaseToast = nil
                                }
                            }
                        }
                    }
                case "set_quantity":
                    // R10 #4.1⭐⭐ — conversational quantity "把数量改成2".
                    // index 1-based (-1 = last); quantity is the target count.
                    if let qty = viewModel.cartIntentQuantity, !cart.items.isEmpty {
                        let ord = viewModel.cartIntentIndex ?? -1
                        let pos = ord == -1 ? cart.items.count - 1 : ord - 1
                        if cart.items.indices.contains(pos) {
                            let item = cart.items[pos]
                            cart.setQuantity(productId: item.productId, quantity: qty)
                            let title = item.title
                            viewModel.repurchaseToast = qty <= 0
                                ? "已删除 · \(title)"
                                : "数量已改为 \(qty) · \(title)"
                            Task { @MainActor in
                                try? await Task.sleep(for: .seconds(1.6))
                                if viewModel.repurchaseToast?.contains(title) == true {
                                    viewModel.repurchaseToast = nil
                                }
                            }
                        }
                    }
                default: break
                }
                viewModel.cartIntent = nil
                viewModel.cartIntentIndex = nil
                viewModel.cartIntentQuantity = nil
            }
            .sheet(isPresented: $showCamera) {
                CameraPicker { data in
                    // R8.E: append (not replace) — user can tap camera
                    // repeatedly until the 10-attachment cap. R8.E.2:
                    // downsample on capture so the 4032×3024 iPhone JPEG
                    // doesn't bloat the request payload.
                    let compressed = Attachment.compressForUpload(data)
                    viewModel.appendAttachment(.init(data: compressed, kind: .camera))
                    showCamera = false
                }
                .ignoresSafeArea()
            }
            // R8.E.1: modifier-form PhotosPicker. The host Menu just sets
            // `showPhotosPicker = true`; the picker sheet itself is owned
            // by the NavigationStack so the binding survives the Menu
            // dismissing.
            .photosPicker(
                isPresented: $showPhotosPicker,
                selection: $pickerItems,
                maxSelectionCount: max(1, viewModel.remainingAttachmentSlots),
                selectionBehavior: .ordered,
                matching: .images,
                photoLibrary: .shared()
            )
            .onChange(of: pickerItems) { _, newItems in
                guard !newItems.isEmpty else { return }
                Task { @MainActor in
                    for item in newItems {
                        guard viewModel.remainingAttachmentSlots > 0 else { break }
                        guard let data = try? await item.loadTransferable(type: Data.self) else { continue }
                        // R8.E.2: downsample BEFORE storing so the chip
                        // preview and the wire payload both use the
                        // smaller bytes — keeps multi-image requests
                        // under the >30 s wall.
                        let compressed = Attachment.compressForUpload(data)
                        viewModel.appendAttachment(.init(data: compressed, kind: .photo))
                    }
                    // Defer the clear so iOS finishes its presentation
                    // animation before the binding flips back to empty.
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                        pickerItems = []
                    }
                }
            }
            .fileImporter(
                isPresented: $showFileImporter,
                allowedContentTypes: [.image, .jpeg, .png, .heic, .pdf],
                allowsMultipleSelection: true
            ) { result in
                Task { @MainActor in
                    switch result {
                    case .failure(let error):
                        viewModel.errorMessage = "文件选择失败 / File pick failed: \(error.localizedDescription)"
                    case .success(let urls):
                        guard !urls.isEmpty else {
                            viewModel.errorMessage = "未选中任何文件 / No file selected"
                            return
                        }
                        // R8.E: iterate all picked files, respecting the
                        // remaining-slots cap. NSFileCoordinator still
                        // needed per-file for iCloud-Drive lazy downloads.
                        for url in urls {
                            guard viewModel.remainingAttachmentSlots > 0 else { break }
                            let access = url.startAccessingSecurityScopedResource()
                            defer { if access { url.stopAccessingSecurityScopedResource() } }
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
                                continue
                            }
                            if let readError {
                                viewModel.errorMessage = "文件读取失败 / \(readError.localizedDescription)"
                                continue
                            }
                            guard let data = loaded, !data.isEmpty else {
                                viewModel.errorMessage = "文件为空 / file empty: \(url.lastPathComponent)"
                                continue
                            }
                            let mime = Attachment.sniffMIME(from: data)
                            // R8.E.2: if the file is an image, downsample
                            // it so the upload payload stays bounded.
                            // Non-image files (PDF etc.) pass through.
                            let finalData = mime.hasPrefix("image/")
                                ? Attachment.compressForUpload(data)
                                : data
                            let finalMIME = mime.hasPrefix("image/") ? "image/jpeg" : mime
                            viewModel.appendAttachment(.init(
                                data: finalData,
                                kind: .file,
                                filename: url.lastPathComponent,
                                mime: finalMIME
                            ))
                        }
                    }
                }
            }
            .task {
                viewModel.runScriptedQueryIfAny()
            }
            .task {
                // R11 — first-launch soft login prompt. The flag is set
                // immediately so it shows at most once; only when not
                // already signed in, after a short beat so it doesn't pop
                // on a cold-launch frame.
                guard !loginPromptShown, !auth.isSignedIn else { return }
                loginPromptShown = true
                try? await Task.sleep(for: .seconds(0.9))
                if !auth.isSignedIn { showLoginPrompt = true }
            }
        }
    }

    private var profileInitial: String {
        let n = auth.displayName.trimmingCharacters(in: .whitespaces)
        guard let first = n.first else { return "🦁" }
        return String(first).uppercased()
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
            // R10 #5 — 主动反问 quick-reply chips. When the agent asks a
            // clarifying question, render the suggested answers as tappable
            // capsules just above the composer; tapping one sends it.
            if !viewModel.clarifyChips.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(viewModel.clarifyChips, id: \.self) { chip in
                            Button {
                                viewModel.sendChip(chip)
                            } label: {
                                Text(chip)
                                    .font(.system(size: 13, weight: .medium))
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 7)
                                    .background(Color.appAccent.opacity(0.12), in: Capsule())
                                    .foregroundStyle(Color.appAccent)
                                    .overlay(Capsule().stroke(Color.appAccent.opacity(0.35), lineWidth: 0.5))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 4)
                }
                .transition(.opacity)
            }
            // R8.E-VOICE: "正在听..." hint mirrors the ChatGPT/Claude mic
            // affordance. Combined with the SpeechService 1.8s idle-timer,
            // this tells the user the app is actively listening and that
            // they can simply stop talking — the mic will release itself.
            if viewModel.isRecording {
                HStack(spacing: 6) {
                    Circle()
                        .fill(Color.red)
                        .frame(width: 8, height: 8)
                    Text("正在听… / Listening")
                        .font(.caption)
                        .foregroundStyle(Color.red)
                    Spacer()
                    Text("停顿 ~2 秒自动结束 / auto-stops on silence")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 4)
                .transition(.opacity)
            }
            attachmentPreviewRow
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
                    (viewModel.draft.trimmingCharacters(in: .whitespaces).isEmpty && viewModel.pendingAttachments.isEmpty)
                    || viewModel.isStreaming
                )
            }
        }
        .padding()
        .background(.bar)
    }

    /// Horizontally-scrolling row of staged attachment chips with delete
    /// buttons. Hidden when there are no staged items. Each chip is 64x64
    /// (image thumbnail or doc-icon + filename) with a small `x` button
    /// at top-right. Mirrors ChatGPT's composer.
    @ViewBuilder
    private var attachmentPreviewRow: some View {
        if !viewModel.pendingAttachments.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(viewModel.pendingAttachments) { attachment in
                            attachmentChip(attachment)
                        }
                    }
                    .padding(.vertical, 2)
                }
                HStack {
                    Spacer()
                    Text(viewModel.remainingAttachmentSlots == 0
                         ? "已达上限 \(Attachment.maxCount) / Max reached"
                         : "\(viewModel.pendingAttachments.count) / \(Attachment.maxCount)")
                        .font(.caption2)
                        .foregroundStyle(viewModel.remainingAttachmentSlots == 0 ? Color.red : .secondary)
                }
            }
        }
    }

    @ViewBuilder
    private func attachmentChip(_ attachment: Attachment) -> some View {
        ZStack(alignment: .topTrailing) {
            Group {
                if attachment.isImage, let uiImage = UIImage(data: attachment.data) {
                    Image(uiImage: uiImage)
                        .resizable()
                        .scaledToFill()
                        .frame(width: 64, height: 64)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                } else {
                    VStack(spacing: 2) {
                        Image(systemName: "doc.fill")
                            .font(.system(size: 22))
                            .foregroundStyle(Color.appAccent)
                        Text(attachment.filename ?? "文件")
                            .font(.system(size: 9))
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .padding(.horizontal, 2)
                    }
                    .frame(width: 64, height: 64)
                    .background(Color.gray.opacity(0.10))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
            Button {
                viewModel.removeAttachment(id: attachment.id)
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .symbolRenderingMode(.palette)
                    .foregroundStyle(.white, Color.black.opacity(0.6))
                    .font(.system(size: 18))
                    .padding(2)
            }
            .accessibilityLabel("Remove attachment")
        }
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
            // R8.E.1: trigger the photo picker via a Button that flips
            // a presentation flag — the actual .photosPicker modifier
            // is applied on the NavigationStack so the sheet lives
            // OUTSIDE the Menu's lifecycle. This makes plural-selection
            // bindings fire reliably.
            Button {
                showPhotosPicker = true
            } label: {
                Label("照片库 / Photo library", systemImage: "photo.on.rectangle")
            }
            .disabled(viewModel.remainingAttachmentSlots == 0)
            if UIImagePickerController.isSourceTypeAvailable(.camera) {
                Button {
                    showCamera = true
                } label: {
                    Label("相机 / Camera", systemImage: "camera")
                }
                .disabled(viewModel.remainingAttachmentSlots == 0)
            }
            Button {
                showFileImporter = true
            } label: {
                Label("文件 / Files", systemImage: "folder")
            }
            .disabled(viewModel.remainingAttachmentSlots == 0)
        } label: {
            Image(systemName: "plus.circle.fill")
                .font(.title2)
                .foregroundStyle(viewModel.remainingAttachmentSlots == 0 ? Color.gray : Color.accentColor)
        }
        .disabled(viewModel.remainingAttachmentSlots == 0)
    }
}
