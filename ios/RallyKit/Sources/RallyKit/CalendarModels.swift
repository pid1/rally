import Foundation

/// One dated instance of an event — what every calendar view renders. Distinct
/// from ``EventSeries``, which is the stored rule the edit form reads.
public struct Occurrence: Decodable, Sendable, Identifiable, Equatable, Hashable {
    public let uid: String
    public let source: String
    public let title: String
    public let description: String
    public let location: String
    public let allDay: Bool
    public let start: Date
    public let end: Date
    public let startDate: String      // local, inclusive
    public let endDate: String        // local, inclusive
    public let timeLabel: String      // "5:30 PM" in the install's zone
    public let endTimeLabel: String
    /// What the edit form shows for *this* occurrence (not the series').
    public let startForm: String
    public let endForm: String
    public let dates: [String]        // every local date it covers
    public let calendarID: Int?
    public let calendarLabel: String
    public let member: String?
    public let memberColor: String?
    public let attendees: [String]
    public let eventID: Int?
    public let occurrenceDate: String?
    public let recurring: Bool
    public let rrule: String?
    public let recurrenceText: String
    public let editable: Bool
    public let notifyMinutesBefore: Int?

    public var id: String { "\(uid)|\(startDate)|\(startForm)|\(title)" }

    enum CodingKeys: String, CodingKey {
        case uid, source, title, description, location, start, end, dates, member, attendees, recurring, rrule, editable
        case allDay = "all_day", startDate = "start_date", endDate = "end_date", timeLabel = "time_label"
        case endTimeLabel = "end_time_label", startForm = "start_form", endForm = "end_form"
        case calendarID = "calendar_id", calendarLabel = "calendar_label", memberColor = "member_color"
        case eventID = "event_id", occurrenceDate = "occurrence_date", recurrenceText = "recurrence_text"
        case notifyMinutesBefore = "notify_minutes_before"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        uid = try c.decode(String.self, forKey: .uid)
        source = try c.decode(String.self, forKey: .source)
        title = try c.decode(String.self, forKey: .title)
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        location = try c.decodeIfPresent(String.self, forKey: .location) ?? ""
        allDay = try c.decode(Bool.self, forKey: .allDay)
        start = try c.decode(Date.self, forKey: .start)
        end = try c.decode(Date.self, forKey: .end)
        startDate = try c.decode(String.self, forKey: .startDate)
        endDate = try c.decode(String.self, forKey: .endDate)
        timeLabel = try c.decodeIfPresent(String.self, forKey: .timeLabel) ?? ""
        endTimeLabel = try c.decodeIfPresent(String.self, forKey: .endTimeLabel) ?? ""
        startForm = try c.decodeIfPresent(String.self, forKey: .startForm) ?? startDate
        endForm = try c.decodeIfPresent(String.self, forKey: .endForm) ?? endDate
        dates = try c.decodeIfPresent([String].self, forKey: .dates) ?? [startDate]
        calendarID = try c.decodeIfPresent(Int.self, forKey: .calendarID)
        calendarLabel = try c.decodeIfPresent(String.self, forKey: .calendarLabel) ?? ""
        member = try c.decodeIfPresent(String.self, forKey: .member)
        memberColor = try c.decodeIfPresent(String.self, forKey: .memberColor)
        attendees = try c.decodeIfPresent([String].self, forKey: .attendees) ?? []
        eventID = try c.decodeIfPresent(Int.self, forKey: .eventID)
        occurrenceDate = try c.decodeIfPresent(String.self, forKey: .occurrenceDate)
        recurring = try c.decodeIfPresent(Bool.self, forKey: .recurring) ?? false
        rrule = try c.decodeIfPresent(String.self, forKey: .rrule)
        recurrenceText = try c.decodeIfPresent(String.self, forKey: .recurrenceText) ?? ""
        editable = try c.decodeIfPresent(Bool.self, forKey: .editable) ?? false
        notifyMinutesBefore = try c.decodeIfPresent(Int.self, forKey: .notifyMinutesBefore)
    }
}

public struct OccurrencePage: Decodable, Sendable {
    public let occurrences: [Occurrence]
    /// Sources that failed, so a view can say so rather than show a silently short calendar.
    public let failures: [String]
}

public struct EventOverride: Decodable, Sendable, Equatable {
    public let occurrenceDate: String
    public let cancelled: Bool
    enum CodingKeys: String, CodingKey { case occurrenceDate = "occurrence_date", cancelled }
}

/// The stored series row: the rule, not a dated instance of it.
public struct EventSeries: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public let calendarID: Int
    public let title: String
    public let description: String?
    public let location: String?
    public let allDay: Bool
    public let start: String   // local, in the event's own zone
    public let end: String
    public let rrule: String?
    public let notifyMinutesBefore: Int?
    public let attendeeIDs: [Int]
    public let overrides: [EventOverride]

    enum CodingKeys: String, CodingKey {
        case id, title, description, location, start, end, rrule, overrides
        case calendarID = "calendar_id", allDay = "all_day", notifyMinutesBefore = "notify_minutes_before"
        case attendeeIDs = "attendee_ids"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        calendarID = try c.decode(Int.self, forKey: .calendarID)
        title = try c.decode(String.self, forKey: .title)
        description = try c.decodeIfPresent(String.self, forKey: .description)
        location = try c.decodeIfPresent(String.self, forKey: .location)
        allDay = try c.decode(Bool.self, forKey: .allDay)
        start = try c.decode(String.self, forKey: .start)
        end = try c.decode(String.self, forKey: .end)
        rrule = try c.decodeIfPresent(String.self, forKey: .rrule)
        notifyMinutesBefore = try c.decodeIfPresent(Int.self, forKey: .notifyMinutesBefore)
        attendeeIDs = try c.decodeIfPresent([Int].self, forKey: .attendeeIDs) ?? []
        overrides = try c.decodeIfPresent([EventOverride].self, forKey: .overrides) ?? []
    }
}

public struct CalendarFeed: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public let label: String
    public let familyMemberID: Int?
    public let calType: String
    enum CodingKeys: String, CodingKey { case id, label, familyMemberID = "family_member_id", calType = "cal_type" }
    public var isNative: Bool { calType == "native" }
}

public struct NotifyResult: Decodable, Sendable, Equatable {
    public let sent: [String]
    public let skipped: [String]
    public let muted: [String]
    public let failed: [String]
    public let error: String?

    /// "Sent to Jon. No Pushover key for Emma." — the claim "it worked" and "both
    /// phones buzzed" are different, so every outcome is named.
    public var summary: String {
        var parts: [String] = []
        if !sent.isEmpty { parts.append("Sent to \(sent.joined(separator: ", ")).") }
        if !skipped.isEmpty { parts.append("No Pushover key for \(skipped.joined(separator: ", ")).") }
        if !muted.isEmpty { parts.append("\(muted.joined(separator: ", ")) turned event reminders off.") }
        if !failed.isEmpty { parts.append("Failed for \(failed.joined(separator: ", ")).") }
        if let error { parts.append(error) }
        return parts.isEmpty ? "Nothing to send." : parts.joined(separator: " ")
    }
}

public enum EditScope: String, Sendable { case this, following, all }

extension APIClient {
    public func occurrences(start: String, end: String, members: Set<String>) async throws -> OccurrencePage {
        var query = [URLQueryItem(name: "start", value: start), URLQueryItem(name: "end", value: end)]
        for name in members.sorted() { query.append(URLQueryItem(name: "member", value: name)) }
        return try await get(.events, query: query)
    }

    public func event(id: Int) async throws -> EventSeries { try await get(.event, ["event_id": String(id)]) }

    public func nativeCalendars() async throws -> [CalendarFeed] {
        let all: [CalendarFeed] = try await get(.calendars)
        return all.filter(\.isNative)
    }

    public func createEvent(_ draft: EventDraft) async throws -> EventSeries {
        try await send(.createEvent, body: draft.body(editing: nil, timesUntouched: false))
    }

    public func updateEvent(id: Int, _ draft: EventDraft, editing: EventSeries, scope: EditScope,
                            occurrenceDate: String?) async throws -> EventSeries {
        var query = [URLQueryItem(name: "scope", value: scope.rawValue)]
        if scope != .all, let occurrenceDate { query.append(URLQueryItem(name: "occurrence_date", value: occurrenceDate)) }
        return try await send(.updateEvent, ["event_id": String(id)], query: query,
                              body: draft.body(editing: editing, timesUntouched: draft.timesUntouched))
    }

    public func deleteEvent(id: Int, scope: EditScope, occurrenceDate: String?) async throws {
        var query = [URLQueryItem(name: "scope", value: scope.rawValue)]
        if scope != .all, let occurrenceDate { query.append(URLQueryItem(name: "occurrence_date", value: occurrenceDate)) }
        try await perform(.deleteEvent, ["event_id": String(id)], query: query)
    }

    /// An unsaved rule read back in words — the vocabulary lives on the server once.
    public func describeRecurrence(_ rrule: String?) async throws -> String {
        struct Reply: Decodable, Sendable { let description: String }
        let reply: Reply = try await send(.describeRecurrence, body: PatchBody().set("rrule", Patch.clearing(rrule)))
        return reply.description
    }

    public func notifyEvent(id: Int, occurrenceDate: String?) async throws -> NotifyResult {
        try await send(.notifyEvent, ["event_id": String(id)],
                       body: PatchBody().set("occurrence_date", Patch.clearing(occurrenceDate)))
    }
}
