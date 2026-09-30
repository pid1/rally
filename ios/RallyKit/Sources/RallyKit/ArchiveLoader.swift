import Foundation
import Observation

/// An archive page that is searched and paged on the server: previous notes,
/// previous meals. The screen only ever holds the pages it has loaded, so a
/// count or a search cannot be answered from memory.
@MainActor @Observable
public final class ArchiveLoader<Item: Decodable & Sendable & Identifiable, Filter: Equatable & Sendable> {
    public typealias Fetch = @Sendable (_ filter: Filter, _ search: String, _ limit: Int, _ offset: Int) async throws -> ArchivePage<Item>

    public private(set) var items: [Item] = []
    public private(set) var total = 0
    public private(set) var hasMore = false
    public private(set) var isLoading = false
    public private(set) var hasLoaded = false
    public var search = ""
    /// Whatever else narrows the archive (meal types, a minimum rating, a sort).
    /// Changing it is the caller's cue to `reload()`.
    public var filter: Filter
    public var errorMessage: String?

    private let fetch: Fetch
    private let pageSize: Int
    /// A slow answer for a search the person has already changed must not
    /// overwrite the results of the new one.
    private var generation = 0

    public init(filter: Filter, pageSize: Int = 50, fetch: @escaping Fetch) {
        self.filter = filter; self.pageSize = pageSize; self.fetch = fetch
    }

    public func reload() async {
        generation += 1
        let mine = generation
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        do {
            let page = try await fetch(filter, search, pageSize, 0)
            guard mine == generation else { return }
            items = page.items; total = page.total; hasMore = page.hasMore; hasLoaded = true
        } catch {
            guard mine == generation else { return }
            errorMessage = error.localizedDescription
        }
    }

    /// Reload everything already loaded — an edit must not jump back to page one.
    public func refreshLoaded() async {
        let wanted = max(pageSize, items.count)
        generation += 1
        let mine = generation
        do {
            let page = try await fetch(filter, search, wanted, 0)
            guard mine == generation else { return }
            items = page.items; total = page.total; hasMore = page.hasMore
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
            let page = try await fetch(filter, search, pageSize, items.count)
            guard mine == generation else { return }
            items += page.items; total = page.total; hasMore = page.hasMore
        } catch {
            guard mine == generation else { return }
            errorMessage = error.localizedDescription
        }
    }
}

/// For an archive with nothing to filter by beyond its search.
public struct NoFilter: Equatable, Sendable { public init() {} }

extension ArchiveLoader where Filter == NoFilter {
    public convenience init(pageSize: Int = 50, fetch: @escaping @Sendable (_ search: String, _ limit: Int, _ offset: Int) async throws -> ArchivePage<Item>) {
        self.init(filter: NoFilter(), pageSize: pageSize) { _, search, limit, offset in try await fetch(search, limit, offset) }
    }
}
