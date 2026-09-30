import Foundation
import SwiftUI
import Observation

/// The install-wide key/value settings and the defaults Rally applies when a key
/// has never been saved. They mirror the server's own, so an untouched install
/// reads the same here as on the web page.
public enum SettingDefaults {
    public static let values: [String: String] = [
        "stem_concept_enabled": "false",
        "shopping_list_in_summary_enabled": "false",
        "shopping_notify_enabled": "false",
        "shopping_notify_settle_minutes": "5",
        "todo_notify_enabled": "true",
        "prep_notify_enabled": "true",
        "prep_notify_time": "08:00",
        "prep_default_remind_days": "14",
        "prep_overdue_in_summary_enabled": "true",
        "prep_review_enabled": "false",
        "sports_watchlist_enabled": "false",
        "meal_default_type": "Dinner",
    ]
}

/// Household settings, edited as a draft. Only keys that actually changed are
/// sent — saving one toggle must not rewrite every other key with the value this
/// screen happened to load.
@MainActor @Observable
public final class HouseholdModel {
    public private(set) var stored: [String: String] = [:]
    public private(set) var draft: [String: String] = [:]
    public private(set) var hasLoaded = false
    public var errorMessage: String?
    public var notice: String?
    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    public func resolved(_ key: String) -> String { stored[key] ?? SettingDefaults.values[key] ?? "" }
    public func value(_ key: String) -> String { draft[key] ?? resolved(key) }
    public func flag(_ key: String) -> Bool { value(key) == "true" }

    public func set(_ key: String, _ newValue: String) {
        if newValue == resolved(key) { draft[key] = nil } else { draft[key] = newValue }
    }

    public func setFlag(_ key: String, _ on: Bool) { set(key, on ? "true" : "false") }
    public var changes: [String: String] { draft }
    public var hasChanges: Bool { !draft.isEmpty }

    public func load() async {
        do { stored = try await client.settings(); hasLoaded = true }
        catch { if !hasLoaded { errorMessage = error.localizedDescription } }
    }

    @discardableResult
    public func save() async -> Bool {
        guard hasChanges else { return true }
        do {
            try await client.saveSettings(draft)
            stored.merge(draft) { _, new in new }
            draft = [:]
            notice = "Saved."
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    /// Save first, then ask the server to verify — the same order the web page uses,
    /// so the test runs against what was just entered.
    public func saveThenTest(_ test: () async throws -> TestResult) async -> String? {
        guard await save() else { return nil }
        do { return try await test().text } catch { return error.localizedDescription }
    }
}

/// Admin lists (family, calendars, sports): load, save, delete, each reporting
/// failure in words rather than throwing into the screen.
@MainActor @Observable
public final class FamilyAdminModel {
    public private(set) var notificationKinds: [NotificationKind] = []
    public private(set) var tokenConfigured = true
    public var errorMessage: String?
    private let client: APIClient
    public init(client: APIClient) { self.client = client }

    public func loadKinds() async {
        if let overview = try? await client.notificationsOverview() { notificationKinds = overview.kinds; tokenConfigured = overview.tokenConfigured }
    }

    public func save(_ draft: MemberDraft, editing member: FamilyMember?) async -> Bool {
        do {
            if let member { _ = try await client.updateFamilyMember(id: member.id, draft) } else { _ = try await client.createFamilyMember(draft) }
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    public func delete(_ member: FamilyMember) async -> Bool {
        do { try await client.deleteFamilyMember(id: member.id); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    public func testPush(_ member: FamilyMember) async -> String {
        do { return try await client.testMemberPushover(id: member.id).text } catch { return error.localizedDescription }
    }
}

@MainActor @Observable
public final class CalendarAdminModel {
    public private(set) var calendars: [CalendarRow] = []
    public private(set) var hasLoaded = false
    public var errorMessage: String?
    private let client: APIClient
    public init(client: APIClient) { self.client = client }

    public func load() async {
        do { calendars = try await client.allCalendars(); hasLoaded = true }
        catch { if !hasLoaded { errorMessage = error.localizedDescription } }
    }

    public func save(_ draft: CalendarDraft, editing row: CalendarRow?) async -> Bool {
        do {
            if let row { _ = try await client.updateCalendar(id: row.id, draft) } else { _ = try await client.createCalendar(draft) }
            await load(); return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    public func delete(_ row: CalendarRow) async -> Bool {
        do { try await client.deleteCalendar(id: row.id); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    public func test(_ row: CalendarRow) async -> String {
        do { return try await client.testCalendar(id: row.id).text } catch { return error.localizedDescription }
    }
}

@MainActor @Observable
public final class TeamsModel {
    public private(set) var teams: [FollowedTeam] = []
    public private(set) var hasLoaded = false
    public var errorMessage: String?
    private let client: APIClient
    public init(client: APIClient) { self.client = client }

    public func load() async {
        do { teams = try await client.followedTeams(); hasLoaded = true }
        catch { if !hasLoaded { errorMessage = error.localizedDescription } }
    }

    public func save(_ draft: TeamDraft, editing team: FollowedTeam?) async -> Bool {
        do {
            if let team { _ = try await client.updateFollowedTeam(id: team.id, draft) } else { _ = try await client.createFollowedTeam(draft) }
            await load(); return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    public func delete(_ team: FollowedTeam) async -> Bool {
        do { try await client.deleteFollowedTeam(id: team.id); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    public func test(_ team: FollowedTeam) async -> String {
        do { return try await client.testFollowedTeam(id: team.id).text } catch { return error.localizedDescription }
    }
}

/// Per-person, per-device behavioral settings: one dropdown per member per setting,
/// saved the moment it changes, carrying only the setting that moved.
@MainActor @Observable
public final class PersonalDefaultsModel {
    public private(set) var catalog: [BehaviorSetting] = []
    /// member id → setting key → value (resolved: defaults already filled in).
    public private(set) var answers: [String: [String: String]] = [:]
    public private(set) var devices: [Device] = []
    public var errorMessage: String?
    private let client: APIClient
    private let deviceID: String
    public init(client: APIClient, deviceID: String) { self.client = client; self.deviceID = deviceID }

    public func load() async {
        do {
            async let c = client.behaviorCatalog()
            async let a = client.devicePreferences(deviceID: deviceID)
            async let d = client.devices()
            (catalog, answers, devices) = try await (c, a, d)
        } catch { errorMessage = error.localizedDescription }
    }

    public func answer(member: Int, key: String) -> String {
        answers[String(member)]?[key] ?? catalog.first { $0.key == key }?.default ?? "auto"
    }

    public func choose(_ value: String, member: Int, key: String) async {
        let previous = answers[String(member)]?[key]
        answers[String(member), default: [:]][key] = value
        do { try await client.saveMemberPreferences(deviceID: deviceID, memberID: member, [key: value]) }
        catch { answers[String(member), default: [:]][key] = previous; errorMessage = error.localizedDescription }
    }

    /// Forget a device and every answer stored for it.
    public func forget(_ device: Device) async {
        do { try await client.forgetDevice(id: device.id); devices.removeAll { $0.id == device.id } }
        catch { errorMessage = error.localizedDescription }
    }
}

/// Agent voice, family context and the LLM: each versioned, each with history and rollback.
@MainActor @Observable
public final class AIModel {
    public static let fields = [("agent_voice", "Agent voice"), ("family_context", "Family context")]
    public private(set) var settings: [String: AISetting] = [:]
    public private(set) var llm: LLMConfig?
    public var errorMessage: String?
    private let client: APIClient
    public init(client: APIClient) { self.client = client }

    public func load() async {
        do { settings = try await client.aiSettings(); llm = try await client.llmConfig() }
        catch { errorMessage = error.localizedDescription }
    }

    @discardableResult
    public func save(field: String, value: String) async -> Bool {
        do { settings[field] = try await client.saveAISetting(field: field, value: value); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    public func history(field: String) async -> AIHistory? { try? await client.aiHistory(field: field) }

    @discardableResult
    public func rollback(field: String, to id: Int) async -> Bool {
        do { settings[field] = try await client.aiRollback(field: field, historyID: id); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    /// Keys first, then the coupled provider/model snapshot, then a live check — a typo'd
    /// model or missing key is reported in the server's own words and writes no snapshot.
    public func saveLLM(keys: [String: String], provider: String, model: String, maxTokens: Int, mode: String) async -> String {
        do {
            try await client.saveSettings(keys)
            llm = try await client.saveLLMConfig(provider: provider, model: model, maxTokens: maxTokens, mode: mode)
            return try await client.testLLM().text
        } catch { return error.localizedDescription }
    }

    public func llmHistory() async -> LLMHistory? { try? await client.llmHistory() }

    @discardableResult
    public func rollbackLLM(to id: Int) async -> Bool {
        do { llm = try await client.llmRollback(historyID: id); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }
}
