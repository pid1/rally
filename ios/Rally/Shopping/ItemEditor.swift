import RallyKit
import SwiftUI

/// Add or edit one item. Adding reads its autocomplete from the permanent item
/// history on the server; editing is a correction, not a lookup, so it gets none.
struct ItemEditor: View {
    @Environment(\.dismiss) private var dismiss
    let model: ShoppingModel
    let client: APIClient?
    let target: EditorTarget

    @State private var name = ""
    @State private var note = ""
    @State private var storeID: Int?
    @State private var storeChosen = false
    @State private var suggestions: [ShoppingSuggestion] = []
    @State private var saving = false
    @FocusState private var nameFocused: Bool

    private var original: ShoppingItem? { if case .edit(let item) = target { item } else { nil } }
    private var isAdding: Bool { original == nil }
    private var canSave: Bool { !name.trimmingCharacters(in: .whitespaces).isEmpty && !saving }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Item", text: $name)
                        .focused($nameFocused)
                        .submitLabel(.done)
                        .accessibilityIdentifier("item-name")
                    if isAdding && !suggestions.isEmpty {
                        ForEach(suggestions) { suggestion in
                            HStack {
                                Button { accept(suggestion) } label: {
                                    Text(suggestion.name).frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin, alignment: .leading)
                                }
                                .buttonStyle(.plain)
                                Button { Task { await forget(suggestion) } } label: {
                                    Image(systemName: "xmark").foregroundStyle(RallyDesign.color("inkSubtle"))
                                        .frame(width: RallyDesign.targetMin, height: RallyDesign.targetMin)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("Forget \(suggestion.name)")
                            }
                        }
                    }
                }
                Section {
                    Picker("Store", selection: Binding(get: { storeID }, set: { storeID = $0; storeChosen = true })) {
                        Text("Anywhere").tag(Int?.none)
                        ForEach(ShoppingLogic.sortedStores(model.stores)) { Text($0.name).tag(Int?.some($0.id)) }
                    }
                    TextField("Note", text: $note, axis: .vertical)
                }
            }
            .navigationTitle(isAdding ? "Add Item" : "Edit Item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(!canSave)
                        .accessibilityIdentifier("item-save")
                }
            }
            .onAppear {
                if let original {
                    name = original.name; note = original.note ?? ""; storeID = original.storeID; storeChosen = true
                } else {
                    nameFocused = true // Store reads "Anywhere" on every open, whatever the chips say
                }
            }
            // A cancelled sleep is the debounce; the generation check is the
            // out-of-order guard — a slow reply for "mi" must not replace "milk".
            .task(id: name) { await lookUp(name) }
        }
        .presentationDetents([.medium, .large])
    }

    private func lookUp(_ text: String) async {
        guard isAdding, let client else { return }
        try? await Task.sleep(for: .milliseconds(150))
        guard !Task.isCancelled else { return }
        let found = (try? await client.shoppingSuggestions(matching: text)) ?? []
        guard !Task.isCancelled else { return }
        // Don't offer the thing already typed in full.
        suggestions = found.filter { $0.name.caseInsensitiveCompare(text.trimmingCharacters(in: .whitespaces)) != .orderedSame }
    }

    /// Fills the store **only when nobody has chosen one**. `note` is deliberately
    /// not restored: last week's note is usually wrong this week.
    private func accept(_ suggestion: ShoppingSuggestion) {
        name = suggestion.name
        if !storeChosen, let id = suggestion.storeID, model.stores.contains(where: { $0.id == id }) { storeID = id }
        suggestions = []
    }

    private func forget(_ suggestion: ShoppingSuggestion) async {
        try? await client?.forgetShoppingSuggestion(id: suggestion.id)
        suggestions.removeAll { $0.id == suggestion.id }
    }

    private func save() async {
        saving = true
        defer { saving = false }
        let clean = name.trimmingCharacters(in: .whitespaces)
        let ok: Bool
        if let original { ok = await model.save(original, name: clean, note: note, storeID: storeID) }
        else { ok = await model.add(name: clean, note: note, storeID: storeID) }
        if ok { dismiss() }
    }
}
