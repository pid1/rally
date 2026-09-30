import Foundation
import Observation

/// The Tasks screen: open todos, the ones done today, and the recurring
/// templates that feed them.
@MainActor @Observable
public final class TodosModel {
    public private(set) var todos: [Todo] = []
    public private(set) var recurring: [RecurringTodo] = []
    public private(set) var hasLoaded = false
    public var sort: TodoSort = .dueSoonest
    public var assignees: Set<String> = []
    public var errorMessage: String?

    private let client: APIClient
    public init(client: APIClient) { self.client = client }

    public func visible(members: [FamilyMember]) -> [Todo] {
        TodoLogic.sorted(TodoLogic.filtered(todos, assignees: assignees), by: sort, members: members)
    }

    /// Chips for the people who have a task, plus Unassigned, plus any selected
    /// one — a filter that cannot be seen cannot be undone.
    public func chips(members: [FamilyMember]) -> [FilterChip] {
        let present = Set(todos.map(TodoLogic.assigneeKey)).union(assignees)
        var chips = members.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            .filter { present.contains(String($0.id)) }
            .map { FilterChip(value: String($0.id), title: $0.name) }
        if present.contains(TodoLogic.unassigned) { chips.append(.init(value: TodoLogic.unassigned, title: "Unassigned")) }
        return chips
    }

    public func toggleAssignee(_ value: String) {
        if assignees.contains(value) { assignees.remove(value) } else { assignees.insert(value) }
    }

    public func load() async {
        do {
            // Listing todos is also what makes any due recurring task appear, so it goes first.
            todos = try await client.todos()
            recurring = try await client.recurringTodos()
        } catch {
            if !hasLoaded { errorMessage = error.localizedDescription }
        }
        hasLoaded = true
    }

    public func setCompleted(_ todo: Todo, _ done: Bool) async {
        if let i = todos.firstIndex(where: { $0.id == todo.id }) {
            todos[i].completed = done
            todos[i].completedAt = done ? .now : nil
        }
        do { _ = try await client.setTodoCompleted(id: todo.id, done) }
        catch { errorMessage = error.localizedDescription }
        // Completing a recurring task is what generates its successor.
        await load()
    }

    public func delete(_ todo: Todo) async {
        todos.removeAll { $0.id == todo.id }
        do { try await client.deleteTodo(id: todo.id) }
        catch { errorMessage = error.localizedDescription; await load() }
    }

    @discardableResult
    public func save(_ draft: TodoDraft, editing todo: Todo?) async -> Bool {
        do {
            if let todo { _ = try await client.updateTodo(id: todo.id, draft) }
            else { _ = try await client.createTodo(draft) }
            await load()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    public func save(_ draft: RecurringDraft, editing rt: RecurringTodo?) async -> Bool {
        do {
            if let rt { _ = try await client.updateRecurringTodo(id: rt.id, draft) }
            else { _ = try await client.createRecurringTodo(draft) }
            await load()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    public func delete(_ rt: RecurringTodo) async -> Bool {
        do { try await client.deleteRecurringTodo(id: rt.id); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }
}

/// Tasks completed before today: read-only, searched, sorted, filtered and paged
/// on the server.
@MainActor @Observable
public final class CompletedTodosModel {
    public private(set) var items: [Todo] = []
    public private(set) var total = 0
    public private(set) var hasMore = false
    public private(set) var isLoading = false
    public private(set) var hasLoaded = false
    public var sort: CompletedSort = .completedNewest
    public var assignees: Set<String> = []
    public var search = ""
    public var errorMessage: String?

    private let client: APIClient
    private let pageSize: Int
    private var generation = 0

    public init(client: APIClient, pageSize: Int = 50) { self.client = client; self.pageSize = pageSize }

    public func toggleAssignee(_ value: String) {
        if assignees.contains(value) { assignees.remove(value) } else { assignees.insert(value) }
    }

    public func reload() async {
        generation += 1
        let mine = generation
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        do {
            let page = try await client.completedTodos(sort: sort, assignees: assignees, search: search, limit: pageSize)
            guard mine == generation else { return }
            items = page.items; total = page.total; hasMore = page.hasMore; hasLoaded = true
        } catch {
            guard mine == generation else { return }
            errorMessage = error.localizedDescription
        }
    }

    public func loadMore() async {
        guard hasMore, !isLoading else { return }
        let mine = generation
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        do {
            let page = try await client.completedTodos(sort: sort, assignees: assignees, search: search,
                                                       limit: pageSize, offset: items.count)
            guard mine == generation else { return }
            items += page.items; total = page.total; hasMore = page.hasMore
        } catch {
            guard mine == generation else { return }
            errorMessage = error.localizedDescription
        }
    }
}
