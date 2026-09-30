import RallyKit
import SwiftUI

struct PreparednessView: View {
    @Environment(AppModel.self) private var app
    @State private var model: PrepModel
    @State private var editing: PrepTarget?
    @State private var showingLocations = false

    init(client: APIClient) { _model = State(initialValue: PrepModel(client: client)) }

    var body: some View {
        List {
            // Filters live in the list: pinned above a searchable screen they slid under the nav bar.
            Section {
                VStack(spacing: 0) {
                                ChipBar(chips: model.locationChips(), selected: model.filter.locations,
                                        toggle: { toggle(\.locations, $0) }, clear: { model.filter.locations = [] })
                                ChipBar(chips: PrepLogic.statusChips, selected: model.filter.statuses,
                                        toggle: { toggle(\.statuses, $0) }, clear: { model.filter.statuses = [] })
                            }
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
            }
            if model.hasLoaded && model.items.isEmpty {
                ContentUnavailableView(model.filter == PrepFilter() ? "No stock yet" : "Nothing matches",
                                       systemImage: "shippingbox",
                                       description: Text(model.filter == PrepFilter() ? "Tap + to add the first item." : ""))
                    .listRowBackground(Color.clear)
            }
            ForEach(Array(model.groups.enumerated()), id: \.offset) { _, group in
                Section(group.title) {
                    ForEach(group.items) { item in
                        PrepRow(item: item, refreshed: { Task { await model.refreshed(item) } }, edit: { editing = .edit(item) })
                    }
                }
            }
            Section {
                Button { showingLocations = true } label: {
                    Label("Locations", systemImage: "mappin.and.ellipse")
                }
                .accessibilityIdentifier("prep-manage-locations")
                NavigationLink("View go list") { GoListView(client: app.client) }.accessibilityIdentifier("prep-golist")
                if app.install.prepReviewEnabled {
                    NavigationLink("AI review") { PrepReviewView(client: app.client) }.accessibilityIdentifier("prep-review")
                }
            }
        }
        .listStyle(.insetGrouped)
        .searchable(text: Binding(get: { model.filter.search }, set: { model.filter.search = $0 }), placement: .navigationBarDrawer(displayMode: .always), prompt: "Search stock")
        .navigationTitle("Preparedness")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Menu {
                    Picker("Sort", selection: Bindable(model).filter.sort) {
                        ForEach(PrepFilter.Sort.allCases) { Text($0.title).tag($0) }
                    }
                } label: { Image(systemName: "arrow.up.arrow.down") }.accessibilityLabel("Sort")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button { editing = .add } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Add item").accessibilityIdentifier("prep-add")
            }
        }
        .refreshable { await model.load() }
        // Filters are server-side, so a change is a new request (search debounced).
        .task(id: "\(model.filter.search)|\(model.filter.sort.rawValue)|\(model.filter.locations.sorted())|\(model.filter.statuses.sorted())") {
            if model.hasLoaded { try? await Task.sleep(for: .milliseconds(250)) }
            guard !Task.isCancelled else { return }
            await model.load()
        }
        .sheet(item: $editing) { PrepItemEditor(model: model, target: $0) }
        .sheet(isPresented: $showingLocations) { PrepLocationsView(model: model) }
        .alert("Couldn't do that", isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.errorMessage ?? "") }
    }

    private func toggle(_ path: WritableKeyPath<PrepFilter, Set<String>>, _ value: String) {
        if model.filter[keyPath: path].contains(value) { model.filter[keyPath: path].remove(value) }
        else { model.filter[keyPath: path].insert(value) }
    }
}

enum PrepTarget: Identifiable {
    case add, edit(PrepItem)
    var id: String { switch self { case .add: "add"; case .edit(let i): "edit-\(i.id)" } }
}

struct PrepRow: View {
    let item: PrepItem
    let refreshed: () -> Void
    let edit: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: RallyDesign.space[3]) {
            Button(action: edit) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(item.name)
                        if let q = item.quantity, !q.isEmpty { Text("× \(q)").foregroundStyle(RallyDesign.color("inkMuted")) }
                    }
                    if let line = PrepLogic.statusLine(item) {
                        Text(line).font(.caption).foregroundStyle(color)
                    }
                    if let notes = item.notes, !notes.isEmpty {
                        Text(notes).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")).lineLimit(2)
                    }
                }
                .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("prep-row-\(item.name)")

            // The one action performed while standing in the garage holding the thing.
            if item.isScheduled {
                Button("Refreshed", action: refreshed)
                    .buttonStyle(.bordered)
                    .frame(minHeight: RallyDesign.targetMin)
                    .accessibilityLabel("Mark \(item.name) refreshed")
            }
        }
    }

    private var color: Color {
        switch item.status {
        case "overdue": RallyDesign.color("stateOverdue")
        case "due": RallyDesign.color("stateDue")
        default: RallyDesign.color("inkMuted")
        }
    }
}

struct PrepItemEditor: View {
    @Environment(\.dismiss) private var dismiss
    let model: PrepModel
    let target: PrepTarget
    @State private var draft = PrepItemDraft()
    @State private var saving = false
    @State private var confirmDelete = false
    @FocusState private var focused: Bool

    private var original: PrepItem? { if case .edit(let i) = target { i } else { nil } }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: $draft.name).focused($focused).accessibilityIdentifier("prep-name")
                    TextField("Quantity (e.g. 12 gallons)", text: $draft.quantity)
                    Picker("Location", selection: $draft.locationID) {
                        Text("Unassigned").tag(Int?.none)
                        ForEach(model.locations) { Text($0.name).tag(Int?.some($0.id)) }
                    }
                    TextField("Notes", text: $draft.notes, axis: .vertical)
                }
                Section("Refresh") {
                    Picker("Schedule", selection: $draft.mode) {
                        ForEach(RefreshMode.allCases) { Text($0.title).tag($0) }
                    }
                    switch draft.mode {
                    case .none: EmptyView()
                    case .date:
                        DatePicker("Refresh on", selection: $draft.nextDate.day(fallback: .now.addingTimeInterval(86400 * 90)),
                                   displayedComponents: .date)
                    case .interval:
                        Stepper("Every \(draft.intervalMonths) month\(draft.intervalMonths == 1 ? "" : "s")",
                                value: $draft.intervalMonths, in: 1...120)
                    }
                    if draft.mode != .none {
                        Picker("Remind", selection: $draft.remindDaysBefore) {
                            Text("Usual lead time").tag(Int?.none)
                            ForEach([1, 3, 7, 14, 30, 60], id: \.self) { Text("\($0) days before").tag(Int?.some($0)) }
                        }
                    }
                }
                if original != nil { Section { Button("Delete Item", role: .destructive) { confirmDelete = true } } }
            }
            .navigationTitle(original == nil ? "Add Item" : "Edit Item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(!canSave).accessibilityIdentifier("prep-save")
                }
            }
            .onAppear { if let original { draft = PrepItemDraft(original) } else { focused = true } }
            // A date-mode item with no date would never notify; give it one as soon as the mode is chosen.
            .onChange(of: draft.mode) { _, mode in
                if mode == .date, draft.nextDate == nil { draft.nextDate = DayString.string(.now.addingTimeInterval(86400 * 90)) }
            }
            .confirmationDialog("Delete this item?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { Task { if let i = original, await model.delete(i) { dismiss() } } }
            }
        }
        .presentationDetents([.large])
    }

    private var canSave: Bool {
        !draft.name.trimmingCharacters(in: .whitespaces).isEmpty && !saving && (draft.mode != .date || draft.nextDate != nil)
    }

    private func save() async {
        saving = true
        defer { saving = false }
        if await model.save(draft, editing: original) { dismiss() }
    }
}

/// Where stock lives, in the order you walk. A go list is packed in that order.
struct PrepLocationsView: View {
    @Environment(\.dismiss) private var dismiss
    let model: PrepModel
    @State private var newName = ""
    @State private var renaming: PrepLocation?
    @State private var renameText = ""
    @State private var deleting: PrepLocation?
    @State private var editMode: EditMode = .inactive

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack {
                        TextField("New location", text: $newName).submitLabel(.done)
                            .onSubmit { Task { await add() } }
                            .accessibilityIdentifier("new-location-name")
                        Button("Add") { Task { await add() } }.disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
                Section {
                    ForEach(model.locations) { location in
                        Button(location.name) { renaming = location; renameText = location.name }
                            .foregroundStyle(RallyDesign.color("ink")).frame(minHeight: RallyDesign.targetMin)
                            .swipeActions { Button("Delete", role: .destructive) { deleting = location } }
                    }
                    .onMove { source, destination in Task { await model.moveLocation(from: source, to: destination) } }
                } footer: { Text("Drag to set walking order — the go list is packed in this order.") }
            }
            .environment(\.editMode, $editMode)
            .navigationTitle("Locations")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { EditButton() }
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .alert("Rename location", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Name", text: $renameText)
                Button("Save") { if let l = renaming { Task { await model.renameLocation(l, to: renameText) } } }
                Button("Cancel", role: .cancel) {}
            }
            .confirmationDialog("Delete \(deleting?.name ?? "location")?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                                titleVisibility: .visible) {
                Button("Delete", role: .destructive) { if let l = deleting { Task { await model.deleteLocation(l) } } }
            } message: { Text("Its items become Unassigned — nothing drops off the go list.") }
        }
        .presentationDetents([.medium, .large])
    }

    private func add() async {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        if await model.addLocation(name) { newName = "" }
    }
}
