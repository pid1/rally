import Foundation

public struct MealPlan: Codable, Sendable, Identifiable, Equatable, Hashable {
    public let id: Int
    public var date: String
    public var mealType: String
    public var plan: String
    /// `nil` means everyone is eating.
    public var attendeeIDs: [Int]?
    public var cookID: Int?
    public var rating: Int?
    public var review: String?

    enum CodingKeys: String, CodingKey {
        case id, date, plan, rating, review
        case mealType = "meal_type"
        case attendeeIDs = "attendee_ids"
        case cookID = "cook_id"
    }

    public init(id: Int, date: String, mealType: String = "Dinner", plan: String, attendeeIDs: [Int]? = nil,
                cookID: Int? = nil, rating: Int? = nil, review: String? = nil) {
        self.id = id; self.date = date; self.mealType = mealType; self.plan = plan
        self.attendeeIDs = attendeeIDs; self.cookID = cookID; self.rating = rating; self.review = review
    }
}

/// What the meal editor sends. Attendees left empty means *everyone*, which the
/// server stores as `null` — so "nobody ticked" and "everybody" are one state.
public struct MealDraft: Sendable, Equatable {
    public var date: String
    public var mealType: String
    public var plan = ""
    public var attendees: Set<Int> = []
    public var cookID: Int?

    public init(date: String, mealType: String) { self.date = date; self.mealType = mealType }
    public init(_ meal: MealPlan) {
        date = meal.date; mealType = meal.mealType; plan = meal.plan
        attendees = Set(meal.attendeeIDs ?? []); cookID = meal.cookID
    }

    func body() throws -> PatchBody {
        try PatchBody()
            .set("date", Patch.value(date))
            .set("meal_type", Patch.value(mealType))
            .set("plan", Patch.value(plan.trimmingCharacters(in: .whitespacesAndNewlines)))
            .set("attendee_ids", Patch.clearing(attendees.isEmpty ? nil : attendees.sorted()))
            .set("cook_id", Patch.clearing(cookID))
    }
}

public enum MealSort: String, CaseIterable, Sendable, Identifiable {
    case ratingDesc = "rating_desc", dateDesc = "date_desc", dateAsc = "date_asc"
    public var id: String { rawValue }
    public var title: String {
        switch self { case .ratingDesc: "Highest Rated"; case .dateDesc: "Most Recent"; case .dateAsc: "Oldest First" }
    }
}

public struct MealFilter: Equatable, Sendable {
    public var sort: MealSort = .ratingDesc
    public var mealTypes: Set<String> = []
    public var minRating: Int?
    public init() {}
}

public enum MealLogic {
    public static let mealTypes = ["Breakfast", "Lunch", "Dinner", "Snacks"]

    static func typeOrder(_ type: String) -> Int { mealTypes.firstIndex(of: type) ?? 99 }

    public struct Day: Identifiable, Equatable, Sendable {
        public let date: String
        public let meals: [MealPlan]
        public var id: String { date }
    }

    /// Every meal from `today` onward, one box per day in date order, and meals
    /// within a day in Breakfast → Snacks order. No upper bound: the planner shows
    /// the whole future. A day filtered down to nothing disappears with its meals.
    public static func upcoming(_ plans: [MealPlan], today: String, types: Set<String> = []) -> [Day] {
        let kept = plans.filter { $0.date >= today && (types.isEmpty || types.contains($0.mealType)) }
            .sorted { a, b in
                a.date != b.date ? a.date < b.date : (typeOrder(a.mealType), a.id) < (typeOrder(b.mealType), b.id)
            }
        var days: [Day] = []
        for plan in kept {
            if let last = days.last, last.date == plan.date { days[days.count - 1] = Day(date: last.date, meals: last.meals + [plan]) }
            else { days.append(Day(date: plan.date, meals: [plan])) }
        }
        return days
    }

    /// Moving a reviewed past meal onto the planner discards its rating and
    /// review (the server enforces it). The editor warns first.
    public static func losesReview(_ meal: MealPlan, movingTo newDate: String, today: String) -> Bool {
        (meal.rating != nil || !(meal.review ?? "").isEmpty) && meal.date < today && newDate >= today
    }

    public static func dayHeading(_ date: String, today: String, calendar: Calendar = .current) -> String {
        guard let day = DayString.date(date, calendar: calendar), let now = DayString.date(today, calendar: calendar) else { return date }
        let days = calendar.dateComponents([.day], from: now, to: day).day ?? 0
        if days == 0 { return "Today" }
        if days == 1 { return "Tomorrow" }
        let sameYear = calendar.component(.year, from: day) == calendar.component(.year, from: now)
        return sameYear ? day.formatted(.dateTime.weekday(.wide).month(.abbreviated).day())
                        : day.formatted(.dateTime.weekday(.wide).month(.abbreviated).day().year())
    }

    /// "Eating: Mom, Dad · Cook: Jake" — only what is set.
    public static func meta(_ meal: MealPlan, members: [FamilyMember]) -> String? {
        var parts: [String] = []
        if let ids = meal.attendeeIDs, !ids.isEmpty {
            parts.append("Eating: " + ids.map { id in members.first { $0.id == id }?.name ?? "?" }.joined(separator: ", "))
        }
        if let cook = meal.cookID.flatMap({ id in members.first { $0.id == id } }) { parts.append("Cook: \(cook.name)") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

extension APIClient {
    public func mealPlans() async throws -> [MealPlan] { try await get(.mealPlans) }

    public func createMeal(_ draft: MealDraft) async throws -> MealPlan { try await send(.createMeal, body: draft.body()) }

    public func updateMeal(id: Int, _ draft: MealDraft) async throws -> MealPlan {
        try await send(.updateMeal, ["plan_id": String(id)], body: draft.body())
    }

    public func deleteMeal(id: Int) async throws { try await perform(.deleteMeal, ["plan_id": String(id)]) }

    /// Both keys go every time: the server treats a present `null` as "clear",
    /// and an absent key as "leave alone".
    public func reviewMeal(id: Int, rating: Int?, review: String?) async throws -> MealPlan {
        let text = review?.trimmingCharacters(in: .whitespacesAndNewlines)
        let body = try PatchBody().set("rating", Patch.clearing(rating))
            .set("review", Patch.clearing((text ?? "").isEmpty ? nil : text))
        return try await send(.reviewMeal, ["plan_id": String(id)], body: body)
    }

    public func previousMeals(filter: MealFilter, search: String, limit: Int, offset: Int) async throws -> ArchivePage<MealPlan> {
        var query = [URLQueryItem(name: "sort", value: filter.sort.rawValue),
                     URLQueryItem(name: "limit", value: String(limit)), URLQueryItem(name: "offset", value: String(offset))]
        if let min = filter.minRating { query.append(URLQueryItem(name: "min_rating", value: String(min))) }
        for type in MealLogic.mealTypes where filter.mealTypes.contains(type) { query.append(URLQueryItem(name: "meal_type", value: type)) }
        let term = search.trimmingCharacters(in: .whitespaces)
        if !term.isEmpty { query.append(URLQueryItem(name: "search", value: term)) }
        return try await get(.previousMeals, query: query)
    }
}
