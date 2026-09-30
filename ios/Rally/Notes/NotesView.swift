import RallyKit
import SwiftUI

/// One Daily Note per day, from today onward. A day with no note has no card.
struct NotesView: View {
    @Environment(AppModel.self) private var app
    @State private var model: NotesModel
    @State private var editing: NoteTarget?

    init(client: APIClient) { _model = State(initialValue: NotesModel(client: client)) }

    var body: some View {
        List {
            if model.hasLoaded && model.notes.isEmpty {
                ContentUnavailableView("No notes yet", systemImage: "note.text",
                                       description: Text("Tap + to leave the family a note for today or a day ahead."))
                    .listRowBackground(Color.clear)
            }
            ForEach(model.notes) { note in
                Button { editing = .edit(note, notice: nil) } label: {
                    VStack(alignment: .leading, spacing: RallyDesign.space[2]) {
                        Text(NoteLogic.heading(note.date, calendar: app.install.calendar).uppercased())
                            .font(.caption.weight(.semibold)).tracking(1.2)
                            .foregroundStyle(RallyDesign.color("inkMuted"))
                        MarkdownText(markdown: note.body).foregroundStyle(RallyDesign.color("ink"))
                    }
                    .padding(.vertical, RallyDesign.space[1])
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("note-\(note.date)")
                .swipeActions { Button(role: .destructive) { Task { await model.delete(note) } } label: { Label("Delete", systemImage: "trash") } }
            }
            Section {
                NavigationLink("View previous notes") { PreviousNotesView(client: app.client) }
                    .accessibilityIdentifier("notes-previous")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Notes")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { editing = .add } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Add note").accessibilityIdentifier("notes-add")
            }
        }
        .refreshable { await model.load() }
        .task(id: editing == nil) {
            guard editing == nil else { return }
            while !Task.isCancelled { await model.load(); try? await Task.sleep(for: .seconds(60)) }
        }
        .sheet(item: $editing) { NoteEditor(model: model, target: $0) }
        .alert("Couldn't do that", isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.errorMessage ?? "") }
    }
}

enum NoteTarget: Identifiable {
    case add
    case edit(Note, notice: String?)
    var id: String { switch self { case .add: "add"; case .edit(let n, _): "edit-\(n.id)" } }
}

struct NoteEditor: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var app
    let model: NotesModel
    @State var target: NoteTarget

    @State private var date = DayString.string(.now)
    @State private var bodyText = ""
    @State private var notice: String?
    @State private var saving = false
    @State private var confirmDelete = false
    @FocusState private var focused: Bool

    init(model: NotesModel, target: NoteTarget) {
        self.model = model
        _target = State(initialValue: target)
    }

    private var editing: Note? { if case .edit(let n, _) = target { n } else { nil } }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    // Past days are read-only, so the picker cannot reach one.
                    DatePicker("Day", selection: Binding(get: { DayString.date(date) ?? .now },
                                                         set: { date = DayString.string($0) }),
                               in: app.install.calendar.startOfDay(for: .now)..., displayedComponents: .date)
                }
                Section {
                    TextEditor(text: $bodyText)
                        .focused($focused)
                        .frame(minHeight: 160)
                        .accessibilityIdentifier("note-body")
                } footer: {
                    if let notice { Text(notice).accessibilityIdentifier("note-notice") }
                    else { Text("Use **bold**, *italic*, “- ” for bullets and “1. ” for numbers. Every Enter is a new line.") }
                }
                if editing != nil {
                    Section { Button("Delete Note", role: .destructive) { confirmDelete = true } }
                }
            }
            .navigationTitle(editing == nil ? "Add Note" : "Edit Note")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || saving)
                        .accessibilityIdentifier("note-save")
                }
            }
            .onAppear { load(target) }
            .confirmationDialog("Delete this note?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { Task { if let n = editing, await model.delete(n) { dismiss() } } }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func load(_ target: NoteTarget) {
        switch target {
        case .add: date = app.install.today(); focused = true
        case .edit(let note, let message): date = note.date; bodyText = note.body; notice = message; focused = true
        }
    }

    private func save() async {
        saving = true
        defer { saving = false }
        // Adding to a day we already know has a note: switch before asking.
        if editing == nil, let existing = model.note(on: date) { switchTo(existing); return }
        switch await model.save(date: date, body: bodyText, editing: editing) {
        case .saved: dismiss()
        case .existsOnDay(let existing): switchTo(existing)
        case .failed: break
        }
    }

    private func switchTo(_ existing: Note) {
        let merged = NoteLogic.merged(existing: existing.body, typed: bodyText)
        target = .edit(existing, notice: merged.appended
                       ? "That day already had a note. Your text was added to the end."
                       : "That day already had a note — loaded for editing.")
        date = existing.date; bodyText = merged.body; notice = merged.appended
            ? "That day already had a note. Your text was added to the end."
            : "That day already had a note — loaded for editing."
    }
}

/// Days before today. Read-only: no edit, no delete, no add.
struct PreviousNotesView: View {
    @State private var loader: ArchiveLoader<Note, NoFilter>

    init(client: APIClient?) {
        let client = client ?? APIClient(baseURL: URL(string: "http://invalid")!)
        _loader = State(initialValue: ArchiveLoader<Note, NoFilter> { search, limit, offset in
            try await client.previousNotes(search: search, limit: limit, offset: offset)
        })
    }

    var body: some View {
        List {
            if loader.hasLoaded && loader.items.isEmpty {
                ContentUnavailableView(loader.search.isEmpty ? "No earlier notes" : "No matches", systemImage: "note.text")
                    .listRowBackground(Color.clear)
            }
            ForEach(loader.items) { note in
                VStack(alignment: .leading, spacing: RallyDesign.space[2]) {
                    Text(NoteLogic.heading(note.date).uppercased())
                        .font(.caption.weight(.semibold)).tracking(1.2)
                        .foregroundStyle(RallyDesign.color("inkMuted"))
                    MarkdownText(markdown: note.body)
                }
            }
            if loader.hasMore {
                Button { Task { await loader.loadMore() } } label: {
                    Text(loader.isLoading ? "Loading…" : "Load more").frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Previous Notes")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: Bindable(loader).search, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search notes")
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if loader.hasLoaded {
                Text("\(loader.total) note\(loader.total == 1 ? "" : "s")")
                    .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                    .padding(RallyDesign.space[2]).frame(maxWidth: .infinity).background(.bar)
            }
        }
        .task(id: loader.search) {
            if loader.hasLoaded { try? await Task.sleep(for: .milliseconds(250)) }
            guard !Task.isCancelled else { return }
            await loader.reload()
        }
        .alert("Couldn't load that", isPresented: Binding(
            get: { loader.errorMessage != nil }, set: { if !$0 { loader.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(loader.errorMessage ?? "") }
    }
}
