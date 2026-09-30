import Foundation
import Observation

/// The shopping list as the screen sees it: what is loaded, what is filtered,
/// and every change, applied optimistically and reconciled with the server.
@MainActor @Observable
public final class ShoppingModel {
    public private(set) var stores: [ShoppingStore] = []
    public private(set) var items: [ShoppingItem] = []
    public var selected: Set<String> = []
    public private(set) var hasLoaded = false
    public private(set) var loadFailed = false
    /// A failed change, in words for the person who made it.
    public var errorMessage: String?

    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    public var groups: [ShoppingGroup] { ShoppingLogic.groups(stores: stores, items: items, selected: selected) }
    public var chips: [FilterChip] { ShoppingLogic.chips(stores: stores, items: items, selected: selected) }

    // MARK: Loading

    public func load() async {
        do {
            async let s = client.shoppingStores()
            async let i = client.shoppingItems()
            (stores, items) = try await (s, i)
            loadFailed = false
        } catch {
            // A list already on screen is better than a blank one: keep it.
            loadFailed = true
            if !hasLoaded { errorMessage = error.localizedDescription }
        }
        hasLoaded = true
    }

    public func toggleFilter(_ value: String) {
        if selected.contains(value) { selected.remove(value) } else { selected.insert(value) }
    }

    public func clearFilters() { selected = [] }

    // MARK: Items

    @discardableResult
    public func add(name: String, note: String?, storeID: Int?) async -> Bool {
        await perform {
            _ = try await self.client.createShoppingItem(name: name, note: note, storeID: storeID)
            await self.load()
        }
    }

    /// Save an edit. `store_id` goes only when it changed, because the server
    /// re-places an item at the top of its new group whenever one is sent.
    @discardableResult
    public func save(_ original: ShoppingItem, name: String, note: String?, storeID: Int?) async -> Bool {
        let cleanNote = note?.trimmingCharacters(in: .whitespacesAndNewlines)
        return await perform {
            _ = try await self.client.updateShoppingItem(
                id: original.id,
                name: name == original.name ? .unset : .value(name),
                note: (cleanNote ?? "") == (original.note ?? "") ? .unset : .clearing(cleanNote.flatMap { $0.isEmpty ? nil : $0 }),
                storeID: storeID == original.storeID ? .unset : .clearing(storeID))
            await self.load()
        }
    }

    public func setCompleted(_ item: ShoppingItem, _ done: Bool) async {
        replace(item.id) { $0.completed = done }
        await perform(reload: true) {
            _ = try await self.client.updateShoppingItem(id: item.id, completed: .value(done))
            await self.load()
        }
    }

    public func delete(_ item: ShoppingItem) async {
        items.removeAll { $0.id == item.id }
        await perform(reload: true) {
            try await self.client.deleteShoppingItem(id: item.id)
        }
    }

    /// Drag within one store's open items.
    public func move(group key: String, from source: IndexSet, to destination: Int) async {
        guard let group = groups.first(where: { $0.key == key }) else { return }
        let ids = ShoppingLogic.reordered(group.openItems.map(\.id), from: source, to: destination)
        for (position, id) in ids.enumerated() { replace(id) { $0.sortOrder = position } }
        items = items.stableSorted()
        await perform(reload: true) {
            try await self.client.reorderShoppingItems(storeID: group.storeID, itemIDs: ids)
        }
    }

    // MARK: Stores

    @discardableResult
    public func addStore(_ name: String) async -> Bool {
        await perform { self.stores = ShoppingLogic.sortedStores(self.stores + [try await self.client.createShoppingStore(name: name)]) }
    }

    @discardableResult
    public func renameStore(_ store: ShoppingStore, to name: String) async -> Bool {
        await perform {
            let updated = try await self.client.renameShoppingStore(id: store.id, to: name)
            self.stores = ShoppingLogic.sortedStores(self.stores.map { $0.id == updated.id ? updated : $0 })
        }
    }

    @discardableResult
    public func deleteStore(_ store: ShoppingStore) async -> Bool {
        let ok = await perform {
            try await self.client.deleteShoppingStore(id: store.id)
            self.selected.remove(String(store.id))
        }
        await load() // its items are now under "Anywhere"
        return ok
    }

    // MARK: Plumbing

    private func replace(_ id: Int, _ change: (inout ShoppingItem) -> Void) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        change(&items[index])
    }

    /// Run a change; on failure say why and, for optimistic ones, put the list
    /// back to what the server says.
    @discardableResult
    private func perform(reload: Bool = false, _ work: @MainActor () async throws -> Void) async -> Bool {
        do { try await work(); return true }
        catch {
            errorMessage = error.localizedDescription
            if reload { await load() }
            return false
        }
    }
}

extension Array where Element == ShoppingItem {
    /// Open items by hand-arranged position, purchased ones after them — the
    /// order the server returns, reproduced so a drag does not wait on it.
    func stableSorted() -> [ShoppingItem] {
        enumerated().sorted { a, b in
            if a.element.completed != b.element.completed { return !a.element.completed }
            if !a.element.completed, a.element.storeID == b.element.storeID, a.element.sortOrder != b.element.sortOrder {
                return a.element.sortOrder < b.element.sortOrder
            }
            return a.offset < b.offset
        }.map(\.element)
    }
}
