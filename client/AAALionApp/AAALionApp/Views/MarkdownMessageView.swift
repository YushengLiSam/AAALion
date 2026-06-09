import SwiftUI

/// R10 — lightweight markdown renderer for assistant messages.
///
/// The LLM replies in markdown (## headings, | comparison tables |, ---
/// rules, - bullets, **bold**). SwiftUI's plain `Text` shows that syntax
/// literally, which looks messy — especially the pipe-and-dash tables.
/// This renders the block elements the model actually emits into real
/// SwiftUI views, while **preserving** the R9.A.3 `[目录✓]` / `[推断?]`
/// provenance coloring inline. Deliberately small — no heavyweight
/// markdown package, just the handful of constructs our prompt produces.
struct MarkdownMessageView: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(Self.parse(text).enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Block rendering

    @ViewBuilder
    private func blockView(_ block: Block) -> some View {
        switch block {
        case .heading(let level, let content):
            Text(Self.styledInline(content))
                .font(level <= 1 ? .system(size: 18, weight: .bold)
                      : level == 2 ? .system(size: 16, weight: .bold)
                      : .system(size: 14, weight: .semibold))
                .padding(.top, 2)
        case .rule:
            Divider().padding(.vertical, 2)
        case .bullets(let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(items.indices, id: \.self) { i in
                    HStack(alignment: .top, spacing: 6) {
                        Text("•").foregroundStyle(Color.appAccent)
                        Text(Self.styledInline(items[i]))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        case .paragraph(let content):
            Text(Self.styledInline(content))
                .fixedSize(horizontal: false, vertical: true)
        case .table(let rows):
            tableView(rows)
        }
    }

    /// Equal-width columns so the table always fits the chat bubble; cells
    /// wrap instead of overflowing. Header row bold, hairline row dividers.
    private func tableView(_ rows: [[String]]) -> some View {
        VStack(spacing: 0) {
            ForEach(rows.indices, id: \.self) { r in
                HStack(alignment: .top, spacing: 8) {
                    ForEach(rows[r].indices, id: \.self) { c in
                        Text(Self.styledInline(rows[r][c]))
                            .font(.system(size: 13, weight: r == 0 ? .semibold : .regular))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(.vertical, 6)
                .padding(.horizontal, 4)
                if r == 0 {
                    Divider()
                } else if r < rows.count - 1 {
                    Divider().opacity(0.35)
                }
            }
        }
        .background(Color.appAccentMuted.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.appBorder, lineWidth: 0.5))
    }

    // MARK: - Inline: **bold** via AttributedString markdown + marker coloring

    static func styledInline(_ s: String) -> AttributedString {
        var attr: AttributedString
        if let md = try? AttributedString(
            markdown: s,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            attr = md
        } else {
            attr = AttributedString(s)
        }
        // R9.A.3 — provenance markers (R12: English variants too, since the
        // assistant's reply language is user-selectable).
        color(&attr, marker: "[目录✓]", to: .green)
        color(&attr, marker: "[推断?]", to: .orange)
        color(&attr, marker: "[catalog✓]", to: .green)
        color(&attr, marker: "[inferred?]", to: .orange)
        return attr
    }

    private static func color(_ attr: inout AttributedString, marker: String, to c: Color) {
        var search = attr.startIndex..<attr.endIndex
        while let range = attr[search].range(of: marker) {
            attr[range].foregroundColor = c
            attr[range].font = .system(size: 11, weight: .bold)
            search = range.upperBound..<attr.endIndex
        }
    }

    // MARK: - Block parsing

    enum Block {
        case heading(Int, String)
        case paragraph(String)
        case bullets([String])
        case table([[String]])
        case rule
    }

    static func parse(_ text: String) -> [Block] {
        var blocks: [Block] = []
        let lines = text.components(separatedBy: "\n")
        var i = 0
        func t(_ n: Int) -> String { lines[n].trimmingCharacters(in: .whitespaces) }
        while i < lines.count {
            let line = t(i)
            if line.isEmpty { i += 1; continue }

            // Table: consecutive lines starting with "|".
            if line.hasPrefix("|") {
                var tbl: [String] = []
                while i < lines.count && t(i).hasPrefix("|") { tbl.append(t(i)); i += 1 }
                if let parsed = parseTable(tbl) { blocks.append(.table(parsed)) }
                continue
            }
            // Heading.
            if line.hasPrefix("#") {
                let level = line.prefix(while: { $0 == "#" }).count
                let content = line.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                blocks.append(.heading(min(level, 3), content))
                i += 1; continue
            }
            // Horizontal rule.
            if line == "---" || line == "***" || line == "___" {
                blocks.append(.rule); i += 1; continue
            }
            // Bullet list.
            if line.hasPrefix("- ") || line.hasPrefix("* ") {
                var items: [String] = []
                while i < lines.count, t(i).hasPrefix("- ") || t(i).hasPrefix("* ") {
                    items.append(String(t(i).dropFirst(2))); i += 1
                }
                blocks.append(.bullets(items)); continue
            }
            // Paragraph (collect until a blank line or a block starter).
            var para: [String] = []
            while i < lines.count {
                let l = t(i)
                if l.isEmpty || l.hasPrefix("|") || l.hasPrefix("#")
                    || l.hasPrefix("- ") || l.hasPrefix("* ") || l == "---" { break }
                para.append(l); i += 1
            }
            if !para.isEmpty { blocks.append(.paragraph(para.joined(separator: "\n"))) }
        }
        return blocks
    }

    /// Split markdown table lines into rows; drops the `|---|---|` separator.
    private static func parseTable(_ lines: [String]) -> [[String]]? {
        var rows: [[String]] = []
        for line in lines {
            var cells = line.split(separator: "|", omittingEmptySubsequences: false)
                .map { $0.trimmingCharacters(in: .whitespaces) }
            if cells.first == "" { cells.removeFirst() }
            if cells.last == "" { cells.removeLast() }
            if cells.isEmpty { continue }
            // Separator row: every cell is only dashes/colons.
            let isSeparator = cells.allSatisfy { !$0.isEmpty && $0.allSatisfy { $0 == "-" || $0 == ":" } }
            if isSeparator { continue }
            rows.append(cells)
        }
        return rows.isEmpty ? nil : rows
    }
}
