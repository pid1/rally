import RallyKit
import SwiftUI

struct TasksView: View {
    @Environment(AppModel.self) private var app
    @State private var model: TodosModel
    @State private var editingTodo: TodoTarget?
    @State private var editingRecurring: RecurringTarget?

    init(client: APIClient) { _model = State(initialValue: TodosModel(client: client)) }

    private var visible: [Todo] { model.visible(members: app.members) }

    var body: some View {
        List {
            Section {
                if model.hasLoaded && visible.isEmpty {
                    ContentUnavailableView(model.assignees.isEmpty ? "All caught up" : "Nothing for that filter",
                                           systemImage: "checklist",
                                           description: Text(model.assignees.isEmpty ? "Tap + to add a task." : ""))
                        .listRowBackground(Color.clear)
                }
                ForEach(visible) { todo in
                    TodoRow(todo: todo, members: app.members,
                            toggle: { Task { await model.setCompleted(todo, !todo.completed) } },
                            edit: { editingTodo = .edit(todo) })
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) { Task { await model.delete(todo) } } label: { Label("Delete", systemImage: "trash") }
                        }
                }
            }

            Section("Recurring") {
                if model.recurring.isEmpty {
                    Text("Chores that come back on a schedule live here.").foregroundStyle(RallyDesign.color("inkMuted"))
                }
                ForEach(model.recurring) { rt in
                    Button { editingRecurring = .edit(rt) } label: { RecurringRow(rt: rt, members: app.members) }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("recurring-row-\(rt.title)")
                }
            }

            Section {
                NavigationLink("View completed tasks") { CompletedTasksView(client: app.client) }
                    .accessibilityIdentifier("tasks-completed")
            }
        }
        .listStyle(.insetGrouped)
        .safeAreaInset(edge: .top, spacing: 0) {
            ChipBar(chips: model.chips(members: app.members), selected: model.assignees,
                    toggle: model.toggleAssignee, clear: { model.assignees = [] })
        }
        .navigationTitle("Tasks")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Menu {
                    Picker("Sort", selection: Bindable(model).sort) {
                        ForEach(TodoSort.allCases) { Text($0.title).tag($0) }
                    }
                } label: { Image(systemName: "arrow.up.arrow.down") }
                    .accessibilityLabel("Sort")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("Add Task", systemImage: "checklist") { editingTodo = .add }
                        .accessibilityIdentifier("tasks-add-task")
                    Button("Add Recurring Task", systemImage: "repeat") { editingRecurring = .add }
                } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Add")
                    .accessibilityIdentifier("tasks-add")
            }
        }
        .refreshable { await model.load() }
        .task(id: editingTodo == nil && editingRecurring == nil) {
            guard editingTodo == nil, editingRecurring == nil else { return }
            while !Task.isCancelled {
                await model.load()
                try? await Task.sleep(for: .seconds(60))
            }
        }
        .sheet(item: $editingTodo) { TodoEditor(model: model, target: $0) }
        .sheet(item: $editingRecurring) { RecurringEditor(model: model, client: app.client, target: $0) }
        .alert("Couldn't do that", isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.errorMessage ?? "") }
    }
}

enum TodoTarget: Identifiable {
    case add, edit(Todo)
    var id: String { switch self { case .add: "add"; case .edit(let t): "edit-\(t.id)" } }
}

enum RecurringTarget: Identifiable {
    case add, edit(RecurringTodo)
    var id: String { switch self { case .add: "add"; case .edit(let t): "edit-\(t.id)" } }
}

struct TodoRow: View {
    let todo: Todo
    let members: [FamilyMember]
    let toggle: () -> Void
    let edit: () -> Void

    var body: some View {
        HStack(spacing: RallyDesign.space[3]) {
            Button(action: toggle) {
                Image(systemName: todo.completed ? "checkmark.circle.fill" : "circle")
                    .font(.title2)
                    .foregroundStyle(todo.completed ? RallyDesign.color("inkSubtle") : RallyDesign.color("ink"))
                    .frame(width: RallyDesign.targetMin, height: RallyDesign.targetMin)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(todo.completed ? "Mark \(todo.title) as not done" : "Mark \(todo.title) as done")

            Button(action: edit) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(todo.title)
                        .strikethrough(todo.completed)
                        .foregroundStyle(todo.completed ? RallyDesign.color("inkSubtle") : RallyDesign.color("ink"))
                    if let d = todo.description, !d.isEmpty {
                        Text(d).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")).lineLimit(2)
                    }
                    HStack(spacing: RallyDesign.space[2]) {
                        if let member = members.first(where: { $0.id == todo.assignedTo }) { MemberLabel(member: member) }
                        if let label = TodoLogic.dueLabel(todo.dueDate) { Text(label).foregroundStyle(dueColor) }
                        if todo.recurringTodoID != nil { Image(systemName: "repeat").accessibilityLabel("Recurring") }
                    }
                    .font(.caption)
                    .foregroundStyle(RallyDesign.color("inkMuted"))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .frame(minHeight: RallyDesign.targetMin)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("todo-row-\(todo.title)")
        }
    }

    private var dueColor: Color {
        switch TodoLogic.status(of: todo) {
        case .overdue: RallyDesign.color("stateOverdue")
        case .today: RallyDesign.color("stateDue")
        default: RallyDesign.color("inkMuted")
        }
    }
}

struct RecurringRow: View {
    let rt: RecurringTodo
    let members: [FamilyMember]

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(rt.title).foregroundStyle(rt.active ? RallyDesign.color("ink") : RallyDesign.color("inkSubtle"))
            HStack(spacing: RallyDesign.space[2]) {
                Text(TodoLogic.describe(rt) + (TodoLogic.startsNote(rt.startDate).map { " · \($0)" } ?? ""))
                if let member = members.first(where: { $0.id == rt.assignedTo }) { MemberLabel(member: member) }
                if !rt.active { Text("Paused") }
            }
            .font(.caption).foregroundStyle(RallyDesign.color("inkMuted"))
        }
        .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin, alignment: .leading)
        .contentShape(Rectangle())
    }
}
