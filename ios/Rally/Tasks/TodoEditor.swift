import RallyKit
import SwiftUI

struct TodoEditor: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var app
    let model: TodosModel
    let target: TodoTarget

    @State private var draft = TodoDraft()
    @State private var saving = false
    @FocusState private var titleFocused: Bool

    private var original: Todo? { if case .edit(let t) = target { t } else { nil } }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Title", text: $draft.title).focused($titleFocused).accessibilityIdentifier("todo-title")
                    TextField("Description", text: $draft.description, axis: .vertical)
                }
                Section {
                    AssigneePicker(selection: $draft.assignedTo, members: app.members)
                    Toggle("Due date", isOn: $draft.dueDate.isPresent())
                    if draft.dueDate != nil {
                        DatePicker("Due", selection: $draft.dueDate.day(), displayedComponents: .date)
                        ReminderPicker(selection: $draft.remindDaysBefore)
                    }
                }
            }
            .navigationTitle(original == nil ? "Add Task" : "Edit Task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(draft.title.trimmingCharacters(in: .whitespaces).isEmpty || saving)
                        .accessibilityIdentifier("todo-save")
                }
            }
            .onAppear { if let original { draft = TodoDraft(original) } else { titleFocused = true } }
        }
        .presentationDetents([.medium, .large])
    }

    private func save() async {
        saving = true
        defer { saving = false }
        if await model.save(draft, editing: original) { dismiss() }
    }
}

struct AssigneePicker: View {
    @Binding var selection: Int?
    let members: [FamilyMember]

    var body: some View {
        Picker("Assigned to", selection: $selection) {
            Text("Unassigned").tag(Int?.none)
            ForEach(members) { Text($0.name).tag(Int?.some($0.id)) }
        }
    }
}

/// How many days before the due date a task starts appearing in briefings.
struct ReminderPicker: View {
    @Binding var selection: Int?
    var body: some View {
        Picker("Remind", selection: $selection) {
            Text("Always").tag(Int?.none)
            ForEach([0, 1, 2, 3, 5, 7, 14, 30], id: \.self) { days in
                Text(days == 0 ? "On the day" : "\(days) day\(days == 1 ? "" : "s") before").tag(Int?.some(days))
            }
        }
    }
}
