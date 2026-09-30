import Foundation
import SwiftUI
import Observation

@MainActor @Observable
public final class PrepModel {
    public private(set) var items: [PrepItem] = []
    public private(set) var locations: [PrepLocation] = []
    public private(set) var hasLoaded = false
    public var filter = PrepFilter()
    public var errorMessage: String?
    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    public var groups: [(title: String, locationID: Int?, items: [PrepItem])] { PrepLogic.groups(items) }

    /// Location chips: every place stock lives, plus Unassigned while something is in it.
    public func locationChips() -> [FilterChip] {
        var chips = locations.map { FilterChip(value: String($0.id), title: $0.name) }
        if items.contains(where: { $0.locationID == nil }) || filter.locations.contains(PrepLogic.unassigned) {
            chips.append(FilterChip(value: PrepLogic.unassigned, title: "Unassigned"))
        }
        return chips
    }

    public func load() async {
        do {
            async let i = client.prepItems(filter)
            async let l = client.prepLocations()
            (items, locations) = try await (i, l)
        } catch { if !hasLoaded { errorMessage = error.localizedDescription } }
        hasLoaded = true
    }

    @discardableResult
    public func save(_ draft: PrepItemDraft, editing item: PrepItem?) async -> Bool {
        do {
            if let item { _ = try await client.updatePrepItem(id: item.id, draft) } else { _ = try await client.createPrepItem(draft) }
            await load(); return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    public func delete(_ item: PrepItem) async -> Bool {
        do { try await client.deletePrepItem(id: item.id); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    public func refreshed(_ item: PrepItem) async {
        do { _ = try await client.refreshPrepItem(id: item.id) } catch { errorMessage = error.localizedDescription }
        await load()
    }

    // MARK: Locations

    @discardableResult
    public func addLocation(_ name: String) async -> Bool {
        do {
            _ = try await client.createPrepLocation(name: name, sortOrder: (locations.map(\.sortOrder).max() ?? -1) + 1)
            await load(); return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    public func renameLocation(_ location: PrepLocation, to name: String) async -> Bool {
        do { _ = try await client.updatePrepLocation(id: location.id, name: .value(name)); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    public func deleteLocation(_ location: PrepLocation) async -> Bool {
        do {
            try await client.deletePrepLocation(id: location.id)
            filter.locations.remove(String(location.id))
            await load(); return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    /// Reorder by walking order: a go list is packed in the order you walk it.
    public func moveLocation(from source: IndexSet, to destination: Int) async {
        var ordered = locations
        ordered.move(fromOffsets: source, toOffset: destination)
        locations = ordered
        do {
            for (position, location) in ordered.enumerated() where location.sortOrder != position {
                _ = try await client.updatePrepLocation(id: location.id, sortOrder: .value(position))
            }
        } catch { errorMessage = error.localizedDescription }
        await load()
    }
}
