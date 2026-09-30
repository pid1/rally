import Foundation

extension APIClient {
    public func shoppingStores() async throws -> [ShoppingStore] { try await get(.shoppingStores) }

    public func createShoppingStore(name: String) async throws -> ShoppingStore {
        try await send(.createShoppingStore, body: PatchBody().set("name", Patch.value(name)))
    }

    public func renameShoppingStore(id: Int, to name: String) async throws -> ShoppingStore {
        try await send(.updateShoppingStore, ["store_id": String(id)], body: PatchBody().set("name", Patch.value(name)))
    }

    /// The server moves the store's items to "Anywhere" first, so nothing is lost.
    public func deleteShoppingStore(id: Int) async throws {
        try await perform(.deleteShoppingStore, ["store_id": String(id)])
    }

    public func shoppingItems() async throws -> [ShoppingItem] { try await get(.shoppingItems) }

    /// `200` with the existing row when an open item of that name is already in
    /// the store, `201` otherwise; either way the caller gets the row back.
    ///
    /// Pass `storeName` instead of `storeID` when only a name is known (Siri):
    /// an unrecognized name lands under "Anywhere" rather than failing. The
    /// server rejects both at once, so callers give one.
    public func createShoppingItem(name: String, note: String?, storeID: Int? = nil, storeName: String? = nil) async throws -> ShoppingItem {
        var body = try PatchBody().set("name", Patch.value(name))
        if let note, !note.isEmpty { body = try body.set("note", Patch.value(note)) }
        if let storeID { body = try body.set("store_id", Patch.value(storeID)) }
        if let storeName, !storeName.isEmpty { body = try body.set("store", Patch.value(storeName)) }
        return try await send(.createShoppingItem, body: body)
    }

    public func updateShoppingItem(
        id: Int,
        name: Patch<String> = .unset,
        note: Patch<String> = .unset,
        storeID: Patch<Int> = .unset,
        completed: Patch<Bool> = .unset
    ) async throws -> ShoppingItem {
        let body = try PatchBody().set("name", name).set("note", note).set("store_id", storeID).set("completed", completed)
        return try await send(.updateShoppingItem, ["item_id": String(id)], body: body)
    }

    public func deleteShoppingItem(id: Int) async throws {
        try await perform(.deleteShoppingItem, ["item_id": String(id)])
    }

    /// Rewrite one store group: every listed item is assigned to `storeID`
    /// (nil is "Anywhere") and numbered by its position.
    @discardableResult
    public func reorderShoppingItems(storeID: Int?, itemIDs: [Int]) async throws -> [ShoppingItem] {
        let body = try PatchBody().set("store_id", Patch.clearing(storeID)).set("item_ids", Patch.value(itemIDs))
        return try await send(.reorderShoppingItems, body: body)
    }

    public func purchasedItems(search: String, stores: Set<String>, limit: Int = 50, offset: Int = 0) async throws -> PurchasedPage {
        var query = [URLQueryItem(name: "limit", value: String(limit)), URLQueryItem(name: "offset", value: String(offset))]
        let term = search.trimmingCharacters(in: .whitespaces)
        if !term.isEmpty { query.append(URLQueryItem(name: "search", value: term)) }
        for store in stores.sorted() { query.append(URLQueryItem(name: "store", value: store)) }
        return try await get(.shoppingPurchased, query: query)
    }

    public func shoppingSuggestions(matching text: String, limit: Int = 8) async throws -> [ShoppingSuggestion] {
        try await get(.shoppingSuggestions, query: [
            URLQueryItem(name: "q", value: text), URLQueryItem(name: "limit", value: String(limit)),
        ])
    }

    public func forgetShoppingSuggestion(id: Int) async throws {
        try await perform(.forgetShoppingSuggestion, ["history_id": String(id)])
    }
}
