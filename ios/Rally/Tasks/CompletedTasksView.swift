import RallyKit
import SwiftUI

/// Tasks completed before today. Read-only: no add, edit, delete or checkbox.
struct CompletedTasksView: View {
    @Environment(AppModel.self) private var app
    @State private var model: CompletedTodosModel

    init(client: APIClient?) {
        _model = State(initialValue: CompletedTodosModel(client: client ?? APIClient(baseURL: URL(string: "http://invalid")!)))
    }

    private var chips: [FilterChip] {
        app.members.map { FilterChip(value: String($0.id), title: $0.name) }
            + [FilterChip(value: TodoLogic.unassigned, title: "Unassigned")]
    }

    var body: some View {
        List {
            if model.hasLoaded && model.items.isEmpty {
                ContentUnavailableView(model.search.isEmpty && model.assignees.isEmpty ? "Nothing completed yet" : "No matches",
                                       systemImage: "checkmark.circle").listRowBackground(Color.clear)
            }
            ForEach(model.items) { todo in
                VStack(alignment: .leading, spacing: 2) {
                    Text(todo.title)
                    if let d = todo.description, !d.isEmpty {
                        Text(d).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")).lineLimit(2)
                    }
                    HStack(spacing: RallyDesign.space[2]) {
                        if let member = app.members.first(where: { $0.id == todo.assignedTo }) { MemberLabel(member: member) }
                        if let due = TodoLogic.dueLabel(todo.dueDate) { Text("Due \(due)") }
                        if let at = todo.completedAt { Text("Done \(at.formatted(date: .abbreviated, time: .omitted))") }
                    }
                    .font(.caption).foregroundStyle(RallyDesign.color("inkMuted"))
                }
                .frame(minHeight: RallyDesign.targetMin, alignment: .leading)
            }
            if model.hasMore {
                Button { Task { await model.loadMore() } } label: {
                    Text(model.isLoading ? "Loading…" : "Load more").frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Completed")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: Bindable(model).search, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search completed tasks")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Sort", selection: Bindable(model).sort) {
                        ForEach(CompletedSort.allCases) { Text($0.title).tag($0) }
                    }
                } label: { Image(systemName: "arrow.up.arrow.down") }
                    .accessibilityLabel("Sort")
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            ChipBar(chips: chips, selected: model.assignees, toggle: model.toggleAssignee, clear: { model.assignees = [] })
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if model.hasLoaded {
                Text("\(model.total) completed task\(model.total == 1 ? "" : "s")")
                    .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                    .padding(RallyDesign.space[2]).frame(maxWidth: .infinity).background(.bar)
            }
        }
        .task(id: "\(model.search)|\(model.assignees.sorted())|\(model.sort.rawValue)") {
            if model.hasLoaded { try? await Task.sleep(for: .milliseconds(250)) }
            guard !Task.isCancelled else { return }
            await model.reload()
        }
        .alert("Couldn't load that", isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.errorMessage ?? "") }
    }
}
