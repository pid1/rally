import Foundation
import Testing
@testable import RallyKit

/// The design tokens are copies, so these fail the moment a copy drifts from
/// the server's own source of truth. `test_member_colors.py` does the same for
/// the stylesheet on the Python side.
struct DesignTokenTests {
    private static let repo = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

    private func read(_ path: String) throws -> String {
        try String(contentsOf: Self.repo.appendingPathComponent(path), encoding: .utf8)
    }

    @Test func memberPaletteMatchesMemberColorsPy() throws {
        let source = try read("src/rally/member_colors.py")
        let pattern = #"MemberColor\(value="(#[0-9a-fA-F]{6})", label="([^"]+)""#
        let regex = try NSRegularExpression(pattern: pattern)
        let range = NSRange(source.startIndex..., in: source)
        let server = regex.matches(in: source, range: range).map { match -> RallyDesign.MemberColor in
            let hex = String(source[Range(match.range(at: 1), in: source)!])
            let label = String(source[Range(match.range(at: 2), in: source)!])
            return .init(hex: hex, label: label)
        }
        #expect(server.count == 5)
        #expect(RallyDesign.memberPalette == server)
    }

    @Test func inkAndSurfaceTokensMatchTheStylesheet() throws {
        let css = try read("static/styles.css")
        let names = [
            "ink": "--ink", "inkMuted": "--ink-muted", "inkSubtle": "--ink-subtle",
            "rule": "--rule", "ruleSubtle": "--rule-subtle", "surface": "--surface",
            "surfaceSunken": "--surface-sunken", "stateDue": "--state-due", "stateOverdue": "--state-overdue",
        ]
        for (key, token) in names {
            let pattern = "\(NSRegularExpression.escapedPattern(for: token)):\\s*(#[0-9a-fA-F]{6})"
            let match = try #require(css.range(of: pattern, options: .regularExpression), "\(token) not found")
            let value = String(css[match]).split(separator: ":").last!.trimmingCharacters(in: .whitespaces)
            #expect(RallyDesign.hex[key]?.lowercased() == value.lowercased(), "\(key) drifted from \(token)")
        }
    }

    @Test func spacingMatchesTheStylesheet() throws {
        let css = try read("static/styles.css")
        for (index, points) in RallyDesign.space.enumerated() {
            let pattern = "--space-\(index + 1):\\s*([0-9.]+)rem"
            let range = try #require(css.range(of: pattern, options: .regularExpression))
            let rem = Double(String(css[range]).split(separator: ":").last!.trimmingCharacters(in: CharacterSet(charactersIn: " rem")))!
            #expect(CGFloat(rem * 16) == points)
        }
    }
}
