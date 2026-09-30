import Foundation

extension JSONDecoder {
    /// Rally's datetimes come in two shapes. Columns stored as naive UTC
    /// (`created_at`) serialize with no zone at all — `2026-09-30T14:05:00.123456` —
    /// and anything passed through `ensure_utc` carries one. A decoder that only
    /// knows the second reads every `created_at` as a failure.
    public static var rally: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let text = try decoder.singleValueContainer().decode(String.self)
            guard let date = RallyDate.parse(text) else {
                throw DecodingError.dataCorrupted(
                    .init(codingPath: decoder.codingPath, debugDescription: "Unrecognized date: \(text)"))
            }
            return date
        }
        return decoder
    }
}

public enum RallyDate {
    public static func parse(_ text: String) -> Date? {
        let hasZone = text.hasSuffix("Z") || text.range(of: #"[+-]\d{2}:?\d{2}$"#, options: .regularExpression) != nil
        let source = hasZone ? text : text + "Z" // naive means UTC

        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFraction.date(from: source) { return date }

        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        if let date = plain.date(from: source) { return date }

        // Microseconds (six digits) are not always accepted by the formatter above.
        if let dot = source.firstIndex(of: "."),
           let end = source.index(dot, offsetBy: 1, limitedBy: source.endIndex) {
            let digits = source[end...].prefix { $0.isNumber }
            let rest = source[source.index(end, offsetBy: digits.count)...]
            let trimmed = String(source[..<dot]) + "." + digits.prefix(3) + rest
            return withFraction.date(from: trimmed)
        }
        return nil
    }
}
