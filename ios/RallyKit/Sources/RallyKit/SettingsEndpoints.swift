import Foundation

private struct SettingsEnvelope: Decodable, Sendable { let settings: [String: String] }

extension APIClient {
    /// Every install-wide setting, as the flat key/value map the server keeps.
    public func settings() async throws -> [String: String] {
        let envelope: SettingsEnvelope = try await get(.settings)
        return envelope.settings
    }

    /// Bulk upsert. Only the keys given are written.
    public func saveSettings(_ values: [String: String]) async throws {
        let body = try PatchBody().set("settings", Patch.value(values))
        try await perform(.saveSettings, body: body)
    }
}

/// The few install-wide settings the app reads to behave like the web pages do.
public struct InstallSettings: Sendable, Equatable {
    /// "Today" is the install's day, not the phone's: a phone that has travelled
    /// must still agree with the server about which notes and meals are past.
    public var timeZone: TimeZone
    public var defaultMealType: String
    /// Off by default: the AI review is a real LLM call.
    public var prepReviewEnabled: Bool = false

    public static let fallback = InstallSettings(timeZone: .current, defaultMealType: "Dinner")

    public init(timeZone: TimeZone, defaultMealType: String) {
        self.timeZone = timeZone; self.defaultMealType = defaultMealType
    }

    public init(_ values: [String: String]) {
        timeZone = values["local_timezone"].flatMap(TimeZone.init(identifier:)) ?? .current
        let type = values["meal_default_type"] ?? "Dinner"
        defaultMealType = MealLogic.mealTypes.contains(type) ? type : "Dinner"
        prepReviewEnabled = values["prep_review_enabled"] == "true"
    }

    public var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = timeZone
        return c
    }

    /// Today's date in the install's zone, as `YYYY-MM-DD`.
    public func today(now: Date = .now) -> String { DayString.string(now, calendar: calendar) }
}
