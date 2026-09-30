import Foundation
import Testing
@testable import RallyKit

private let cal: Calendar = { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "America/Chicago")!; return c }()
private func day(_ s: String) -> Date { DayString.date(s, calendar: cal)! }
private func todo(_ id: Int, due: String? = nil, who: Int? = nil, done: Bool = false, age: Int = 0, completedAgo: Int = 0) -> Todo {
    Todo(id: id, title: "T\(id)", dueDate: due, assignedTo: who, completed: done,
         completedAt: done ? Date(timeIntervalSince1970: 1_000_000 - Double(completedAgo)) : nil,
         createdAt: Date(timeIntervalSince1970: 1_000_000 - Double(age)))
}
private let members = [
    try! JSONDecoder.rally.decode(FamilyMember.self, from: Data(##"{"id":1,"name":"Mom","color":"#315277"}"##.utf8)),
    try! JSONDecoder.rally.decode(FamilyMember.self, from: Data(##"{"id":2,"name":"Dad","color":"#af2c3d"}"##.utf8)),
]

struct TodoLogicTests {
    @Test func dueSoonestPutsUndatedLastNewestFirst() {
        let list = [todo(1, age: 10), todo(2, due: "2026-10-05"), todo(3, due: "2026-10-01"), todo(4, age: 1)]
        #expect(TodoLogic.sorted(list, by: .dueSoonest, members: members).map(\.id) == [3, 2, 4, 1])
    }

    @Test func dueFurthestStillPutsUndatedLast() {
        let list = [todo(1), todo(2, due: "2026-10-05"), todo(3, due: "2026-10-01")]
        #expect(TodoLogic.sorted(list, by: .dueFurthest, members: members).map(\.id) == [2, 3, 1])
    }

    @Test func assigneeSortsByNameUnassignedLast() {
        let list = [todo(1), todo(2, who: 1), todo(3, who: 2)]
        #expect(TodoLogic.sorted(list, by: .assignee, members: members).map(\.id) == [3, 2, 1]) // Dad, Mom, nobody
    }

    @Test func completedTodayStayAfterOpenOnes() {
        let list = [todo(1, done: true, completedAgo: 0), todo(2), todo(3, done: true, completedAgo: 500), todo(4)]
        let out = TodoLogic.sorted(list, by: .newest, members: members)
        #expect(out.prefix(2).allSatisfy { !$0.completed })
        #expect(out.suffix(2).map(\.id) == [1, 3])
    }

    @Test func assigneeFilterIsUnfilteredWhenEmpty() {
        let list = [todo(1), todo(2, who: 1), todo(3, who: 2)]
        #expect(TodoLogic.filtered(list, assignees: []).count == 3)
        #expect(TodoLogic.filtered(list, assignees: ["1", TodoLogic.unassigned]).map(\.id) == [1, 2])
    }

    @Test func dueStatus() {
        let today = day("2026-09-30")
        #expect(TodoLogic.status(of: todo(1, due: "2026-09-29"), today: today, calendar: cal) == .overdue)
        #expect(TodoLogic.status(of: todo(1, due: "2026-09-30"), today: today, calendar: cal) == .today)
        #expect(TodoLogic.status(of: todo(1, due: "2026-10-02"), today: today, calendar: cal) == .upcoming)
        #expect(TodoLogic.status(of: todo(1, due: "2026-09-01", done: true), today: today, calendar: cal) == .none)
        #expect(TodoLogic.status(of: todo(1), today: today, calendar: cal) == .none)
    }

    @Test func dueLabels() {
        let today = day("2026-09-30") // a Wednesday
        #expect(TodoLogic.dueLabel("2026-09-30", today: today, calendar: cal) == "Today")
        #expect(TodoLogic.dueLabel("2026-10-01", today: today, calendar: cal) == "Tomorrow")
        #expect(TodoLogic.dueLabel("2026-09-29", today: today, calendar: cal) == "Yesterday")
        #expect(TodoLogic.dueLabel(nil, today: today, calendar: cal) == nil)
    }

    @Test func ordinals() {
        #expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111].map(TodoLogic.ordinal)
                == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st", "111th"])
    }

    @Test func describesRecurrenceLikeTheWebList() {
        #expect(TodoLogic.describe(RecurringTodo(id: 1, title: "x", recurrenceType: "weekly", recurrenceDay: 4)) == "Weekly on Friday")
        #expect(TodoLogic.describe(RecurringTodo(id: 1, title: "x", recurrenceType: "monthly", recurrenceDay: 22)) == "Monthly on the 22nd")
        #expect(TodoLogic.describe(CustomRule(freq: .daily, interval: 3, weekdaysOnly: true, nextDueFromCompletion: true))
                == "Every 3 days (weekdays only, from completion date)")
        #expect(TodoLogic.describe(CustomRule(freq: .weekly, interval: 2, weekdays: [1, 3])) == "Every 2 weeks on Tue, Thu")
        #expect(TodoLogic.describe(CustomRule(freq: .monthly, interval: 12, mode: .weekday, ordinal: "first", weekday: 6))
                == "Every 12 months on the first Sunday")
        #expect(TodoLogic.describe(RecurringTodo(id: 1, title: "x", recurrenceType: "custom")) == "Custom")
    }

    @Test func startsNoteOnlyWhileInTheFuture() {
        let today = day("2026-09-30")
        #expect(TodoLogic.startsNote("2027-01-01", today: today, calendar: cal)?.hasPrefix("starts ") == true)
        #expect(TodoLogic.startsNote("2026-09-01", today: today, calendar: cal) == nil)
        #expect(TodoLogic.startsNote(nil, today: today, calendar: cal) == nil)
    }
}

struct CustomRuleTests {
    private func encode(_ rule: CustomRule) throws -> [String: Any] {
        try JSONSerialization.jsonObject(with: JSONEncoder().encode(rule)) as! [String: Any]
    }

    @Test func dailyWritesOnlyDailyKeys() throws {
        let out = try encode(CustomRule(freq: .daily, interval: 2, weekdays: [1, 2], day: 9))
        #expect(Set(out.keys) == ["freq", "interval"])
        let flagged = try encode(CustomRule(freq: .daily, weekdaysOnly: true, nextDueFromCompletion: true))
        #expect(flagged["weekdays_only"] as? Bool == true && flagged["next_due_from"] as? String == "completion_date")
    }

    @Test func weeklyWritesSortedWeekdaysAndDefaultsToMonday() throws {
        #expect(try encode(CustomRule(freq: .weekly, weekdays: [4, 0]))["weekdays"] as? [Int] == [0, 4])
        #expect(try encode(CustomRule(freq: .weekly, weekdays: []))["weekdays"] as? [Int] == [0])
    }

    @Test func monthlyByDayOrByPosition() throws {
        let byDay = try encode(CustomRule(freq: .monthly, mode: .day, day: 15))
        #expect(Set(byDay.keys) == ["freq", "interval", "mode", "day"])
        let byPos = try encode(CustomRule(freq: .monthly, mode: .weekday, ordinal: "last", weekday: 4))
        #expect(Set(byPos.keys) == ["freq", "interval", "mode", "ordinal", "weekday"])
    }

    @Test func intervalNeverGoesBelowOne() throws {
        #expect(try encode(CustomRule(interval: 0))["interval"] as? Int == 1)
    }

    @Test func roundTripsWhatTheServerStores() throws {
        let json = Data(##"{"freq":"monthly","interval":12,"mode":"weekday","ordinal":"first","weekday":6}"##.utf8)
        let rule = try JSONDecoder().decode(CustomRule.self, from: json)
        #expect(rule == CustomRule(freq: .monthly, interval: 12, mode: .weekday, ordinal: "first", weekday: 6))
    }

    @Test func anUnreadableRuleDoesNotBreakTheTemplate() throws {
        let json = Data(##"{"id":1,"title":"x","recurrence_type":"custom","custom_rule":{"freq":"yearly"},"active":true}"##.utf8)
        let rt = try JSONDecoder.rally.decode(RecurringTodo.self, from: json)
        #expect(rt.customRule == nil)
        #expect(TodoLogic.describe(rt) == "Custom")
    }
}

struct TodoEndpointTests {
    let base = URL(string: "http://rally.test")!
    let todoJSON = Data(##"{"id":1,"title":"T","description":null,"due_date":null,"assigned_to":null,"remind_days_before":null,"recurring_todo_id":null,"completed":false,"completed_at":null,"created_at":"2026-09-30T10:00:00","updated_at":"2026-09-30T10:00:00"}"##.utf8)

    private func sent(_ rec: Recorder) -> [String: Any] {
        try! JSONSerialization.jsonObject(with: rec.requests.last!.httpBody!) as! [String: Any]
    }

    @Test func clearingAssigneeAndDueDateSendsNulls() async throws {
        let rec = Recorder(); rec.body = todoJSON
        var draft = TodoDraft(); draft.title = " Fix sink "
        _ = try await APIClient(baseURL: base, transport: rec.transport()).updateTodo(id: 1, draft)
        let body = sent(rec)
        #expect(body["title"] as? String == "Fix sink")
        #expect(body["assigned_to"] is NSNull && body["due_date"] is NSNull && body["description"] is NSNull)
        #expect(body["remind_days_before"] is NSNull)
    }

    @Test func aReminderWindowNeedsADueDate() async throws {
        let rec = Recorder(); rec.body = todoJSON
        var draft = TodoDraft(); draft.title = "x"; draft.remindDaysBefore = 3
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createTodo(draft)
        #expect(sent(rec)["remind_days_before"] is NSNull)
        draft.dueDate = "2026-10-01"
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createTodo(draft)
        #expect(sent(rec)["remind_days_before"] as? Int == 3)
    }

    @Test func completingSendsOnlyCompleted() async throws {
        let rec = Recorder(); rec.body = todoJSON
        _ = try await APIClient(baseURL: base, transport: rec.transport()).setTodoCompleted(id: 1, true)
        #expect(sent(rec).keys.sorted() == ["completed"])
    }

    @Test func recurringCreateCompilesTheRule() async throws {
        let rec = Recorder()
        rec.body = Data(##"{"id":1,"title":"x","recurrence_type":"custom","active":true,"created_at":"2026-09-30T10:00:00","updated_at":"2026-09-30T10:00:00"}"##.utf8)
        var draft = RecurringDraft()
        draft.title = "Smoke detector"; draft.type = "custom"
        draft.customRule = CustomRule(freq: .monthly, interval: 12, mode: .weekday, ordinal: "first", weekday: 6)
        draft.startDate = "2027-01-01"
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createRecurringTodo(draft)
        let body = sent(rec)
        #expect(body["recurrence_type"] as? String == "custom")
        #expect(body["recurrence_day"] is NSNull)
        #expect(body["start_date"] as? String == "2027-01-01")
        #expect((body["custom_rule"] as? [String: Any])?["ordinal"] as? String == "first")
        #expect(body["active"] == nil) // a new series is always active
    }

    @Test func weeklyAndMonthlySendTheirDay() async throws {
        let rec = Recorder()
        rec.body = Data(##"{"id":1,"title":"x","recurrence_type":"weekly","active":true,"created_at":"2026-09-30T10:00:00","updated_at":"2026-09-30T10:00:00"}"##.utf8)
        let client = APIClient(baseURL: base, transport: rec.transport())
        var draft = RecurringDraft(); draft.title = "x"; draft.type = "weekly"; draft.weekday = 4
        _ = try await client.createRecurringTodo(draft)
        #expect(sent(rec)["recurrence_day"] as? Int == 4 && sent(rec)["custom_rule"] is NSNull)
        draft.type = "monthly"; draft.dayOfMonth = 15
        _ = try await client.createRecurringTodo(draft)
        #expect(sent(rec)["recurrence_day"] as? Int == 15)
    }

    @Test func previewSendsTheUnsavedRule() async throws {
        let rec = Recorder(); rec.body = Data(#"{"occurrences":["2027-01-01","2028-01-01"]}"#.utf8)
        var draft = RecurringDraft(); draft.type = "daily"
        let dates = try await APIClient(baseURL: base, transport: rec.transport()).previewRecurrence(draft)
        #expect(dates == ["2027-01-01", "2028-01-01"])
        #expect(rec.requests[0].url?.path == "/api/recurring-todos/preview")
    }

    @Test func completedArchiveQuery() async throws {
        let rec = Recorder(); rec.body = Data(#"{"items":[],"has_more":false,"total":0}"#.utf8)
        _ = try await APIClient(baseURL: base, transport: rec.transport())
            .completedTodos(sort: .completedOldest, assignees: ["2", "unassigned"], search: "sink", offset: 50)
        let url = rec.requests[0].url!.absoluteString
        #expect(url.contains("sort=completed-oldest") && url.contains("assignee=2") && url.contains("assignee=unassigned")
                && url.contains("search=sink") && url.contains("offset=50"))
    }
}
