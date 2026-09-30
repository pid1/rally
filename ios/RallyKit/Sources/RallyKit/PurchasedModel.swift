import Foundation
import Observation

/// The purchased-items archive: searched, filtered and paged on the server,
/// because the screen only ever holds the pages it has loaded.
@MainActor @Observable
public final class PurchasedModel {
    public private(set) var items: [ShoppingItem] = []
    public private(set) var total = 0
    public private(set) var hasMore = false
    /// Chip values with a purchase matching the search, ignoring the store filter.
    public private(set) var chipValues: [String] = []
    public private(set) var isLoading = false
    public private(set) var hasLoaded = false
    public var errorMessage: String?
    public var search = ""
    public var selected: Set<String> = []

    private let client: APIClient
    private let pageSize: Int
    /// Bumped by every reload, so a slow answer for a search the person has
    /// already changed cannot overwrite the results of the new one.
    private var generation = 0

    public init(client: APIClient, pageSize: Int = 50) {
        self.client = client
        self.pageSize = pageSize
    }

    public func reload() async {
        generation += 1
        let mine = generation
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        do {
            let page = try await client.purchasedItems(search: search, stores: selected, limit: pageSize, offset: 0)
            guard mine == generation else { return }
            items = page.items; total = page.total; hasMore = page.hasMore; chipValues = page.stores
            hasLoaded = true
        } catch {
            guard mine == generation else { return }
            errorMessage = error.localizedDescription
        }
    }

    public func loadMore() async {
        guard hasMore, !isLoading else { return }
        let mine = generation
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        do {
            let page = try await client.purchasedItems(search: search, stores: selected, limit: pageSize, offset: items.count)
            guard mine == generation else { return }
            items += page.items; total = page.total; hasMore = page.hasMore; chipValues = page.stores
        } catch {
            guard mine == generation else { return }
            errorMessage = error.localizedDescription
        }
    }

    public func toggleFilter(_ value: String) {
        if selected.contains(value) { selected.remove(value) } else { selected.insert(value) }
    }

    /// Chips for the stores with a purchase, plus any selected one — a filter
    /// that cannot be seen cannot be undone.
    public func chips(stores: [ShoppingStore]) -> [FilterChip] {
        let present = Set(chipValues).union(selected)
        var chips = ShoppingLogic.sortedStores(stores)
            .filter { present.contains(String($0.id)) }
            .map { FilterChip(value: String($0.id), title: $0.name) }
        if present.contains(ShoppingLogic.anywhere) { chips.append(.init(value: ShoppingLogic.anywhere, title: "Anywhere")) }
        return chips
    }
}
