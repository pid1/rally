import RallyKit
import SwiftUI

struct RecurringEditor: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var app
    let model: TodosModel
    let client: APIClient?
    let target: RecurringTarget

    @State private var draft = RecurringDraft()
    @State private var preview: [String] = []
    @State private var previewFailed = false
    @State private var saving = false
    @FocusState private var titleFocused: Bool
    @State private var confirmDelete = false

    private var original: RecurringTodo? { if case .edit(let t) = target { t } else { nil } }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Title", text: $draft.title).focused($titleFocused).accessibilityIdentifier("recurring-title")
                    TextField("Description", text: $draft.description, axis: .vertical)
                }

                Section("Repeats") {
                    Picker("Schedule", selection: $draft.type) {
                        Text("Daily").tag("daily"); Text("Weekly").tag("weekly")
                        Text("Monthly").tag("monthly"); Text("Custom").tag("custom")
                    }
                    switch draft.type {
                    case "weekly":
                        Picker("On", selection: $draft.weekday) {
                            ForEach(TodoLogic.weekdayNames.indices, id: \.self) { Text(TodoLogic.weekdayNames[$0]).tag($0) }
                        }
                    case "monthly":
                        Stepper("On the \(TodoLogic.ordinal(draft.dayOfMonth))", value: $draft.dayOfMonth, in: 1...31)
                    case "custom":
                        CustomRuleFields(rule: $draft.customRule)
                    default: EmptyView()
                    }
                    if let line = previewLine {
                        Text(line).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                            .accessibilityIdentifier("recurring-preview")
                    }
                }

                Section {
                    AssigneePicker(selection: $draft.assignedTo, members: app.members)
                    Toggle("Has a due date", isOn: $draft.hasDueDate)
                    if draft.hasDueDate { ReminderPicker(selection: $draft.remindDaysBefore) }
                }

                Section {
                    Toggle("Start on a specific date", isOn: $draft.startDate.isPresent())
                    if draft.startDate != nil {
                        DatePicker("First task", selection: $draft.startDate.day(), displayedComponents: .date)
                    }
                } footer: {
                    Text("Leave off to start on the next occurrence from today.")
                }

                if original != nil {
                    Section {
                        Toggle("Active", isOn: $draft.active)
                        Button("Delete Recurring Task", role: .destructive) { confirmDelete = true }
                    }
                }
            }
            .navigationTitle(original == nil ? "Add Recurring Task" : "Edit Recurring Task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(draft.title.trimmingCharacters(in: .whitespaces).isEmpty || saving)
                        .accessibilityIdentifier("recurring-save")
                }
            }
            .onAppear { if let original { draft = RecurringDraft(original) } else { titleFocused = true } }
            // Ask the server, which owns the recurrence math, what this rule produces.
            .task(id: draft) { await refreshPreview() }
            .confirmationDialog("Delete this recurring task?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    Task { if let original, await model.delete(original) { dismiss() } }
                }
            } message: { Text("Tasks already created from it stay on your list.") }
        }
        .presentationDetents([.large])
    }

    private var previewLine: String? {
        if previewFailed { return nil }
        guard let first = preview.first, let date = DayString.date(first) else { return nil }
        let rest = preview.dropFirst().compactMap { DayString.date($0)?.formatted(.dateTime.month(.abbreviated).day().year()) }
        let head = "First task: \(date.formatted(.dateTime.weekday(.wide).month(.wide).day().year()))"
        return rest.isEmpty ? head : head + " — then " + rest.prefix(2).joined(separator: ", ")
    }

    private func refreshPreview() async {
        try? await Task.sleep(for: .milliseconds(300))
        guard !Task.isCancelled, let client else { return }
        do { preview = try await client.previewRecurrence(draft); previewFailed = false }
        catch { if !Task.isCancelled { previewFailed = true } }
    }

    private func save() async {
        saving = true
        defer { saving = false }
        if await model.save(draft, editing: original) { dismiss() }
    }
}

/// The Custom… rule: an interval over days, weeks or months, several weekdays at
/// once, and a monthly rule by date or by position.
struct CustomRuleFields: View {
    @Binding var rule: CustomRule

    var body: some View {
        Picker("Every", selection: $rule.freq) {
            Text("Days").tag(CustomRule.Freq.daily)
            Text("Weeks").tag(CustomRule.Freq.weekly)
            Text("Months").tag(CustomRule.Freq.monthly)
        }
        .pickerStyle(.segmented)
        Stepper(intervalText, value: $rule.interval, in: 1...99)

        switch rule.freq {
        case .daily:
            Toggle("Weekdays only", isOn: $rule.weekdaysOnly)
            Picker("Next one is due", selection: $rule.nextDueFromCompletion) {
                Text("From the due date").tag(false)
                Text("From when it's done").tag(true)
            }
        case .weekly:
            HStack(spacing: RallyDesign.space[1]) {
                ForEach(0..<7, id: \.self) { index in
                    let on = rule.weekdays.contains(index)
                    Button {
                        if on { rule.weekdays.removeAll { $0 == index } } else { rule.weekdays.append(index) }
                    } label: {
                        Text(String(TodoLogic.weekdayNames[index].prefix(1)))
                            .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin)
                            .background(on ? RallyDesign.color("ink") : RallyDesign.color("surfaceSunken"), in: Circle())
                            .foregroundStyle(on ? RallyDesign.color("surface") : RallyDesign.color("ink"))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(TodoLogic.weekdayNames[index])
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
        case .monthly:
            Picker("By", selection: $rule.mode) {
                Text("Day of month").tag(CustomRule.MonthlyMode.day)
                Text("Weekday").tag(CustomRule.MonthlyMode.weekday)
            }
            .pickerStyle(.segmented)
            if rule.mode == .day {
                Stepper("On the \(TodoLogic.ordinal(rule.day))", value: $rule.day, in: 1...31)
            } else {
                Picker("Position", selection: $rule.ordinal) {
                    ForEach(["first", "second", "third", "fourth", "last"], id: \.self) { Text($0.capitalized).tag($0) }
                }
                Picker("Weekday", selection: $rule.weekday) {
                    ForEach(TodoLogic.weekdayNames.indices, id: \.self) { Text(TodoLogic.weekdayNames[$0]).tag($0) }
                }
            }
        }
    }

    private var intervalText: String {
        let unit = switch rule.freq { case .daily: "day"; case .weekly: "week"; case .monthly: "month" }
        return rule.interval == 1 ? "Every \(unit)" : "Every \(rule.interval) \(unit)s"
    }
}
