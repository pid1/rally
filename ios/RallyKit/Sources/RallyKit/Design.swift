import SwiftUI

/// Rally's design tokens, mirrored from `static/styles.css` and
/// `src/rally/member_colors.py`. They are copied rather than shared because the
/// app cannot read the server's stylesheet, and `DesignTokenTests` fails if a
/// value here stops matching either source.
public enum RallyDesign {
    public struct MemberColor: Sendable, Equatable {
        public let hex: String
        public let label: String
    }

    /// The closed five-entry member palette, darkest first — the order is the
    /// luminance ladder that keeps members apart on a monochrome panel. Never
    /// add a sixth and never hand a view a raw member color.
    public static let memberPalette: [MemberColor] = [
        .init(hex: "#315277", label: "Ink blue"),
        .init(hex: "#af2c3d", label: "Crimson"),
        .init(hex: "#8859b1", label: "Violet"),
        .init(hex: "#3b8c61", label: "Forest"),
        .init(hex: "#a38b43", label: "Amber"),
    ]

    /// Text and surfaces. `rule` and `ruleSubtle` are hairlines and fail WCAG AA
    /// as text; only `ink`, `inkMuted` and `inkSubtle` may carry words.
    public static let hex: [String: String] = [
        "ink": "#1a1a1a",
        "inkMuted": "#666666",
        "inkSubtle": "#767676",
        "rule": "#cccccc",
        "ruleSubtle": "#e5e5e5",
        "surface": "#ffffff",
        "surfaceSunken": "#f5f5f5",
        "stateDue": "#8a5a06",
        "stateOverdue": "#9b3222",
    ]

    /// Spacing in points: `--space-1` … `--space-8` (rem × 16).
    public static let space: [CGFloat] = [4, 8, 12, 16, 24, 32, 48, 64]

    /// The hit-area floor on a touch screen (`--target-min` on coarse pointers).
    public static let targetMin: CGFloat = 44

    public static func color(_ token: String) -> Color {
        Color(hex: hex[token] ?? "#000000")
    }

    public static func memberColor(hex value: String) -> Color {
        Color(hex: value)
    }
}

extension Color {
    public init(hex: String) {
        let digits = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var value: UInt64 = 0
        Scanner(string: digits).scanHexInt64(&value)
        self.init(
            red: Double((value >> 16) & 0xff) / 255,
            green: Double((value >> 8) & 0xff) / 255,
            blue: Double(value & 0xff) / 255)
    }
}

extension Font {
    /// Rally sets display and body in a serif; the system serif is the native
    /// equivalent and follows Dynamic Type.
    public static func rally(_ style: Font.TextStyle = .body) -> Font {
        .system(style, design: .serif)
    }
}
