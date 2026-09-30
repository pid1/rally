import Foundation

public struct ShoppingStore: Codable, Sendable, Identifiable, Equatable, Hashable {
    public let id: Int
    public var name: String
}

public struct ShoppingItem: Codable, Sendable, Identifiable, Equatable, Hashable {
    public let id: Int
    public var name: String
    public var note: String?
    public var storeID: Int?
    public var completed: Bool
    public var completedAt: Date?
    public var sortOrder: Int
    public let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, name, note, completed
        case storeID = "store_id"
        case completedAt = "completed_at"
        case sortOrder = "sort_order"
        case createdAt = "created_at"
    }

    public init(id: Int, name: String, note: String? = nil, storeID: Int? = nil, completed: Bool = false,
                completedAt: Date? = nil, sortOrder: Int = 0, createdAt: Date = .now) {
        self.id = id; self.name = name; self.note = note; self.storeID = storeID
        self.completed = completed; self.completedAt = completedAt
        self.sortOrder = sortOrder; self.createdAt = createdAt
    }
}

public struct ShoppingSuggestion: Codable, Sendable, Identifiable, Equatable {
    public let id: Int
    public let name: String
    public let storeID: Int?
    public let timesAdded: Int

    enum CodingKeys: String, CodingKey {
        case id, name
        case storeID = "store_id"
        case timesAdded = "times_added"
    }
}

/// One page of any archive (completed tasks, previous notes, previous meals,
/// purchased items): they all page the same way, so they share a shape.
public struct ArchivePage<Item: Decodable & Sendable>: Decodable, Sendable {
    public let items: [Item]
    public let hasMore: Bool
    public let total: Int

    enum CodingKeys: String, CodingKey { case items, total, hasMore = "has_more" }
}

public struct PurchasedPage: Decodable, Sendable {
    public let items: [ShoppingItem]
    public let hasMore: Bool
    public let total: Int
    /// Chip values — store ids as strings and `"anywhere"` — with a purchase
    /// matching the search, ignoring the store filter.
    public let stores: [String]

    enum CodingKeys: String, CodingKey { case items, total, stores, hasMore = "has_more" }
}
