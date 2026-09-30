import Foundation

/// The answer of every "Test connection" button: `{success, message}` or `{success, error}`.
public struct TestResult: Decodable, Sendable, Equatable {
    public let success: Bool
    public let message: String?
    public let error: String?
    public var text: String { (success ? message : error) ?? (success ? "Connected." : "That didn't work.") }
}

// MARK: Family members

/// What the member editor sends. Clearing a Pushover key is a real `null`;
/// `notifications` is a *partial* map — kinds left out keep what they resolve to.
public struct MemberDraft: Sendable, Equatable {
    public var name = ""
    public var color: String?
    public var pushoverUserKey = ""
    public var pushoverDevice = ""
    public var notifications: [String: Bool] = [:]
    public init() {}
    public init(_ m: FamilyMember) {
        name = m.name; color = m.color; pushoverUserKey = m.pushoverUserKey ?? ""; pushoverDevice = m.pushoverDevice ?? ""
        notifications = m.notifications
    }

    func body(creating: Bool) throws -> PatchBody {
        func text(_ s: String) -> String? { let t = s.trimmingCharacters(in: .whitespacesAndNewlines); return t.isEmpty ? nil : t }
        var body = try PatchBody().set("name", Patch.value(name.trimmingCharacters(in: .whitespaces)))
        // Omitting the color lets the server hand out the first unused palette entry (create)
        // or leave the stored one alone (update). It is always one of the closed five.
        if let color { body = try body.set("color", Patch.value(color)) }
        body = try body.set("pushover_user_key", Patch.clearing(text(pushoverUserKey)))
            .set("pushover_device", Patch.clearing(text(pushoverDevice)))
        if !notifications.isEmpty || !creating { body = try body.set("notifications", Patch.value(notifications)) }
        return body
    }
}

public struct NotificationKind: Decodable, Sendable, Identifiable, Equatable {
    public let kind: String
    public let label: String
    public let audience: String
    public let defaultOn: Bool
    public let settingsKey: String?
    public let enabled: Bool
    public let receiving: [String]
    public let muted: [String]
    public let noKey: [String]
    public var id: String { kind }
    enum CodingKeys: String, CodingKey {
        case kind, label, audience, enabled, receiving, muted
        case defaultOn = "default_on", settingsKey = "settings_key", noKey = "no_key"
    }
}

public struct NotificationOverview: Decodable, Sendable, Equatable {
    public let tokenConfigured: Bool
    public let kinds: [NotificationKind]
    enum CodingKeys: String, CodingKey { case kinds, tokenConfigured = "token_configured" }
}

// MARK: Calendars

public struct CalendarRow: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public var label: String
    public var url: String
    public var familyMemberID: Int?
    public var ownerEmail: String?
    public var calType: String
    public var username: String?
    enum CodingKeys: String, CodingKey {
        case id, label, url, username
        case familyMemberID = "family_member_id", ownerEmail = "owner_email", calType = "cal_type"
    }
    public var isNative: Bool { calType == "native" }
    public var typeTitle: String { CalendarDraft.typeTitle(calType) }
}

public struct CalendarDraft: Sendable, Equatable {
    public static let types = ["ics", "caldav_google", "caldav_apple"]
    public static func typeTitle(_ t: String) -> String {
        switch t { case "ics": "ICS feed"; case "caldav_google": "Google (CalDAV)"; case "caldav_apple": "iCloud (CalDAV)"; case "native": "Rally"; default: t }
    }
    public var label = ""
    public var type = "ics"
    public var url = ""
    public var memberID: Int?
    public var ownerEmail = ""
    public var username = ""
    /// Only sent when typed: the server never returns it, so blank means "keep".
    public var password = ""
    public init() {}
    public init(_ c: CalendarRow) {
        label = c.label; type = c.calType; url = c.url; memberID = c.familyMemberID
        ownerEmail = c.ownerEmail ?? ""; username = c.username ?? ""
    }
    public var needsCredentials: Bool { type != "ics" }

    func body() throws -> PatchBody {
        func text(_ s: String) -> String? { let t = s.trimmingCharacters(in: .whitespacesAndNewlines); return t.isEmpty ? nil : t }
        var body = try PatchBody().set("label", Patch.value(label.trimmingCharacters(in: .whitespaces)))
            .set("cal_type", Patch.value(type)).set("url", Patch.value(url.trimmingCharacters(in: .whitespaces)))
            .set("owner_email", Patch.clearing(text(ownerEmail)))
            .set("username", Patch.clearing(needsCredentials ? text(username) : nil))
        if let memberID { body = try body.set("family_member_id", Patch.value(memberID)) }
        if needsCredentials, let p = text(password) { body = try body.set("password", Patch.value(p)) }
        return body
    }
}

// MARK: AI settings and LLM

public struct AISetting: Decodable, Sendable, Equatable {
    public let fieldName: String
    public let value: String
    public let historyID: Int?
    enum CodingKeys: String, CodingKey { case value, fieldName = "field_name", historyID = "history_id" }
}

public struct AIHistoryEntry: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public let value: String
    public let createdAt: Date
    public let lastUsedAt: Date
    enum CodingKeys: String, CodingKey { case id, value, createdAt = "created_at", lastUsedAt = "last_used_at" }
}

public struct AIHistory: Decodable, Sendable, Equatable {
    public let currentHistoryID: Int?
    public let history: [AIHistoryEntry]
    enum CodingKeys: String, CodingKey { case history, currentHistoryID = "current_history_id" }
}

public struct LLMConfig: Decodable, Sendable, Equatable {
    public let provider: String
    public let model: String
    public let maxTokens: Int?
    public let maxTokensMode: String?
    public let historyID: Int?
    enum CodingKeys: String, CodingKey {
        case provider, model
        case maxTokens = "max_tokens", maxTokensMode = "max_tokens_mode", historyID = "history_id"
    }
}

public struct LLMHistoryEntry: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public let provider: String
    public let model: String
    public let maxTokens: Int
    public let maxTokensMode: String
    public let createdAt: Date
    public let lastUsedAt: Date
    enum CodingKeys: String, CodingKey {
        case id, provider, model
        case maxTokens = "max_tokens", maxTokensMode = "max_tokens_mode", createdAt = "created_at", lastUsedAt = "last_used_at"
    }
}

public struct LLMHistory: Decodable, Sendable, Equatable {
    public let currentHistoryID: Int?
    public let history: [LLMHistoryEntry]
    enum CodingKeys: String, CodingKey { case history, currentHistoryID = "current_history_id" }
}

// MARK: Sports

public struct FollowedTeam: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public var provider: String
    public var league: String
    public var teamKey: String?
    public var label: String
    public var radioStation: String?
    public var active: Bool
    enum CodingKeys: String, CodingKey {
        case id, provider, league, label, active
        case teamKey = "team_key", radioStation = "radio_station"
    }
}

public struct TeamDraft: Sendable, Equatable {
    public var provider = "espn"     // espn | mlb
    public var league = ""           // e.g. hockey/nhl, racing/nascar-premier
    public var teamKey = ""          // blank for a racing series
    public var label = ""
    public var radioStation = ""
    public var active = true
    public init() {}
    public init(_ t: FollowedTeam) {
        provider = t.provider; league = t.league; teamKey = t.teamKey ?? ""; label = t.label
        radioStation = t.radioStation ?? ""; active = t.active
    }

    func body() throws -> PatchBody {
        func text(_ s: String) -> String? { let t = s.trimmingCharacters(in: .whitespacesAndNewlines); return t.isEmpty ? nil : t }
        return try PatchBody().set("provider", Patch.value(provider)).set("league", Patch.value(league.trimmingCharacters(in: .whitespaces)))
            .set("team_key", Patch.clearing(text(teamKey))).set("label", Patch.value(label.trimmingCharacters(in: .whitespaces)))
            .set("radio_station", Patch.clearing(text(radioStation))).set("active", Patch.value(active))
    }
}

// MARK: Behavioral preferences

public struct BehaviorChoice: Decodable, Sendable, Identifiable, Equatable { public let value: String; public let label: String; public var id: String { value } }

public struct BehaviorSetting: Decodable, Sendable, Identifiable, Equatable {
    public let key: String
    public let label: String
    public let description: String
    public let choices: [BehaviorChoice]
    public let `default`: String
    public var id: String { key }
}

extension APIClient {
    // Family
    public func createFamilyMember(_ d: MemberDraft) async throws -> FamilyMember { try await send(.createFamilyMember, body: d.body(creating: true)) }
    public func updateFamilyMember(id: Int, _ d: MemberDraft) async throws -> FamilyMember {
        try await send(.updateFamilyMember, ["member_id": String(id)], body: d.body(creating: false))
    }
    public func deleteFamilyMember(id: Int) async throws { try await perform(.deleteFamilyMember, ["member_id": String(id)]) }
    public func testMemberPushover(id: Int) async throws -> TestResult { try await send(.testMemberPushover, ["member_id": String(id)], body: PatchBody()) }

    // Connection tests
    public func testPushover() async throws -> TestResult { try await send(.testPushover, body: PatchBody()) }
    public func testWeather() async throws -> TestResult { try await send(.testWeather, body: PatchBody()) }
    public func testLLM() async throws -> TestResult { try await send(.testLLM, body: PatchBody()) }

    // Calendars
    public func allCalendars() async throws -> [CalendarRow] { try await get(.calendars) }
    public func createCalendar(_ d: CalendarDraft) async throws -> CalendarRow { try await send(.createCalendar, body: d.body()) }
    public func updateCalendar(id: Int, _ d: CalendarDraft) async throws -> CalendarRow { try await send(.updateCalendar, ["cal_id": String(id)], body: d.body()) }
    public func deleteCalendar(id: Int) async throws { try await perform(.deleteCalendar, ["cal_id": String(id)]) }
    public func testCalendar(id: Int) async throws -> TestResult { try await send(.testCalendar, ["cal_id": String(id)], body: PatchBody()) }

    // AI
    public func aiSettings() async throws -> [String: AISetting] { try await get(.aiSettings) }
    public func saveAISetting(field: String, value: String) async throws -> AISetting {
        try await send(.saveAISetting, ["field_name": field], body: PatchBody().set("value", Patch.value(value)))
    }
    public func aiHistory(field: String) async throws -> AIHistory { try await get(.aiHistory, ["field_name": field]) }
    public func aiRollback(field: String, historyID: Int) async throws -> AISetting {
        try await send(.aiRollback, ["field_name": field], body: PatchBody().set("history_id", Patch.value(historyID)))
    }

    // LLM
    public func llmConfig() async throws -> LLMConfig { try await get(.llmConfig) }
    public func saveLLMConfig(provider: String, model: String, maxTokens: Int, mode: String) async throws -> LLMConfig {
        try await send(.saveLLMConfig, body: PatchBody().set("provider", Patch.value(provider)).set("model", Patch.value(model))
            .set("max_tokens", Patch.value(maxTokens)).set("max_tokens_mode", Patch.value(mode)))
    }
    public func llmHistory() async throws -> LLMHistory { try await get(.llmHistory) }
    public func llmRollback(historyID: Int) async throws -> LLMConfig {
        try await send(.llmRollback, body: PatchBody().set("history_id", Patch.value(historyID)))
    }

    // Notifications and preferences
    public func notificationsOverview() async throws -> NotificationOverview { try await get(.notificationsOverview) }
    public func behaviorCatalog() async throws -> [BehaviorSetting] {
        struct Reply: Decodable, Sendable { let settings: [BehaviorSetting] }
        let reply: Reply = try await get(.preferenceCatalog)
        return reply.settings
    }

    // Sports
    public func followedTeams() async throws -> [FollowedTeam] { try await get(.followedTeams) }
    public func createFollowedTeam(_ d: TeamDraft) async throws -> FollowedTeam { try await send(.createFollowedTeam, body: d.body()) }
    public func updateFollowedTeam(id: Int, _ d: TeamDraft) async throws -> FollowedTeam { try await send(.updateFollowedTeam, ["team_id": String(id)], body: d.body()) }
    public func deleteFollowedTeam(id: Int) async throws { try await perform(.deleteFollowedTeam, ["team_id": String(id)]) }
    public func testFollowedTeam(id: Int) async throws -> TestResult { try await send(.testFollowedTeam, ["team_id": String(id)], body: PatchBody()) }
}
