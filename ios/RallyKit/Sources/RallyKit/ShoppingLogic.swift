import Foundation
import SwiftUI

/// One filter chip: what it selects, and what it says. Shopping stores, task
/// assignees and the archives all use it.
public struct FilterChip: Identifiable, Equatable, Sendable {
    public let value: String
    public let title: String
    /// A member's identity color, drawn as a dot. The one color on a page.
    public let colorHex: String?
    public init(value: String, title: String, colorHex: String? = nil) { self.value = value; self.title = title; self.colorHex = colorHex }
    public var id: String { value }
}

public struct ShoppingGroup: Identifiable, Equatable, Sendable {
    public let key: String
    public let storeID: Int?
    public let title: String
    public var items: [ShoppingItem]
    public var id: String { key }
    public var openItems: [ShoppingItem] { items.filter { !$0.completed } }
}

/// How the list is grouped, filtered and reordered. Pure functions, so every
/// rule the web page keeps in its script is pinned by a test here instead.
public enum ShoppingLogic {
    /// The chip value for the catch-all group, whose items have no store.
    public static let anywhere = "anywhere"

    public static func key(for item: ShoppingItem) -> String {
        item.storeID.map(String.init) ?? anywhere
    }

    /// Chips describe what is **on the list**, not which stores exist: a store
    /// earns one when an item is on screen, or when it is currently selected.
    /// The second clause stops a filter that cannot be seen or undone — tick off
    /// the last Costco item while filtered to Costco and the chip must stay.
    public static func chips(stores: [ShoppingStore], items: [ShoppingItem], selected: Set<String>) -> [FilterChip] {
        let present = Set(items.map(key(for:)))
        var chips = sortedStores(stores)
            .filter { present.contains(String($0.id)) || selected.contains(String($0.id)) }
            .map { FilterChip(value: String($0.id), title: $0.name) }
        if present.contains(anywhere) || selected.contains(anywhere) {
            chips.append(FilterChip(value: anywhere, title: "Anywhere"))
        }
        return chips
    }

    /// Named stores alphabetically, the catch-all last. Items keep the order the
    /// server sent: open ones in the hand-arranged order, then purchased ones.
    /// A selected store with nothing in it still gets a group, so filtering to
    /// it reads as "nothing here" rather than as a vanished group.
    public static func groups(stores: [ShoppingStore], items: [ShoppingItem], selected: Set<String>) -> [ShoppingGroup] {
        let visible = selected.isEmpty ? items : items.filter { selected.contains(key(for: $0)) }
        var byKey: [String: [ShoppingItem]] = [:]
        for item in visible { byKey[key(for: item), default: []].append(item) }
        for value in selected where byKey[value] == nil { byKey[value] = [] }

        func title(_ key: String) -> String {
            if key == anywhere { return "Anywhere" }
            return stores.first { String($0.id) == key }?.name ?? "Anywhere"
        }
        let keys = byKey.keys.sorted { a, b in
            if a == anywhere { return false }
            if b == anywhere { return true }
            return title(a).localizedCaseInsensitiveCompare(title(b)) == .orderedAscending
        }
        return keys.map { key in
            ShoppingGroup(key: key, storeID: key == anywhere ? nil : Int(key), title: title(key), items: byKey[key] ?? [])
        }
    }

    /// The ids of one group's open items after a drag, in the order they should
    /// now read — the body of a reorder request.
    public static func reordered(_ ids: [Int], from source: IndexSet, to destination: Int) -> [Int] {
        var copy = ids
        copy.move(fromOffsets: source, toOffset: destination)
        return copy
    }

    public static func sortedStores(_ stores: [ShoppingStore]) -> [ShoppingStore] {
        stores.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }
}
