import Foundation
import Testing
@testable import RallyKit

private let cal: Calendar = { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "America/Chicago")!; return c }()
private func meal(_ id: Int, _ date: String, _ type: String = "Dinner", rating: Int? = nil, review: String? = nil,
                  eating: [Int]? = nil, cook: Int? = nil) -> MealPlan {
    MealPlan(id: id, date: date, mealType: type, plan: "P\(id)", attendeeIDs: eating, cookID: cook, rating: rating, review: review)
}

struct MealLogicTests {
    @Test func upcomingDropsThePastAndOrdersByDateThenMealType() {
        let plans = [meal(1, "2026-10-02", "Dinner"), meal(2, "2026-10-01", "Snacks"), meal(3, "2026-10-01", "Breakfast"),
                     meal(4, "2026-09-29"), meal(5, "2026-09-30", "Lunch")]
        let days = MealLogic.upcoming(plans, today: "2026-09-30")
        #expect(days.map(\.date) == ["2026-09-30", "2026-10-01", "2026-10-02"])
        #expect(days[1].meals.map(\.id) == [3, 2]) // Breakfast before Snacks
    }

    @Test func filteringByTypeRemovesEmptyDays() {
        let plans = [meal(1, "2026-10-01", "Lunch"), meal(2, "2026-10-02", "Dinner")]
        #expect(MealLogic.upcoming(plans, today: "2026-09-30", types: ["Dinner"]).map(\.date) == ["2026-10-02"])
    }

    @Test func movingAReviewedPastMealOntoThePlannerLosesItsReview() {
        #expect(MealLogic.losesReview(meal(1, "2026-09-01", rating: 4), movingTo: "2026-10-05", today: "2026-09-30"))
        #expect(MealLogic.losesReview(meal(1, "2026-09-01", review: "great"), movingTo: "2026-09-30", today: "2026-09-30"))
        #expect(!MealLogic.losesReview(meal(1, "2026-09-01"), movingTo: "2026-10-05", today: "2026-09-30")) // nothing to lose
        #expect(!MealLogic.losesReview(meal(1, "2026-09-01", rating: 4), movingTo: "2026-09-10", today: "2026-09-30")) // stays past
        #expect(!MealLogic.losesReview(meal(1, "2026-10-01", rating: 4), movingTo: "2026-10-05", today: "2026-09-30")) // was not past
    }

    @Test func headings() {
        #expect(MealLogic.dayHeading("2026-09-30", today: "2026-09-30", calendar: cal) == "Today")
        #expect(MealLogic.dayHeading("2026-10-01", today: "2026-09-30", calendar: cal) == "Tomorrow")
        #expect(MealLogic.dayHeading("2027-02-01", today: "2026-09-30", calendar: cal).contains("2027"))
    }

    @Test func metaNamesOnlyWhatIsSet() {
        let members = [try! JSONDecoder.rally.decode(FamilyMember.self, from: Data(##"{"id":1,"name":"Mom","color":"#315277"}"##.utf8)),
                       try! JSONDecoder.rally.decode(FamilyMember.self, from: Data(##"{"id":2,"name":"Dad","color":"#af2c3d"}"##.utf8))]
        #expect(MealLogic.meta(meal(1, "x"), members: members) == nil)
        #expect(MealLogic.meta(meal(1, "x", eating: [1, 2], cook: 2), members: members) == "Eating: Mom, Dad · Cook: Dad")
        #expect(MealLogic.meta(meal(1, "x", eating: [9]), members: members) == "Eating: ?")
    }
}

struct MealEndpointTests {
    let base = URL(string: "http://x")!
    let mealJSON = Data(#"{"id":1,"date":"2026-10-01","meal_type":"Dinner","plan":"Tacos","attendee_ids":null,"cook_id":null,"rating":null,"review":null,"created_at":"2026-09-30T10:00:00","updated_at":"2026-09-30T10:00:00"}"#.utf8)
    private func sent(_ rec: Recorder) -> [String: Any] { try! JSONSerialization.jsonObject(with: rec.requests.last!.httpBody!) as! [String: Any] }

    @Test func nobodyTickedMeansEveryoneAndIsSentAsNull() async throws {
        let rec = Recorder(); rec.body = mealJSON
        var draft = MealDraft(date: "2026-10-01", mealType: "Dinner"); draft.plan = " Tacos "
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createMeal(draft)
        #expect(sent(rec)["attendee_ids"] is NSNull && sent(rec)["cook_id"] is NSNull && sent(rec)["plan"] as? String == "Tacos")
        draft.attendees = [3, 1]; draft.cookID = 3
        _ = try await APIClient(baseURL: base, transport: rec.transport()).updateMeal(id: 1, draft)
        #expect(sent(rec)["attendee_ids"] as? [Int] == [1, 3] && sent(rec)["cook_id"] as? Int == 3)
        #expect(rec.requests.last?.httpMethod == "PUT")
    }

    @Test func clearingAReviewSendsBothKeysAsNull() async throws {
        let rec = Recorder(); rec.body = mealJSON
        _ = try await APIClient(baseURL: base, transport: rec.transport()).reviewMeal(id: 1, rating: nil, review: "  ")
        #expect(sent(rec)["rating"] is NSNull && sent(rec)["review"] is NSNull)
        #expect(rec.requests[0].url?.path == "/api/meal-planner/1/review")
    }

    @Test func previousMealsQuery() async throws {
        let rec = Recorder(); rec.body = Data(#"{"items":[],"has_more":false,"total":0}"#.utf8)
        var filter = MealFilter(); filter.sort = .dateAsc; filter.minRating = 4; filter.mealTypes = ["Lunch", "Dinner"]
        _ = try await APIClient(baseURL: base, transport: rec.transport()).previousMeals(filter: filter, search: "taco", limit: 50, offset: 0)
        let url = rec.requests[0].url!.absoluteString
        #expect(url.contains("sort=date_asc") && url.contains("min_rating=4") && url.contains("meal_type=Lunch")
                && url.contains("meal_type=Dinner") && url.contains("search=taco"))
    }
}

struct InstallSettingsTests {
    @Test func readsTheInstallZoneAndDefaultMeal() {
        let s = InstallSettings(["local_timezone": "Pacific/Auckland", "meal_default_type": "Lunch"])
        #expect(s.timeZone.identifier == "Pacific/Auckland" && s.defaultMealType == "Lunch")
    }

    @Test func fallsBackWhenTheValuesAreMissingOrNonsense() {
        let s = InstallSettings(["local_timezone": "Nowhere/Land", "meal_default_type": "Brunch"])
        #expect(s.timeZone == .current && s.defaultMealType == "Dinner")
    }

    @Test func todayIsTheInstallsDayNotThePhones() {
        // 2026-09-30 23:30 in Chicago is already October 1 in Auckland.
        let instant = Date(timeIntervalSince1970: 1_790_743_800)
        let chicago = InstallSettings(["local_timezone": "America/Chicago"]).today(now: instant)
        let auckland = InstallSettings(["local_timezone": "Pacific/Auckland"]).today(now: instant)
        #expect(chicago != auckland)
    }
}
