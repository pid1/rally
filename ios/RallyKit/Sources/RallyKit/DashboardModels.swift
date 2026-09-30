import Foundation

public struct Note: Codable, Sendable, Identifiable, Equatable, Hashable {
    public let id: Int
    public var date: String      // YYYY-MM-DD
    public var body: String      // markdown as the family typed it
    public let bodyHTML: String  // server-rendered; the app renders `body` natively instead
    public let createdAt: Date
    public let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, date, body
        case bodyHTML = "body_html"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

public struct ScheduleItem: Codable, Sendable, Equatable, Identifiable {
    public let time: String
    public let title: String
    public let notes: String
    public var id: String { "\(time)|\(title)" }
}

public struct StemActivity: Codable, Sendable, Equatable {
    public let idea: String
    public let audience: String
}

public struct StemConcept: Codable, Sendable, Equatable {
    public let title: String
    public let field: String
    public let explanation: String
    public let activities: [StemActivity]
}

public struct Dashboard: Decodable, Sendable, Equatable {
    public let hasSnapshot: Bool
    public let generatedAt: Date?
    public let greeting: String
    public let weatherSummary: String
    public let schedule: [ScheduleItem]
    public let briefing: String
    public let stemConcept: StemConcept?
    public let note: Note?

    enum CodingKeys: String, CodingKey {
        case greeting, schedule, briefing, note
        case hasSnapshot = "has_snapshot"
        case generatedAt = "generated_at"
        case weatherSummary = "weather_summary"
        case stemConcept = "stem_concept"
    }
}

extension APIClient {
    public func dashboard() async throws -> Dashboard { try await get(.dashboard) }
}
