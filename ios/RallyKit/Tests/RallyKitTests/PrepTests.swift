import Foundation
import Testing
@testable import RallyKit

private func item(_ id: Int, loc: Int?, _ locName: String?, status: String = "ok", days: Int? = nil,
                  mode: RefreshMode = .none, next: String? = nil) -> PrepItem {
    try! JSONDecoder.rally.decode(PrepItem.self, from: Data("""
    {"id":\(id),"name":"N\(id)","quantity":null,"location_id":\(loc.map(String.init) ?? "null"),"notes":null,
     "refresh_mode":"\(mode.rawValue)","refresh_interval_months":null,"next_refresh_date":\(next.map { "\"\($0)\"" } ?? "null"),
     "remind_days_before":null,"last_refreshed_on":null,"status":"\(status)","days_until":\(days.map(String.init) ?? "null"),
     "location_name":\(locName.map { "\"\($0)\"" } ?? "null")}
    """.utf8))
}

struct PrepLogicTests {
    @Test func groupsKeepTheServersWalkingOrder() {
        let groups = PrepLogic.groups([item(1, loc: 2, "Garage"), item(2, loc: 2, "Garage"), item(3, loc: 1, "Truck"), item(4, loc: nil, nil)])
        #expect(groups.map(\.title) == ["Garage", "Truck", "Unassigned"])
        #expect(groups[0].items.count == 2)
    }

    @Test func statusLineWording() {
        #expect(PrepLogic.statusLine(item(1, loc: nil, nil)) == nil)
        #expect(PrepLogic.statusLine(item(1, loc: nil, nil, mode: .date)) == "Not yet scheduled")
        #expect(PrepLogic.statusLine(item(1, loc: nil, nil, status: "overdue", days: -12, mode: .date, next: "2026-09-18"))?.hasPrefix("Overdue by 12 days") == true)
        #expect(PrepLogic.statusLine(item(1, loc: nil, nil, status: "overdue", days: -1, mode: .date, next: "2026-09-29"))?.hasPrefix("Overdue by 1 day ·") == true)
        #expect(PrepLogic.statusLine(item(1, loc: nil, nil, status: "due", days: 5, mode: .date, next: "2026-10-05"))?.hasPrefix("Due in 5 days") == true)
        #expect(PrepLogic.statusLine(item(1, loc: nil, nil, status: "due", days: 0, mode: .date, next: "2026-09-30")) == "Due today")
        #expect(PrepLogic.statusLine(item(1, loc: nil, nil, status: "ok", days: 90, mode: .interval, next: "2026-12-30"))?.hasPrefix("Refreshes ") == true)
    }
}

struct PrepEndpointTests {
    let base = URL(string: "http://x")!
    let itemJSON = Data(#"{"id":1,"name":"Water","quantity":null,"location_id":null,"notes":null,"refresh_mode":"none","refresh_interval_months":null,"next_refresh_date":null,"remind_days_before":null,"last_refreshed_on":null,"status":"ok","days_until":null,"location_name":"Unassigned","created_at":"2026-09-30T10:00:00","updated_at":"2026-09-30T10:00:00"}"#.utf8)
    private func sent(_ rec: Recorder) -> [String: Any] { try! JSONSerialization.jsonObject(with: rec.requests.last!.httpBody!) as! [String: Any] }

    @Test func noScheduleWritesNoRefreshFields() async throws {
        let rec = Recorder(); rec.body = itemJSON
        var draft = PrepItemDraft(); draft.name = " Water "; draft.nextDate = "2027-01-01"; draft.intervalMonths = 6; draft.remindDaysBefore = 7
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createPrepItem(draft)
        let body = sent(rec)
        #expect(body["refresh_mode"] as? String == "none" && body["name"] as? String == "Water")
        #expect(body["refresh_interval_months"] is NSNull && body["next_refresh_date"] is NSNull && body["remind_days_before"] is NSNull)
    }

    @Test func dateModeCarriesADateAndNoInterval() async throws {
        let rec = Recorder(); rec.body = itemJSON
        var draft = PrepItemDraft(); draft.name = "x"; draft.mode = .date; draft.nextDate = "2027-01-01"; draft.intervalMonths = 6
        _ = try await APIClient(baseURL: base, transport: rec.transport()).updatePrepItem(id: 1, draft)
        let body = sent(rec)
        #expect(body["refresh_mode"] as? String == "date" && body["next_refresh_date"] as? String == "2027-01-01")
        #expect(body["refresh_interval_months"] is NSNull)
    }

    @Test func intervalModeCarriesAnIntervalAtLeastOne() async throws {
        let rec = Recorder(); rec.body = itemJSON
        var draft = PrepItemDraft(); draft.name = "x"; draft.mode = .interval; draft.intervalMonths = 0
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createPrepItem(draft)
        #expect(sent(rec)["refresh_interval_months"] as? Int == 1)
        #expect(sent(rec)["next_refresh_date"] is NSNull) // the server seeds today + interval
    }

    @Test func itemsQueryAndMultiStatusFilter() async throws {
        let rec = Recorder(); rec.body = Data("[\(String(data: itemJSON, encoding: .utf8)!)]".utf8)
        var filter = PrepFilter(); filter.locations = ["2", "unassigned"]; filter.statuses = ["due", "overdue"]; filter.search = "water"; filter.sort = .refreshSoonest
        let items = try await APIClient(baseURL: base, transport: rec.transport()).prepItems(filter)
        let url = rec.requests[0].url!.absoluteString
        #expect(url.contains("sort=refresh-soonest") && url.contains("location=2") && url.contains("location=unassigned") && url.contains("search=water"))
        #expect(!url.contains("status="), "two statuses cannot go in one parameter")
        #expect(items.isEmpty, "the ok item is filtered out client-side")
    }

    @Test func aMissingReviewIsNilNotAnError() async throws {
        let rec = Recorder(); rec.status = 404; rec.body = Data(#"{"detail":"No review yet"}"#.utf8)
        #expect(try await APIClient(baseURL: base, transport: rec.transport()).latestPrepReview() == nil)
    }

    @Test func downloadReturnsTheSuggestedFilename() async throws {
        let client = APIClient(baseURL: base) { request in
            (Data("# list".utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                headerFields: ["Content-Disposition": #"attachment; filename="go-list-2026-09-30.md""#])!)
        }
        let file = try await client.exportGoList(format: "md")
        #expect(file.filename == "go-list-2026-09-30.md" && file.data == Data("# list".utf8))
    }
}
