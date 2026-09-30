import Foundation
import Observation

@MainActor @Observable
public final class MealsModel {
    public private(set) var plans: [MealPlan] = []
    public private(set) var hasLoaded = false
    public var mealTypes: Set<String> = []
    public var errorMessage: String?
    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    public func days(today: String) -> [MealLogic.Day] { MealLogic.upcoming(plans, today: today, types: mealTypes) }

    public func toggleType(_ type: String) {
        if mealTypes.contains(type) { mealTypes.remove(type) } else { mealTypes.insert(type) }
    }

    public func load() async {
        do { plans = try await client.mealPlans() }
        catch { if !hasLoaded { errorMessage = error.localizedDescription } }
        hasLoaded = true
    }

    @discardableResult
    public func save(_ draft: MealDraft, editing meal: MealPlan?) async -> Bool {
        do {
            if let meal { _ = try await client.updateMeal(id: meal.id, draft) } else { _ = try await client.createMeal(draft) }
            await load()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    public func delete(_ meal: MealPlan) async -> Bool {
        do { try await client.deleteMeal(id: meal.id); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }
}
