import Foundation

public enum TodoSort: String, CaseIterable, Sendable, Identifiable {
    case dueSoonest = "due-soonest", dueFurthest = "due-furthest", assignee, newest, oldest
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .dueSoonest: "Due Date (Soonest)"
        case .dueFurthest: "Due Date (Furthest Out)"
        case .assignee: "Assignee"
        case .newest: "Newest First"
        case .oldest: "Oldest First"
        }
    }
}

public enum CompletedSort: String, CaseIterable, Sendable, Identifiable {
    case completedNewest = "completed-newest", completedOldest = "completed-oldest"
    case dueSoonest = "due-soonest", dueFurthest = "due-furthest", assignee, newest, oldest
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .completedNewest: "Completion Date (Most Recent)"
        case .completedOldest: "Completion Date (Oldest)"
        case .dueSoonest: "Due Date (Soonest)"
        case .dueFurthest: "Due Date (Furthest Out)"
        case .assignee: "Assignee"
        case .newest: "Newest First"
        case .oldest: "Oldest First"
        }
    }
}

/// A calendar date with no time, which is what a due date is. Parsed into the
/// phone's calendar so "today" means the day the person is living.
public enum DayString {
    public static func date(_ text: String?, calendar: Calendar = .current) -> Date? {
        guard let text, text.count == 10 else { return nil }
        let p = text.split(separator: "-").compactMap { Int($0) }
        guard p.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: p[0], month: p[1], day: p[2]))
    }

    public static func string(_ date: Date, calendar: Calendar = .current) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year!, c.month!, c.day!)
    }
}

public enum DueStatus: Equatable, Sendable { case none, overdue, today, upcoming }

public enum TodoLogic {
    public static let unassigned = "unassigned"

    public static func assigneeKey(_ todo: Todo) -> String { todo.assignedTo.map(String.init) ?? unassigned }

    /// No selection is the unfiltered state, matching the chips on the web page.
    public static func filtered(_ todos: [Todo], assignees: Set<String>) -> [Todo] {
        assignees.isEmpty ? todos : todos.filter { assignees.contains(assigneeKey($0)) }
    }

    /// Open tasks in the chosen order, then the ones completed today (they stay
    /// until local midnight), so ticking one off does not make it vanish under a finger.
    public static func sorted(_ todos: [Todo], by sort: TodoSort, members: [FamilyMember]) -> [Todo] {
        func newest(_ a: Todo, _ b: Todo) -> Bool { a.createdAt > b.createdAt }
        func dueSoonest(_ a: Todo, _ b: Todo) -> Bool {
            switch (a.dueDate, b.dueDate) {
            case let (x?, y?): return x != y ? x < y : newest(a, b)
            case (nil, nil): return newest(a, b)
            case (_?, nil): return true
            case (nil, _?): return false
            }
        }
        func dueFurthest(_ a: Todo, _ b: Todo) -> Bool {
            switch (a.dueDate, b.dueDate) {
            case let (x?, y?): return x != y ? x > y : newest(a, b)
            case (nil, nil): return newest(a, b)
            case (_?, nil): return true
            case (nil, _?): return false
            }
        }
        func name(_ t: Todo) -> String? { t.assignedTo.flatMap { id in members.first { $0.id == id }?.name } }

        let order: (Todo, Todo) -> Bool
        switch sort {
        case .newest: order = newest
        case .oldest: order = { $0.createdAt < $1.createdAt }
        case .dueSoonest: order = dueSoonest
        case .dueFurthest: order = dueFurthest
        case .assignee:
            order = { a, b in
                switch (name(a), name(b)) {
                case let (x?, y?):
                    let c = x.localizedCaseInsensitiveCompare(y)
                    return c == .orderedSame ? dueSoonest(a, b) : c == .orderedAscending
                case (nil, nil): return dueSoonest(a, b)
                case (_?, nil): return true
                case (nil, _?): return false
                }
            }
        }
        let open = todos.filter { !$0.completed }.sorted(by: order)
        let done = todos.filter(\.completed).sorted { ($0.completedAt ?? .distantPast) > ($1.completedAt ?? .distantPast) }
        return open + done
    }

    public static func status(of todo: Todo, today: Date = .now, calendar: Calendar = .current) -> DueStatus {
        guard !todo.completed, let due = DayString.date(todo.dueDate, calendar: calendar) else { return .none }
        let start = calendar.startOfDay(for: today)
        if due < start { return .overdue }
        return due == start ? .today : .upcoming
    }

    /// "Today", "Tomorrow", a weekday inside the week, otherwise a short date.
    public static func dueLabel(_ dueDate: String?, today: Date = .now, calendar: Calendar = .current) -> String? {
        guard let due = DayString.date(dueDate, calendar: calendar) else { return nil }
        let start = calendar.startOfDay(for: today)
        let days = calendar.dateComponents([.day], from: start, to: due).day ?? 0
        switch days {
        case 0: return "Today"
        case 1: return "Tomorrow"
        case -1: return "Yesterday"
        case 2...6: return due.formatted(.dateTime.weekday(.wide))
        default:
            let sameYear = calendar.component(.year, from: due) == calendar.component(.year, from: start)
            return sameYear ? due.formatted(.dateTime.month(.abbreviated).day()) : due.formatted(.dateTime.month(.abbreviated).day().year())
        }
    }

    // MARK: Recurrence wording

    public static let weekdayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    public static func ordinal(_ n: Int) -> String {
        let tens = n % 100
        if (11...13).contains(tens) { return "\(n)th" }
        switch n % 10 { case 1: return "\(n)st"; case 2: return "\(n)nd"; case 3: return "\(n)rd"; default: return "\(n)th" }
    }

    /// The same sentences the web list shows.
    public static func describe(_ rt: RecurringTodo) -> String {
        switch rt.recurrenceType {
        case "daily": return "Daily"
        case "weekly": return "Weekly on \(weekdayNames[safe: rt.recurrenceDay ?? 0] ?? "Monday")"
        case "monthly": return "Monthly on the \(ordinal(rt.recurrenceDay ?? 1))"
        case "custom": return rt.customRule.map(describe) ?? "Custom"
        default: return rt.recurrenceType
        }
    }

    public static func describe(_ rule: CustomRule) -> String {
        let n = max(1, rule.interval)
        let every = n == 1 ? "Every" : "Every \(n)"
        switch rule.freq {
        case .daily:
            let base = n == 1 ? "Daily" : "Every \(n) days"
            var notes: [String] = []
            if rule.weekdaysOnly { notes.append("weekdays only") }
            if rule.nextDueFromCompletion { notes.append("from completion date") }
            return notes.isEmpty ? base : "\(base) (\(notes.joined(separator: ", ")))"
        case .weekly:
            let days = (rule.weekdays.isEmpty ? [0] : rule.weekdays).compactMap { weekdayNames[safe: $0]?.prefix(3) }.joined(separator: ", ")
            return "\(every) week\(n == 1 ? "" : "s") on \(days)"
        case .monthly:
            let month = "\(every) month\(n == 1 ? "" : "s")"
            return rule.mode == .day
                ? "\(month) on the \(ordinal(rule.day))"
                : "\(month) on the \(rule.ordinal) \(weekdayNames[safe: rule.weekday] ?? "Monday")"
        }
    }

    /// The Recurring list appends this while the start is still in the future: a
    /// series with nothing on the task list is otherwise invisibly scheduled.
    public static func startsNote(_ startDate: String?, today: Date = .now, calendar: Calendar = .current) -> String? {
        guard let start = DayString.date(startDate, calendar: calendar), start > calendar.startOfDay(for: today) else { return nil }
        return "starts \(start.formatted(.dateTime.month(.abbreviated).day().year()))"
    }
}

extension Array {
    subscript(safe index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
}
