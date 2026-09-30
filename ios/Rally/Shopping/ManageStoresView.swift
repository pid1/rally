import RallyKit
import SwiftUI

struct ManageStoresView: View {
    @Environment(\.dismiss) private var dismiss
    let model: ShoppingModel
    @State private var newName = ""
    @State private var renaming: ShoppingStore?
    @State private var renameText = ""
    @State private var deleting: ShoppingStore?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack {
                        TextField("New store", text: $newName)
                            .submitLabel(.done)
                            .onSubmit { Task { await add() } }
                            .accessibilityIdentifier("new-store-name")
                        Button("Add") { Task { await add() } }
                            .disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
                Section {
                    if model.stores.isEmpty {
                        Text("No stores yet. Items you add land under \"Anywhere\".")
                            .foregroundStyle(RallyDesign.color("inkMuted"))
                    }
                    ForEach(ShoppingLogic.sortedStores(model.stores)) { store in
                        Button(store.name) { renaming = store; renameText = store.name }
                            .foregroundStyle(RallyDesign.color("ink"))
                            .frame(minHeight: RallyDesign.targetMin)
                            .swipeActions { Button("Delete", role: .destructive) { deleting = store } }
                    }
                }
            }
            .navigationTitle("Stores")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .alert("Rename store", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Name", text: $renameText)
                Button("Save") { if let store = renaming { Task { await model.renameStore(store, to: renameText) } } }
                Button("Cancel", role: .cancel) {}
            }
            .confirmationDialog(
                "Delete \(deleting?.name ?? "store")?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                titleVisibility: .visible
            ) {
                Button("Delete", role: .destructive) { if let store = deleting { Task { await model.deleteStore(store) } } }
            } message: { Text("Its items move to \"Anywhere\" — nothing is lost.") }
        }
        .presentationDetents([.medium, .large])
    }

    private func add() async {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        if await model.addStore(name) { newName = "" }
    }
}
