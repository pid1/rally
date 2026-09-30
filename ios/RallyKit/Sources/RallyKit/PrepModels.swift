import Foundation

public struct PrepLocation: Codable, Sendable, Identifiable, Equatable, Hashable {
    public let id: Int
    public var name: String
    public var sortOrder: Int
    enum CodingKeys: String, CodingKey { case id, name, sortOrder = "sort_order" }
}

public enum RefreshMode: String, Codable, Sendable, CaseIterable, Identifiable {
    case none, date, interval
    public var id: String { rawValue }
    public var title: String {
        switch self { case .none: "No schedule"; case .date: "On a date"; case .interval: "Every few months" }
    }
}

public struct PrepItem: Codable, Sendable, Identifiable, Equatable, Hashable {
    public let id: Int
    public var name: String
    public var quantity: String?
    public var locationID: Int?
    public var notes: String?
    public var refreshMode: RefreshMode
    public var refreshIntervalMonths: Int?
    public var nextRefreshDate: String?
    public var remindDaysBefore: Int?
    public var lastRefreshedOn: String?
    /// `ok`, `due` or `overdue` — derived by the server from today's date.
    public var status: String
    public var daysUntil: Int?
    public var locationName: String?

    enum CodingKeys: String, CodingKey {
        case id, name, quantity, notes, status
        case locationID = "location_id"
        case refreshMode = "refresh_mode"
        case refreshIntervalMonths = "refresh_interval_months"
        case nextRefreshDate = "next_refresh_date"
        case remindDaysBefore = "remind_days_before"
        case lastRefreshedOn = "last_refreshed_on"
        case daysUntil = "days_until"
        case locationName = "location_name"
    }

    public var isScheduled: Bool { refreshMode != .none }
}

/// What the item editor sends. The three refresh fields must agree — the server
/// rejects a mode that contradicts its fields, because such an item would
/// silently never notify — so the draft only ever writes the ones its mode uses.
public struct PrepItemDraft: Sendable, Equatable {
    public var name = ""
    public var quantity = ""
    public var locationID: Int?
    public var notes = ""
    public var mode: RefreshMode = .none
    public var intervalMonths = 12
    public var nextDate: String?
    public var remindDaysBefore: Int?

    public init() {}
    public init(_ item: PrepItem) {
        name = item.name; quantity = item.quantity ?? ""; locationID = item.locationID; notes = item.notes ?? ""
        mode = item.refreshMode; intervalMonths = item.refreshIntervalMonths ?? 12
        nextDate = item.nextRefreshDate; remindDaysBefore = item.remindDaysBefore
    }

    func body() throws -> PatchBody {
        func text(_ s: String) -> String? { let t = s.trimmingCharacters(in: .whitespacesAndNewlines); return t.isEmpty ? nil : t }
        return try PatchBody()
            .set("name", Patch.value(name.trimmingCharacters(in: .whitespaces)))
            .set("quantity", Patch.clearing(text(quantity)))
            .set("location_id", Patch.clearing(locationID))
            .set("notes", Patch.clearing(text(notes)))
            .set("refresh_mode", Patch.value(mode))
            // `date` carries a date and no interval; `interval` an interval and (optionally) a
            // first date; `none` neither. The reminder window only means something when scheduled.
            .set("refresh_interval_months", Patch.clearing(mode == .interval ? max(1, intervalMonths) : nil))
            .set("next_refresh_date", Patch.clearing(mode == .none ? nil : nextDate))
            .set("remind_days_before", Patch.clearing(mode == .none ? nil : remindDaysBefore))
    }
}

public struct GoListGroup: Decodable, Sendable, Identifiable, Equatable {
    public let locationID: Int?
    public let locationName: String
    public let items: [PrepItem]
    public var id: String { locationID.map(String.init) ?? "unassigned" }
    enum CodingKeys: String, CodingKey { case items, locationID = "location_id", locationName = "location_name" }
}

public struct GoList: Decodable, Sendable, Equatable {
    public let generatedOn: String
    public let totalItems: Int
    public let groups: [GoListGroup]
    enum CodingKeys: String, CodingKey { case groups, generatedOn = "generated_on", totalItems = "total_items" }
}

public struct PrepReviewGap: Codable, Sendable, Equatable {
    public let item: String
    public let category: String
    public let why: String
    public let priority: String
}

public struct PrepReviewData: Codable, Sendable, Equatable {
    public let assessment: String
    public let gaps: [PrepReviewGap]
    public let strengths: [String]
    public let assumptions: [String]
    public let notes: String
}

public struct PrepReview: Decodable, Sendable, Equatable {
    public let id: Int
    public let review: PrepReviewData
    public let model: String?
    public let itemCount: Int
    public let currentItemCount: Int
    /// The item count has changed since it ran — visibly out of date.
    public let stale: Bool
    public let createdAt: Date
    enum CodingKeys: String, CodingKey {
        case id, review, model, stale
        case itemCount = "item_count", currentItemCount = "current_item_count", createdAt = "created_at"
    }
}

public struct PrepFilter: Equatable, Sendable {
    public enum Sort: String, CaseIterable, Sendable, Identifiable {
        case location, name, refreshSoonest = "refresh-soonest", newest
        public var id: String { rawValue }
        public var title: String {
            switch self { case .location: "Location"; case .name: "Name"; case .refreshSoonest: "Refresh Soonest"; case .newest: "Newest" }
        }
    }
    public var locations: Set<String> = []   // ids and/or "unassigned"
    public var statuses: Set<String> = []    // ok, due, overdue
    public var search = ""
    public var sort: Sort = .location
    public init() {}
}

public enum PrepLogic {
    public static let unassigned = "unassigned"
    public static let statusChips = [FilterChip(value: "ok", title: "OK"), FilterChip(value: "due", title: "Due soon"),
                                     FilterChip(value: "overdue", title: "Overdue")]

    /// Items in the order received, folded into one group per location. The server
    /// already sorts by physical walking order, so groups keep first-seen order.
    public static func groups(_ items: [PrepItem]) -> [(title: String, locationID: Int?, items: [PrepItem])] {
        var out: [(title: String, locationID: Int?, items: [PrepItem])] = []
        for item in items {
            if let i = out.firstIndex(where: { $0.locationID == item.locationID }) { out[i].items.append(item) }
            else { out.append((item.locationName ?? "Unassigned", item.locationID, [item])) }
        }
        return out
    }

    /// "Overdue by 12 days", "Due in 5 days", "Refreshes Mar 3" — the wording a
    /// person standing in the garage wants.
    public static func statusLine(_ item: PrepItem, calendar: Calendar = .current) -> String? {
        guard item.isScheduled, let next = DayString.date(item.nextRefreshDate, calendar: calendar) else {
            return item.isScheduled ? "Not yet scheduled" : nil
        }
        let date = next.formatted(.dateTime.month(.abbreviated).day().year())
        switch item.status {
        case "overdue":
            let d = abs(item.daysUntil ?? 0)
            return d == 0 ? "Overdue · was due \(date)" : "Overdue by \(d) day\(d == 1 ? "" : "s") · \(date)"
        case "due":
            let d = item.daysUntil ?? 0
            return d <= 0 ? "Due today" : "Due in \(d) day\(d == 1 ? "" : "s") · \(date)"
        default: return "Refreshes \(date)"
        }
    }
}

extension APIClient {
    public func prepLocations() async throws -> [PrepLocation] { try await get(.prepLocations) }

    public func createPrepLocation(name: String, sortOrder: Int) async throws -> PrepLocation {
        try await send(.createPrepLocation, body: PatchBody().set("name", Patch.value(name)).set("sort_order", Patch.value(sortOrder)))
    }

    public func updatePrepLocation(id: Int, name: Patch<String> = .unset, sortOrder: Patch<Int> = .unset) async throws -> PrepLocation {
        try await send(.updatePrepLocation, ["location_id": String(id)],
                       body: PatchBody().set("name", name).set("sort_order", sortOrder))
    }

    /// The server moves the location's items to Unassigned first, so none vanish from the go list.
    public func deletePrepLocation(id: Int) async throws { try await perform(.deletePrepLocation, ["location_id": String(id)]) }

    public func prepItems(_ filter: PrepFilter) async throws -> [PrepItem] {
        var query = [URLQueryItem(name: "sort", value: filter.sort.rawValue)]
        for l in filter.locations.sorted() { query.append(URLQueryItem(name: "location", value: l)) }
        if filter.statuses.count == 1, let s = filter.statuses.first { query.append(URLQueryItem(name: "status", value: s)) }
        let term = filter.search.trimmingCharacters(in: .whitespaces)
        if !term.isEmpty { query.append(URLQueryItem(name: "search", value: term)) }
        let items: [PrepItem] = try await get(.prepItems, query: query)
        // The endpoint filters one status; several are OR'd here.
        return filter.statuses.count > 1 ? items.filter { filter.statuses.contains($0.status) } : items
    }

    public func createPrepItem(_ draft: PrepItemDraft) async throws -> PrepItem { try await send(.createPrepItem, body: draft.body()) }

    public func updatePrepItem(id: Int, _ draft: PrepItemDraft) async throws -> PrepItem {
        try await send(.updatePrepItem, ["item_id": String(id)], body: draft.body())
    }

    public func deletePrepItem(id: Int) async throws { try await perform(.deletePrepItem, ["item_id": String(id)]) }

    /// Mark refreshed today (the server's "today"). An interval item re-anchors on the actual date.
    public func refreshPrepItem(id: Int) async throws -> PrepItem {
        try await send(.refreshPrepItem, ["item_id": String(id)], body: PatchBody())
    }

    public func goList(locations: Set<String> = []) async throws -> GoList {
        try await get(.goList, query: locations.sorted().map { URLQueryItem(name: "location", value: $0) })
    }

    public func exportGoList(format: String, locations: Set<String> = []) async throws -> (data: Data, filename: String) {
        try await download(.exportGoList, query: [URLQueryItem(name: "format", value: format)]
                           + locations.sorted().map { URLQueryItem(name: "location", value: $0) })
    }

    /// `nil` until a review has been run — that is "not reviewed yet", not a failure.
    public func latestPrepReview() async throws -> PrepReview? {
        do { return try await get(.prepReview) }
        catch APIError.http(let status, _) where status == 404 { return nil }
    }

    /// A real LLM call: seconds and money. Only ever from a button.
    public func runPrepReview() async throws -> PrepReview {
        try await send(.runPrepReview, body: PatchBody())
    }
}
