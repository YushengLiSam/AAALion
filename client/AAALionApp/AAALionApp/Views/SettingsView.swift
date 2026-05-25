import SwiftUI

struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var backendURLText: String = ""
    @State private var probeResult: ProbeResult?
    @AppStorage("lionpick.autoTTS") private var autoTTS: Bool = false

    enum ProbeResult: Equatable {
        case ok(version: String)
        case failed(message: String)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.0.1:8000", text: $backendURLText)
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
                    Text("后端地址 / Backend URL")
                } footer: {
                    Text("Mac 上的服务器地址。同一 Wi-Fi 下用 `ipconfig getifaddr en0` 拿到 LAN IP。" +
                         "\nServer running on the Mac (same Wi-Fi). Get the LAN IP via `ipconfig getifaddr en0`.")
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
            }
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
