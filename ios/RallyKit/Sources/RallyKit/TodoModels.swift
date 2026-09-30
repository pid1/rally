import Foundation

public struct Todo: Codable, Sendable, Identifiable, Equatable, Hashable {
    public let id: Int
    public var title: String
    public var description: String?
    /// `YYYY-MM-DD`, a calendar date with no time or zone.
    public var dueDate: String?
    public var assignedTo: Int?
    public var remindDaysBefore: Int?
    public var recurringTodoID: Int?
    public var completed: Bool
    public var completedAt: Date?
    public let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, title, description, completed
        case dueDate = "due_date"
        case assignedTo = "assigned_to"
        case remindDaysBefore = "remind_days_before"
        case recurringTodoID = "recurring_todo_id"
        case completedAt = "completed_at"
        case createdAt = "created_at"
    }

    public init(id: Int, title: String, description: String? = nil, dueDate: String? = nil, assignedTo: Int? = nil,
                remindDaysBefore: Int? = nil, recurringTodoID: Int? = nil, completed: Bool = false,
                completedAt: Date? = nil, createdAt: Date = .now) {
        self.id = id; self.title = title; self.description = description; self.dueDate = dueDate
        self.assignedTo = assignedTo; self.remindDaysBefore = remindDaysBefore
        self.recurringTodoID = recurringTodoID; self.completed = completed
        self.completedAt = completedAt; self.createdAt = createdAt
    }
}

/// The `custom_rule` JSON of a recurring task. Weekdays count from **Monday = 0**,
/// as they do in `rally.recurrence` (unlike the calendar's Sunday-first grid).
public struct CustomRule: Codable, Sendable, Equatable, Hashable {
    public enum Freq: String, Codable, Sendable, CaseIterable { case daily, weekly, monthly }
    public enum MonthlyMode: String, Codable, Sendable { case day, weekday }

    public var freq: Freq
    public var interval: Int
    public var weekdaysOnly: Bool
    /// `completion_date` counts the next one from when it was done; absent means the due date.
    public var nextDueFromCompletion: Bool
    public var weekdays: [Int]
    public var mode: MonthlyMode
    public var day: Int
    public var ordinal: String
    public var weekday: Int

    public init(freq: Freq = .daily, interval: Int = 1, weekdaysOnly: Bool = false, nextDueFromCompletion: Bool = false,
                weekdays: [Int] = [0], mode: MonthlyMode = .day, day: Int = 1, ordinal: String = "first", weekday: Int = 0) {
        self.freq = freq; self.interval = interval; self.weekdaysOnly = weekdaysOnly
        self.nextDueFromCompletion = nextDueFromCompletion; self.weekdays = weekdays
        self.mode = mode; self.day = day; self.ordinal = ordinal; self.weekday = weekday
    }

    enum CodingKeys: String, CodingKey {
        case freq, interval, weekdays, mode, day, ordinal, weekday
        case weekdaysOnly = "weekdays_only"
        case nextDueFrom = "next_due_from"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        freq = try c.decode(Freq.self, forKey: .freq)
        interval = try c.decodeIfPresent(Int.self, forKey: .interval) ?? 1
        weekdaysOnly = try c.decodeIfPresent(Bool.self, forKey: .weekdaysOnly) ?? false
        nextDueFromCompletion = (try c.decodeIfPresent(String.self, forKey: .nextDueFrom)) == "completion_date"
        weekdays = try c.decodeIfPresent([Int].self, forKey: .weekdays) ?? [0]
        mode = try c.decodeIfPresent(MonthlyMode.self, forKey: .mode) ?? .day
        day = try c.decodeIfPresent(Int.self, forKey: .day) ?? 1
        ordinal = try c.decodeIfPresent(String.self, forKey: .ordinal) ?? "first"
        weekday = try c.decodeIfPresent(Int.self, forKey: .weekday) ?? 0
    }

    /// Only what the chosen frequency uses, exactly as the web form builds it —
    /// a stray `weekdays` on a daily rule would be stored forever and read by nothing.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(freq, forKey: .freq)
        try c.encode(max(1, interval), forKey: .interval)
        switch freq {
        case .daily:
            if weekdaysOnly { try c.encode(true, forKey: .weekdaysOnly) }
            if nextDueFromCompletion { try c.encode("completion_date", forKey: .nextDueFrom) }
        case .weekly:
            try c.encode(weekdays.isEmpty ? [0] : weekdays.sorted(), forKey: .weekdays)
        case .monthly:
            try c.encode(mode, forKey: .mode)
            if mode == .day { try c.encode(day, forKey: .day) }
            else { try c.encode(ordinal, forKey: .ordinal); try c.encode(weekday, forKey: .weekday) }
        }
    }
}

public struct RecurringTodo: Decodable, Sendable, Identifiable, Equatable, Hashable {
    public let id: Int
    public var title: String
    public var description: String?
    public var recurrenceType: String   // daily, weekly, monthly, custom
    public var recurrenceDay: Int?
    public var assignedTo: Int?
    public var hasDueDate: Bool
    public var remindDaysBefore: Int?
    public var customRule: CustomRule?
    public var startDate: String?
    public var active: Bool
    public var lastGeneratedDate: String?
    public var lastCompletedDisplay: String?

    enum CodingKeys: String, CodingKey {
        case id, title, description, active
        case recurrenceType = "recurrence_type"
        case recurrenceDay = "recurrence_day"
        case assignedTo = "assigned_to"
        case hasDueDate = "has_due_date"
        case remindDaysBefore = "remind_days_before"
        case customRule = "custom_rule"
        case startDate = "start_date"
        case lastGeneratedDate = "last_generated_date"
        case lastCompletedDisplay = "last_completed_display"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        title = try c.decode(String.self, forKey: .title)
        description = try c.decodeIfPresent(String.self, forKey: .description)
        recurrenceType = try c.decode(String.self, forKey: .recurrenceType)
        recurrenceDay = try c.decodeIfPresent(Int.self, forKey: .recurrenceDay)
        assignedTo = try c.decodeIfPresent(Int.self, forKey: .assignedTo)
        hasDueDate = try c.decodeIfPresent(Bool.self, forKey: .hasDueDate) ?? false
        remindDaysBefore = try c.decodeIfPresent(Int.self, forKey: .remindDaysBefore)
        // A rule this app cannot read is shown as plain "Custom", not a failed list.
        customRule = try? c.decodeIfPresent(CustomRule.self, forKey: .customRule)
        startDate = try c.decodeIfPresent(String.self, forKey: .startDate)
        active = try c.decodeIfPresent(Bool.self, forKey: .active) ?? true
        lastGeneratedDate = try c.decodeIfPresent(String.self, forKey: .lastGeneratedDate)
        lastCompletedDisplay = try c.decodeIfPresent(String.self, forKey: .lastCompletedDisplay)
    }

    public init(id: Int, title: String, recurrenceType: String, recurrenceDay: Int? = nil, customRule: CustomRule? = nil,
                startDate: String? = nil, active: Bool = true) {
        self.id = id; self.title = title; self.description = nil; self.recurrenceType = recurrenceType
        self.recurrenceDay = recurrenceDay; self.assignedTo = nil; self.hasDueDate = false
        self.remindDaysBefore = nil; self.customRule = customRule; self.startDate = startDate
        self.active = active; self.lastGeneratedDate = nil; self.lastCompletedDisplay = nil
    }
}

struct RecurrencePreview: Decodable, Sendable { let occurrences: [String] }
