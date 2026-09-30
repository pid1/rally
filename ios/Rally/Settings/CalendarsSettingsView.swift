import RallyKit
import SwiftUI

struct CalendarsSettingsView: View {
    @Environment(AppModel.self) private var app
    @State private var model: CalendarAdminModel
    @State private var editing: CalendarTarget?

    init(client: APIClient) { _model = State(initialValue: CalendarAdminModel(client: client)) }

    var body: some View {
        List {
            do {
                ForEach(model.calendars) { cal in
                    Button { if !cal.isNative { editing = .edit(cal) } } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(cal.label)
                            Text("\(cal.typeTitle) · \(app.members.first { $0.id == cal.familyMemberID }?.name ?? "No owner")")
                                .font(.caption).foregroundStyle(RallyDesign.color("inkMuted"))
                        }
                        .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin, alignment: .leading).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("calendar-row-\(cal.label)")
                }
            }
        }
        .navigationTitle("Calendars")
        .navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom) {
            Text("Rally's own calendars (one per person) are made for you. Add an ICS feed or a Google / iCloud calendar to bring events in — they're read-only here.")
                .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")).padding(RallyDesign.space[3]).background(.bar)
        }
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { editing = .add } label: { Image(systemName: "plus") }.accessibilityLabel("Add calendar") } }
        .task { await model.load() }
        .sheet(item: $editing) { CalendarEditor(model: model, target: $0) }
    }
}

enum CalendarTarget: Identifiable {
    case add, edit(CalendarRow)
    var id: String { switch self { case .add: "add"; case .edit(let c): "edit-\(c.id)" } }
}

struct CalendarEditor: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var app
    let model: CalendarAdminModel
    let target: CalendarTarget
    @State private var draft = CalendarDraft()
    @State private var testResult: String?
    @State private var confirmDelete = false
    @State private var saving = false
    @State private var didSetUp = false

    private var original: CalendarRow? { if case .edit(let c) = target { c } else { nil } }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Label", text: $draft.label).accessibilityIdentifier("calendar-label")
                    Picker("Type", selection: $draft.type) { ForEach(CalendarDraft.types, id: \.self) { Text(CalendarDraft.typeTitle($0)).tag($0) } }
                    Picker("Belongs to", selection: $draft.memberID) {
                        Text("Choose…").tag(Int?.none)
                        ForEach(app.members) { Text($0.name).tag(Int?.some($0.id)) }
                    }
                }
                Section {
                    TextField(draft.type == "ics" ? "Feed URL" : "Server URL", text: $draft.url)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("Owner email (for declined events)", text: $draft.ownerEmail).keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                if draft.needsCredentials {
                    Section {
                        TextField("Account email", text: $draft.username).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
                        SecureField(original == nil ? "App-specific password" : "App-specific password (leave blank to keep)", text: $draft.password)
                    } footer: { Text("Use an app-specific password, never your account password. It is stored on your Rally server and never sent back.") }
                }
                if let original {
                    Section {
                        Button("Test connection") { Task { testResult = await model.test(original) } }
                        if let testResult { Text(testResult).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
                        Button("Delete Calendar", role: .destructive) { confirmDelete = true }
                    }
                }
            }
            .navigationTitle(original == nil ? "Add Calendar" : "Edit Calendar")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }.disabled(!canSave).accessibilityIdentifier("calendar-save")
                }
            }
            .onAppear { if !didSetUp { didSetUp = true; if let original { draft = CalendarDraft(original) } } }
            .confirmationDialog("Delete this calendar?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { Task { if let o = original, await model.delete(o) { dismiss() } } }
            } message: { Text("Its cached events go with it.") }
            .alert("Couldn't do that", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
                Button("OK") {}
            } message: { Text(model.errorMessage ?? "") }
        }
        .presentationDetents([.large])
    }

    private var canSave: Bool {
        !draft.label.trimmingCharacters(in: .whitespaces).isEmpty && !draft.url.trimmingCharacters(in: .whitespaces).isEmpty
            && draft.memberID != nil && !saving
    }

    private func save() async {
        saving = true
        defer { saving = false }
        if await model.save(draft, editing: original) { dismiss() }
    }
}
