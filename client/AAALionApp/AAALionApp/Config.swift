import Foundation

enum Config {
    static let backendURL: URL = {
        if let raw = ProcessInfo.processInfo.environment["PUBLIC_BACKEND_URL"],
           let url = URL(string: raw) {
            return url
        }
        return URL(string: "http://localhost:8000")!
    }()
}
