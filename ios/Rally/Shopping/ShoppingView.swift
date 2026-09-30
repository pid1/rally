import RallyKit
import SwiftUI

struct ShoppingView: View {
    @Environment(AppModel.self) private var app
    @State private var model: ShoppingModel
    @State private var editing: EditorTarget?
    @State private var showingStores = false
    @State private var editMode: EditMode = .inactive

    init(client: APIClient) {
        _model = State(initialValue: ShoppingModel(client: client))
    }

    var body: some View {
        List {
            if model.hasLoaded && model.groups.isEmpty {
                ContentUnavailableView("Nothing on the list yet", systemImage: "cart",
                                       description: Text("Tap + to add the first item."))
                    .listRowBackground(Color.clear)
            }
            ForEach(model.groups) { group in
                Section {
                    ForEach(group.items) { item in
                        ShoppingRow(item: item,
                                    toggle: { Task { await model.setCompleted(item, !item.completed) } },
                                    edit: { editing = .edit(item) })
                            .moveDisabled(item.completed)
                            .swipeActions(edge: .trailing) {
                                Button(role: .destructive) { Task { await model.delete(item) } } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                    }
                    .onMove { source, destination in
                        // Only the open rows are arranged; purchased ones sit below.
                        let open = group.openItems.count
                        guard source.allSatisfy({ $0 < open }) else { return }
                        Task { await model.move(group: group.key, from: source, to: min(destination, open)) }
                    }
                } header: {
                    HStack {
                        Text(group.title)
                        Spacer()
                        Text("\(group.openItems.count)").monospacedDigit()
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .environment(\.editMode, $editMode)
        .safeAreaInset(edge: .top, spacing: 0) { FilterBar(model: model, manageStores: { showingStores = true }) }
        .navigationTitle("Shopping")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                if !model.groups.isEmpty { EditButton().accessibilityIdentifier("shopping-reorder") }
            }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink { PurchasedView(client: app.client) } label: { Image(systemName: "clock.arrow.circlepath") }
                    .accessibilityLabel("Purchased items")
                    .accessibilityIdentifier("shopping-purchased")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button { editing = .add } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Add item")
                    .accessibilityIdentifier("shopping-add")
            }
        }
        .refreshable { await model.load() }
        .task(id: editing == nil && !showingStores && editMode == .inactive) {
            // Refetch while the list is just being looked at. Re-rendering under
            // an open editor or a drag in progress would take it out of the
            // person's hand, so the loop only runs when neither is happening.
            guard editing == nil, !showingStores, editMode == .inactive else { return }
            while !Task.isCancelled {
                await model.load()
                try? await Task.sleep(for: .seconds(60))
            }
        }
        .sheet(item: $editing) { target in
            ItemEditor(model: model, client: app.client, target: target)
        }
        .sheet(isPresented: $showingStores) { ManageStoresView(model: model) }
        .alert("Couldn't do that", isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.errorMessage ?? "") }
    }
}

enum EditorTarget: Identifiable {
    case add
    case edit(ShoppingItem)
    var id: String {
        switch self { case .add: "add"; case .edit(let item): "edit-\(item.id)" }
    }
}

private struct ShoppingRow: View {
    let item: ShoppingItem
    let toggle: () -> Void
    let edit: () -> Void

    var body: some View {
        HStack(spacing: RallyDesign.space[3]) {
            Button(action: toggle) {
                Image(systemName: item.completed ? "checkmark.circle.fill" : "circle")
                    .font(.title2)
                    .foregroundStyle(item.completed ? RallyDesign.color("inkSubtle") : RallyDesign.color("ink"))
                    .frame(width: RallyDesign.targetMin, height: RallyDesign.targetMin)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(item.completed ? "Mark \(item.name) as not purchased" : "Mark \(item.name) as purchased")

            Button(action: edit) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name)
                        .strikethrough(item.completed)
                        .foregroundStyle(item.completed ? RallyDesign.color("inkSubtle") : RallyDesign.color("ink"))
                    if let note = item.note, !note.isEmpty {
                        Text(note).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .frame(minHeight: RallyDesign.targetMin)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("shopping-row-\(item.name)")
        }
    }
}

/// Store filter chips plus the way into managing stores. Chips describe what is
/// on the list, not which stores exist.
private struct FilterBar: View {
    let model: ShoppingModel
    let manageStores: () -> Void

    var body: some View {
        ChipBar(chips: model.chips, selected: model.selected, toggle: model.toggleFilter, clear: model.clearFilters) {
            Button(action: manageStores) {
                Label("Stores", systemImage: "storefront").frame(minHeight: RallyDesign.targetMin)
            }
            .accessibilityIdentifier("shopping-manage-stores")
        }
    }
}
