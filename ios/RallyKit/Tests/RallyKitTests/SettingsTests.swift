import Foundation
import Testing
@testable import RallyKit

private func sent(_ rec: Recorder) -> [String: Any] { try! JSONSerialization.jsonObject(with: rec.requests.last!.httpBody!) as! [String: Any] }
private let base = URL(string: "http://x")!
private let memberJSON = Data(##"{"id":1,"name":"Mom","color":"#315277","created_at":"2026-01-01T00:00:00","updated_at":"2026-01-01T00:00:00","notifications":{}}"##.utf8)

struct MemberDraftTests {
    @Test func createOmitsAnUnchosenColorSoTheServerHandsOutTheNextOne() async throws {
        let rec = Recorder(); rec.body = memberJSON
        var d = MemberDraft(); d.name = " Mom "
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createFamilyMember(d)
        let body = sent(rec)
        #expect(body["name"] as? String == "Mom" && body["color"] == nil && body["notifications"] == nil)
        #expect(body["pushover_user_key"] is NSNull)
    }

    @Test func updateClearsAKeyWithNullAndSendsThePartialNotificationMap() async throws {
        let rec = Recorder(); rec.body = memberJSON
        var d = MemberDraft(); d.name = "Mom"; d.color = "#af2c3d"; d.pushoverUserKey = "  "; d.notifications = ["shopping_added": true]
        _ = try await APIClient(baseURL: base, transport: rec.transport()).updateFamilyMember(id: 1, d)
        let body = sent(rec)
        #expect(body["pushover_user_key"] is NSNull && body["color"] as? String == "#af2c3d")
        #expect((body["notifications"] as? [String: Bool]) == ["shopping_added": true])
        #expect(rec.requests.last?.httpMethod == "PUT")
    }
}

struct CalendarDraftTests {
    let json = Data(#"{"id":2,"label":"Work","url":"https://x/ics","family_member_id":1,"owner_email":null,"cal_type":"ics","username":null,"created_at":"2026-01-01T00:00:00","updated_at":"2026-01-01T00:00:00"}"#.utf8)

    @Test func anICSFeedSendsNoCredentials() async throws {
        let rec = Recorder(); rec.body = json
        var d = CalendarDraft(); d.label = "Work"; d.url = " https://x/ics "; d.memberID = 1; d.username = "me"; d.password = "secret"
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createCalendar(d)
        let body = sent(rec)
        #expect(body["url"] as? String == "https://x/ics" && body["password"] == nil && body["username"] is NSNull)
    }

    @Test func aBlankPasswordOnEditMeansKeepTheStoredOne() async throws {
        let rec = Recorder(); rec.body = json
        var d = CalendarDraft(); d.label = "iCloud"; d.type = "caldav_apple"; d.memberID = 1; d.username = "me@icloud.com"; d.password = ""
        _ = try await APIClient(baseURL: base, transport: rec.transport()).updateCalendar(id: 2, d)
        #expect(sent(rec)["password"] == nil && sent(rec)["username"] as? String == "me@icloud.com")
        d.password = "app-specific"
        _ = try await APIClient(baseURL: base, transport: rec.transport()).updateCalendar(id: 2, d)
        #expect(sent(rec)["password"] as? String == "app-specific")
    }
}

struct TeamDraftTests {
    @Test func aRacingSeriesHasNoTeamKey() async throws {
        let rec = Recorder()
        rec.body = Data(#"{"id":1,"provider":"espn","league":"racing/nascar-premier","team_key":null,"label":"NASCAR","radio_station":null,"active":true,"created_at":"2026-01-01T00:00:00","updated_at":"2026-01-01T00:00:00"}"#.utf8)
        var d = TeamDraft(); d.league = "racing/nascar-premier"; d.label = "NASCAR"
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createFollowedTeam(d)
        #expect(sent(rec)["team_key"] is NSNull && sent(rec)["radio_station"] is NSNull && sent(rec)["active"] as? Bool == true)
    }
}

@MainActor
struct HouseholdModelTests {
    final class Server: @unchecked Sendable {
        var stored: [String: String] = ["local_timezone": "America/Chicago", "todo_notify_enabled": "false"]
        var puts: [[String: String]] = []
        func transport() -> Transport {
            { [self] request in
                func reply(_ json: String) -> (Data, HTTPURLResponse) {
                    (Data(json.utf8), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
                }
                if request.httpMethod == "PUT" {
                    let body = try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
                    let values = body["settings"] as! [String: String]
                    puts.append(values); stored.merge(values) { _, n in n }
                }
                let data = try! JSONSerialization.data(withJSONObject: ["settings": stored])
                return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
            }
        }
    }

    @Test func defaultsApplyToKeysThatWereNeverSaved() async {
        let model = HouseholdModel(client: APIClient(baseURL: base, transport: Server().transport()))
        await model.load()
        #expect(model.flag("prep_notify_enabled"), "defaults on")
        #expect(!model.flag("todo_notify_enabled"), "explicitly off")
        #expect(!model.flag("stem_concept_enabled"))
        #expect(model.value("shopping_notify_settle_minutes") == "5" && model.value("prep_notify_time") == "08:00")
    }

    @Test func onlyWhatChangedIsSent() async {
        let server = Server()
        let model = HouseholdModel(client: APIClient(baseURL: base, transport: server.transport()))
        await model.load()
        model.setFlag("stem_concept_enabled", true)
        model.setFlag("prep_notify_enabled", true) // already true: not a change
        model.set("local_timezone", "America/Chicago") // unchanged
        #expect(model.changes == ["stem_concept_enabled": "true"])
        #expect(await model.save())
        #expect(server.puts == [["stem_concept_enabled": "true"]])
        #expect(!model.hasChanges && model.flag("stem_concept_enabled"))
    }

    @Test func changingBackToTheStoredValueIsNoLongerAChange() async {
        let model = HouseholdModel(client: APIClient(baseURL: base, transport: Server().transport()))
        await model.load()
        model.setFlag("stem_concept_enabled", true)
        model.setFlag("stem_concept_enabled", false)
        #expect(!model.hasChanges)
    }
}

struct SettingsDecodingTests {
    @Test func testResultsReadSuccessAndErrorShapes() throws {
        let ok = try JSONDecoder().decode(TestResult.self, from: Data(#"{"success":true,"message":"Connected to m"}"#.utf8))
        let bad = try JSONDecoder().decode(TestResult.self, from: Data(#"{"success":false,"error":"Missing key"}"#.utf8))
        #expect(ok.text == "Connected to m" && bad.text == "Missing key")
    }

    @Test func llmAndAIHistoryDecode() throws {
        let llm = try JSONDecoder.rally.decode(LLMHistory.self, from: Data(#"{"current_history_id":2,"history":[{"id":2,"provider":"anthropic","model":"claude","max_tokens":4000,"max_tokens_mode":"custom","created_at":"2026-01-01T00:00:00","last_used_at":"2026-01-02T00:00:00"}]}"#.utf8))
        #expect(llm.history.first?.model == "claude" && llm.currentHistoryID == 2)
        let ai = try JSONDecoder.rally.decode(AIHistory.self, from: Data(#"{"field_name":"agent_voice","current_history_id":null,"history":[]}"#.utf8))
        #expect(ai.history.isEmpty && ai.currentHistoryID == nil)
    }

    @Test func notificationOverviewDecodes() throws {
        let o = try JSONDecoder().decode(NotificationOverview.self, from: Data(#"{"token_configured":false,"kinds":[{"kind":"event_reminder","label":"Event reminders","audience":"attendees","default_on":true,"settings_key":null,"enabled":true,"receiving":["Mom"],"muted":[],"no_key":["Dad"]}]}"#.utf8)) 
        #expect(!o.tokenConfigured && o.kinds.first?.noKey == ["Dad"])
    }
}
