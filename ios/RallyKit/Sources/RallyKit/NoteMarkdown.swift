import Foundation

/// A Daily Note's markdown, read the way the server's renderer reads it.
///
/// That renderer is deliberately tiny — bold, italic, bullet and numbered lists,
/// and a line break for every Enter — and this mirrors it instead of pulling in a
/// full markdown engine: anything the server would not turn into markup (headings,
/// links, code) must not turn into markup here either. Inline styling is left to
/// `AttributedString`, which understands `**bold**` and `*italic*`.
public enum NoteBlock: Equatable, Sendable {
    case paragraph(String)
    case bullet(String)
    case numbered(Int, String)
}

public enum NoteMarkdown {
    public static func blocks(_ body: String) -> [NoteBlock] {
        body.replacingOccurrences(of: "\r\n", with: "\n")
            .split(separator: "\n", omittingEmptySubsequences: false)
            .compactMap { raw -> NoteBlock? in
                let line = String(raw)
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.isEmpty { return nil }
                if let rest = stripBullet(trimmed) { return .bullet(rest) }
                if let (n, rest) = stripNumber(trimmed) { return .numbered(n, rest) }
                return .paragraph(trimmed)
            }
    }

    /// `- item`, `* item` or `+ item`. A line that merely *starts* with bold
    /// (`**Pizza**`) is not a bullet: a bullet marker is followed by a space.
    private static func stripBullet(_ line: String) -> String? {
        guard let first = line.first, "-*+".contains(first), line.dropFirst().first == " " else { return nil }
        return String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
    }

    private static func stripNumber(_ line: String) -> (Int, String)? {
        let digits = line.prefix { $0.isNumber }
        guard !digits.isEmpty, digits.count <= 9, let n = Int(digits) else { return nil }
        let after = line.dropFirst(digits.count)
        guard let mark = after.first, mark == "." || mark == ")", after.dropFirst().first == " " else { return nil }
        return (n, String(after.dropFirst(2)).trimmingCharacters(in: .whitespaces))
    }

    /// Bold and italic only. Anything else that `AttributedString` would style —
    /// links, code — is shown as the characters that were typed.
    public static func inline(_ text: String) -> AttributedString {
        let safe = text.replacingOccurrences(of: "[", with: "\\[").replacingOccurrences(of: "`", with: "\\`")
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnly)
        guard var styled = try? AttributedString(markdown: safe, options: options) else { return AttributedString(text) }
        // Apple's parser auto-links bare URLs; the server's renderer does not.
        for run in styled.runs where run.link != nil { styled[run.range].link = nil }
        return styled
    }
}
