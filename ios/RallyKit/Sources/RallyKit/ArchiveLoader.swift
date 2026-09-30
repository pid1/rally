import Foundation
import Observation

/// An archive page that is searched and paged on the server: previous notes,
/// previous meals. The screen only ever holds the pages it has loaded, so a
/// count or a search cannot be answered from memory.
@MainActor @Observable
public final class ArchiveLoader<Item: Decodable & Sendable & Identifiable> {
    public typealias Fetch = @Sendable (_ search: String, _ limit: Int, _ offset: Int) async throws -> ArchivePage<Item>

    public private(set) var items: [Item] = []
    public private(set) var total = 0
    public private(set) var hasMore = false
    public private(set) var isLoading = false
    public private(set) var hasLoaded = false
    public var search = ""
    public var errorMessage: String?

    private let fetch: Fetch
    private let pageSize: Int
    /// A slow answer for a search the person has already changed must not
    /// overwrite the results of the new one.
    private var generation = 0

    public init(pageSize: Int = 50, fetch: @escaping Fetch) { self.pageSize = pageSize; self.fetch = fetch }

    public func reload() async {
        generation += 1
        let mine = generation
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        do {
            let page = try await fetch(search, pageSize, 0)
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
            let page = try await fetch(search, wanted, 0)
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
            let page = try await fetch(search, pageSize, items.count)
            guard mine == generation else { return }
            items += page.items; total = page.total; hasMore = page.hasMore
        } catch {
            guard mine == generation else { return }
            errorMessage = error.localizedDescription
        }
    }
}
