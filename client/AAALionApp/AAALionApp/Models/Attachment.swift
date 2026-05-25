import Foundation
import UIKit

/// One staged user attachment in the composer (image OR file). Replaces
/// the R6-era single `pendingImage: Data?`. The composer can hold up to
/// `Attachment.maxCount` items, mirroring ChatGPT / Claude UX: tap "+"
/// repeatedly to mix photos, camera shots, and files; each chip has its
/// own delete button; the bubble renders a grid once sent.
///
/// We carry `data` (raw bytes), `kind` (display affordance), and an
/// optional `filename` so the message bubble can render a file icon +
/// name for non-image kinds.
struct Attachment: Identifiable, Hashable {
    enum Kind: String, Codable {
        case photo   // PhotosPicker
        case camera  // CameraPicker
        case file    // FileImporter (image OR doc)
    }

    /// Cap on total attachments per message. Matches the ChatGPT cap; the
    /// "+" menu greys out and we show "已达上限 / Max reached" once hit.
    static let maxCount: Int = 10

    let id: UUID
    let data: Data
    let kind: Kind
    /// Original filename for `file` kind, nil for photo/camera.
    let filename: String?
    /// MIME hint for the backend image_url part. JPEG for photo/camera;
    /// for `file` we look at the bytes' magic header (PNG / PDF / etc.).
    let mime: String

    init(
        id: UUID = UUID(),
        data: Data,
        kind: Kind,
        filename: String? = nil,
        mime: String = "image/jpeg"
    ) {
        self.id = id
        self.data = data
        self.kind = kind
        self.filename = filename
        self.mime = mime
    }

    /// True if the attachment is renderable as an image (UIImage(data:)).
    /// File attachments may be PDFs or unknown types — we still show them
    /// as a doc-icon chip, but won't try to inline them.
    var isImage: Bool {
        switch kind {
        case .photo, .camera: return true
        case .file: return mime.hasPrefix("image/")
        }
    }
}

extension Attachment {
    /// R8.E.2: downsample-and-recompress an image before it joins the
    /// upload pipeline. iPhone camera JPEGs are ~2-4 MB at 4032×3024;
    /// base64-encoded this balloons each image to ~3-5 MB on the wire,
    /// which is why 3 photos hit the >30 s wall (network upload + LLM
    /// vision processing both scale with payload size).
    ///
    /// Vision LLMs (Claude / GPT-4o / Doubao Vision) process images at
    /// most ~1568×1568 internally — sending bigger is pure waste. We
    /// resize to `maxDim` on the longer edge, re-encode JPEG @ 0.78
    /// quality. Typical 4032×3024 photo shrinks from 2.4 MB to ~120 KB.
    ///
    /// Returns the original bytes unchanged if decoding fails or if
    /// the image is already small enough — defensive against PNG /
    /// HEIC / files-imported-but-not-image cases.
    static func compressForUpload(_ data: Data, maxDim: CGFloat = 1280, quality: CGFloat = 0.78) -> Data {
        guard let image = UIImage(data: data) else { return data }
        let originalSize = image.size
        let longestEdge = max(originalSize.width, originalSize.height)
        // Skip work if already smaller than the cap and reasonably compressed.
        if longestEdge <= maxDim && data.count < 300_000 {
            return data
        }
        let scale = min(1.0, maxDim / longestEdge)
        let newSize = CGSize(
            width: floor(originalSize.width * scale),
            height: floor(originalSize.height * scale)
        )
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1  // we already have absolute target size in points
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(size: newSize, format: format)
        let resized = renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: newSize))
        }
        return resized.jpegData(compressionQuality: quality) ?? data
    }

    /// Best-effort MIME sniff from the first few bytes. Used by the file
    /// importer so PDFs / PNGs land with the right header on the wire.
    static func sniffMIME(from data: Data, fallback: String = "application/octet-stream") -> String {
        let header = data.prefix(8)
        if header.count >= 4 {
            let b = Array(header)
            // JPEG: FF D8 FF
            if b[0] == 0xFF, b[1] == 0xD8, b[2] == 0xFF { return "image/jpeg" }
            // PNG: 89 50 4E 47
            if b[0] == 0x89, b[1] == 0x50, b[2] == 0x4E, b[3] == 0x47 { return "image/png" }
            // GIF: 47 49 46 38
            if b[0] == 0x47, b[1] == 0x49, b[2] == 0x46, b[3] == 0x38 { return "image/gif" }
            // HEIC/HEIF: bytes 4..8 = "ftyp"
            if header.count >= 12 {
                let ftyp = b[4..<8]
                if ftyp.elementsEqual([0x66, 0x74, 0x79, 0x70]) { return "image/heic" }
            }
            // PDF: 25 50 44 46
            if b[0] == 0x25, b[1] == 0x50, b[2] == 0x44, b[3] == 0x46 { return "application/pdf" }
        }
        return fallback
    }
}
