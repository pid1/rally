import Foundation

/// What the task editor sends. Every field is explicit, so clearing one (no
/// assignee, no due date) is a real `null` rather than an omitted key.
public struct TodoDraft: Sendable, Equatable {
    public var title = ""
    public var description = ""
    public var dueDate: String?
    public var assignedTo: Int?
    public var remindDaysBefore: Int?
    public init() {}
    public init(_ todo: Todo) {
        title = todo.title; description = todo.description ?? ""; dueDate = todo.dueDate
        assignedTo = todo.assignedTo; remindDaysBefore = todo.remindDaysBefore
    }

    func body(includeCompleted: Bool? = nil) throws -> PatchBody {
        let desc = description.trimmingCharacters(in: .whitespacesAndNewlines)
        var body = try PatchBody()
            .set("title", Patch.value(title.trimmingCharacters(in: .whitespaces)))
            .set("description", Patch.clearing(desc.isEmpty ? nil : desc))
            .set("due_date", Patch.clearing(dueDate))
            .set("assigned_to", Patch.clearing(assignedTo))
            // No due date means no reminder window: it counts back from one.
            .set("remind_days_before", Patch.clearing(dueDate == nil ? nil : remindDaysBefore))
        if let includeCompleted { body = try body.set("completed", Patch.value(includeCompleted)) }
        return body
    }
}

public struct RecurringDraft: Sendable, Equatable {
    public var title = ""
    public var description = ""
    public var type = "daily"            // daily, weekly, monthly, custom
    public var weekday = 0               // weekly; Monday = 0
    public var dayOfMonth = 1            // monthly
    public var customRule = CustomRule()
    public var assignedTo: Int?
    public var hasDueDate = false
    public var remindDaysBefore: Int?
    public var startDate: String?
    public var active = true
    public init() {}
    public init(_ rt: RecurringTodo) {
        title = rt.title; description = rt.description ?? ""; type = rt.recurrenceType
        weekday = rt.recurrenceDay ?? 0; dayOfMonth = rt.recurrenceDay ?? 1
        customRule = rt.customRule ?? CustomRule(); assignedTo = rt.assignedTo
        hasDueDate = rt.hasDueDate; remindDaysBefore = rt.remindDaysBefore
        startDate = rt.startDate; active = rt.active
    }

    var recurrenceDay: Int? {
        switch type { case "weekly": weekday; case "monthly": dayOfMonth; default: nil }
    }
    var rule: CustomRule? { type == "custom" ? customRule : nil }

    func previewBody() throws -> PatchBody {
        try PatchBody()
            .set("recurrence_type", Patch.value(type))
            .set("recurrence_day", Patch.clearing(recurrenceDay))
            .set("custom_rule", Patch.clearing(rule))
            .set("start_date", Patch.clearing(startDate))
    }

    func body(isCreate: Bool) throws -> PatchBody {
        let desc = description.trimmingCharacters(in: .whitespacesAndNewlines)
        var body = try previewBody()
            .set("title", Patch.value(title.trimmingCharacters(in: .whitespaces)))
            .set("description", Patch.clearing(desc.isEmpty ? nil : desc))
            .set("assigned_to", Patch.clearing(assignedTo))
            .set("has_due_date", Patch.value(hasDueDate))
            .set("remind_days_before", Patch.clearing(hasDueDate ? remindDaysBefore : nil))
        if !isCreate { body = try body.set("active", Patch.value(active)) }
        return body
    }
}

extension APIClient {
    public func todos() async throws -> [Todo] { try await get(.todos) }

    public func createTodo(_ draft: TodoDraft) async throws -> Todo {
        try await send(.createTodo, body: draft.body())
    }

    public func updateTodo(id: Int, _ draft: TodoDraft) async throws -> Todo {
        try await send(.updateTodo, ["todo_id": String(id)], body: draft.body())
    }

    public func setTodoCompleted(id: Int, _ done: Bool) async throws -> Todo {
        try await send(.updateTodo, ["todo_id": String(id)], body: PatchBody().set("completed", Patch.value(done)))
    }

    public func deleteTodo(id: Int) async throws { try await perform(.deleteTodo, ["todo_id": String(id)]) }

    public func completedTodos(sort: CompletedSort, assignees: Set<String>, search: String,
                               limit: Int = 50, offset: Int = 0) async throws -> ArchivePage<Todo> {
        var query = [URLQueryItem(name: "sort", value: sort.rawValue),
                     URLQueryItem(name: "limit", value: String(limit)),
                     URLQueryItem(name: "offset", value: String(offset))]
        let term = search.trimmingCharacters(in: .whitespaces)
        if !term.isEmpty { query.append(URLQueryItem(name: "search", value: term)) }
        for a in assignees.sorted() { query.append(URLQueryItem(name: "assignee", value: a)) }
        return try await get(.completedTodos, query: query)
    }

    public func recurringTodos() async throws -> [RecurringTodo] { try await get(.recurringTodos) }

    public func createRecurringTodo(_ draft: RecurringDraft) async throws -> RecurringTodo {
        try await send(.createRecurringTodo, body: draft.body(isCreate: true))
    }

    public func updateRecurringTodo(id: Int, _ draft: RecurringDraft) async throws -> RecurringTodo {
        try await send(.updateRecurringTodo, ["rt_id": String(id)], body: draft.body(isCreate: false))
    }

    public func deleteRecurringTodo(id: Int) async throws { try await perform(.deleteRecurringTodo, ["rt_id": String(id)]) }

    /// What dates an unsaved rule produces — so the editor reads it back as dates
    /// without reimplementing the recurrence math here.
    public func previewRecurrence(_ draft: RecurringDraft) async throws -> [String] {
        let preview: RecurrencePreview = try await send(.previewRecurrence, body: draft.previewBody())
        return preview.occurrences
    }
}
