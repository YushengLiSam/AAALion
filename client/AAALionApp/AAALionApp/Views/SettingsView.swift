import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var backendURLText: String = ""
    @State private var probeResult: ProbeResult?
    @AppStorage("lionpick.autoTTS") private var autoTTS: Bool = false

    // R8.D: dev-mode gate — long-press the gear icon for 1.5s on ChatView
    // to toggle this. When false (default), the backend-URL editor is
    // hidden so users don't have to think about IPs. Cache panel + Auto-TTS
    // remain visible for everyone.
    @AppStorage("lionpick.devMode") private var devMode: Bool = false

    // R8: cache observability panel (consumes Sam's /cache/stats endpoint).
    @State private var cacheStats: CacheStats?
    @State private var cacheError: String?
    @State private var pollingTask: Task<Void, Never>?

    // R11 — account + my-preferences moved to ProfileView (reached from the
    // chat top-bar avatar). Settings now keeps only dev / cache / speech.

    enum ProbeResult: Equatable {
        case ok(version: String)
        case failed(message: String)
    }

    var body: some View {
        NavigationStack {
            Form {
                if devMode {
                    Section {
                        TextField("https://your-tunnel.trycloudflare.com or http://192.168.0.1:8000", text: $backendURLText)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .disableAutocorrection(true)
                        Button("测试连接 / Test connection") { Task { await probe() } }
                        if let result = probeResult {
                            switch result {
                            case .ok(let version):
                                Label("已连接 / Connected (v\(version))", systemImage: "checkmark.circle.fill")
                                    .foregroundStyle(.green)
                                    .font(.footnote)
                            case .failed(let msg):
                                Label(msg, systemImage: "xmark.circle.fill")
                                    .foregroundStyle(.red)
                                    .font(.footnote)
                            }
                        }
                    } header: {
                        Text("后端地址 / Backend URL  (dev)")
                    } footer: {
                        Text("默认走公网 Cloudflare 隧道,不需要配置 LAN IP。" +
                             "切换为本地后端或换隧道时改这里。\n" +
                             "Default routes through the public Cloudflare Tunnel — no LAN IP setup needed. Change here only to point at a different backend.")
                    }

                    Section {
                        Button("恢复默认 / Reset to default") {
                            backendURLText = Config.defaultBackendURL
                            probeResult = nil
                        }
                        .foregroundStyle(.orange)
                    } footer: {
                        Text("默认 / default: \(Config.defaultBackendURL)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Toggle("自动朗读首段 / Auto-read first paragraph", isOn: $autoTTS)
                } header: {
                    Text("语音 / Speech")
                } footer: {
                    Text("开启后,助手回复的第一段会自动朗读。仍可手动点喇叭重读其他段落。\n" +
                         "When on, the first paragraph of every assistant reply is read aloud automatically.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section {
                    if let stats = cacheStats {
                        cacheStatsView(stats)
                    } else if let err = cacheError {
                        Label(err, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    } else {
                        HStack {
                            ProgressView().controlSize(.small)
                            Text("加载中 / Loading…").font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Button {
                        Task { await refreshCacheStats() }
                    } label: {
                        Label("刷新 / Refresh", systemImage: "arrow.clockwise")
                    }
                } header: {
                    Text("缓存命中率 / Cache hit-rate")
                } footer: {
                    Text("命中率 (hit rate) 越高,意味着更多查询命中缓存、首字延迟越低。\n" +
                         "Higher hit-rate = more queries served from in-memory LRU = lower first-delta latency. " +
                         "Source: `GET /cache/stats`.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

            }
            .navigationTitle("设置 / Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消 / Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存 / Save") {
                        Config.setBackendURL(backendURLText)
                        dismiss()
                    }
                }
            }
            .onAppear {
                backendURLText = Config.backendURL.absoluteString
                Task { await refreshCacheStats() }
                // Auto-poll every 10s while sheet is open.
                pollingTask?.cancel()
                pollingTask = Task {
                    while !Task.isCancelled {
                        try? await Task.sleep(nanoseconds: 10_000_000_000)
                        if Task.isCancelled { break }
                        await refreshCacheStats()
                    }
                }
            }
            .onDisappear {
                pollingTask?.cancel()
                pollingTask = nil
            }
        }
    }

    // MARK: - Cache stats panel (R8 B1)

    @ViewBuilder
    private func cacheStatsView(_ s: CacheStats) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("命中率 / Hit rate").font(.subheadline)
                Spacer()
                Text(String(format: "%.1f%%", s.hitRate * 100))
                    .font(.system(.subheadline, design: .rounded).bold())
                    .foregroundStyle(s.hitRate >= 0.3 ? .green : .orange)
            }
            HStack {
                Text("命中 / Hits").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("\(s.hits)").font(.caption.monospacedDigit())
            }
            HStack {
                Text("未命中 / Misses").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("\(s.misses) (含 \(s.expiredMisses) 过期 / expired)")
                    .font(.caption.monospacedDigit())
            }
            HStack {
                Text("容量 / Capacity").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("\(s.size) / \(s.maxSize)   TTL \(s.ttlSec)s")
                    .font(.caption.monospacedDigit())
            }
            HStack {
                Text("淘汰 / Evictions").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("\(s.evictions)").font(.caption.monospacedDigit())
            }
            HStack {
                Text("Uptime").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text("\(Int(s.uptimeSec))s").font(.caption.monospacedDigit())
            }
            // R10 — second cache layer: the retrieval (hybrid+rerank) memo.
            // This is the one that turns an ~8s cold retrieval into ~0.3s on
            // a repeat, so it's the headline latency win to show at demo.
            if let rHit = s.retrievalCacheHitRate {
                Divider().padding(.vertical, 2)
                HStack {
                    Text("检索缓存命中率 / Retrieval hit rate").font(.caption.bold())
                    Spacer()
                    Text(String(format: "%.1f%%", rHit * 100))
                        .font(.system(.caption, design: .rounded).bold())
                        .foregroundStyle(rHit >= 0.3 ? .green : .orange)
                }
                if let rh = s.retrievalCacheHits, let rm = s.retrievalCacheMisses {
                    HStack {
                        Text("命中/未命中 / Hits·Misses").font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                        Text("\(rh) · \(rm)").font(.caption2.monospacedDigit())
                    }
                }
            }
        }
        .padding(.vertical, 2)
    }

    private func refreshCacheStats() async {
        do {
            let stats = try await CacheStatsService(baseURL: Config.backendURL).fetch()
            cacheStats = stats
            cacheError = nil
        } catch {
            cacheError = "无法获取 / can't fetch: \(error.localizedDescription)"
        }
    }

    private func probe() async {
        guard let base = URL(string: backendURLText) else {
            probeResult = .failed(message: "URL 无效 / invalid URL")
            return
        }
        let url = base.appendingPathComponent("health")
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                probeResult = .failed(message: "HTTP \((response as? HTTPURLResponse)?.statusCode ?? -1)")
                return
            }
            struct Health: Decodable { let status: String; let version: String }
            let health = try JSONDecoder().decode(Health.self, from: data)
            probeResult = .ok(version: health.version)
        } catch {
            probeResult = .failed(message: error.localizedDescription)
        }
    }
}
