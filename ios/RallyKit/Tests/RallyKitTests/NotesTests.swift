import Foundation
import Testing
@testable import RallyKit

private let cal: Calendar = { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "America/Chicago")!; return c }()

struct NoteLogicTests {
    @Test func headings() {
        let today = DayString.date("2026-09-30", calendar: cal)!
        #expect(NoteLogic.heading("2026-09-30", today: today, calendar: cal).hasPrefix("Today · "))
        #expect(NoteLogic.heading("2026-10-01", today: today, calendar: cal).hasPrefix("Tomorrow · "))
        #expect(!NoteLogic.heading("2026-12-25", today: today, calendar: cal).contains("Today"))
        #expect(NoteLogic.heading("2027-01-04", today: today, calendar: cal).contains("2027"))
    }

    @Test func typedTextIsAppendedNotDropped() {
        let merged = NoteLogic.merged(existing: "Soccer at 5", typed: "  Bring water  ")
        #expect(merged.body == "Soccer at 5\nBring water" && merged.appended)
    }

    @Test func nothingToAppendWhenEmptyOrIdentical() {
        #expect(NoteLogic.merged(existing: "A", typed: "   ") == ("A", false))
        #expect(NoteLogic.merged(existing: "A\n", typed: "A") == ("A\n", false))
    }
}

@MainActor
struct NotesModelTests {
    let base = URL(string: "http://x")!
    let noteJSON = ##"{"id":3,"date":"2026-10-02","body":"Hi","body_html":"<p>Hi</p>","created_at":"2026-09-30T10:00:00","updated_at":"2026-09-30T10:00:00"}"##

    @Test func aConflictHandsBackTheExistingNote() async {
        let rec = Recorder()
        let client = APIClient(baseURL: base) { [noteJSON] request in
            let path = request.url!.path, method = request.httpMethod!
            func reply(_ status: Int, _ body: String) -> (Data, HTTPURLResponse) {
                (Data(body.utf8), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
            }
            _ = rec
            if method == "POST" { return reply(409, #"{"detail":{"message":"A note already exists for 2026-10-02","id":3}}"#) }
            if path == "/api/notes" { return reply(200, "[\(noteJSON)]") }
            return reply(404, "{}")
        }
        let model = NotesModel(client: client)
        let result = await model.save(date: "2026-10-02", body: "typed", editing: nil)
        if case .existsOnDay(let existing) = result { #expect(existing.id == 3) } else { Issue.record("expected existsOnDay, got \(result)") }
    }

    @Test func updateSendsTheDateOnlyWhenItMoved() async throws {
        let rec = Recorder(); rec.body = Data(noteJSON.utf8)
        let client = APIClient(baseURL: base, transport: rec.transport())
        _ = try await client.updateNote(id: 3, date: nil, body: "x")
        #expect(String(data: rec.requests[0].httpBody!, encoding: .utf8) == #"{"body":"x"}"#)
        _ = try await client.updateNote(id: 3, date: "2026-10-09", body: "x")
        #expect(String(data: rec.requests[1].httpBody!, encoding: .utf8)?.contains("2026-10-09") == true)
    }
}

@MainActor
struct ArchiveLoaderTests {
    final class Backend: @unchecked Sendable {
        var calls: [(String, Int, Int)] = []
    }

    @Test func pagesAndKeepsItsPlaceOnRefresh() async {
        let backend = Backend()
        let loader = ArchiveLoader<Note>(pageSize: 2) { search, limit, offset in
            backend.calls.append((search, limit, offset))
            let rows = (offset..<min(offset + limit, 5)).map {
                #"{"id":\#($0),"date":"2026-09-01","body":"n","body_html":"","created_at":"2026-09-01T00:00:00","updated_at":"2026-09-01T00:00:00"}"#
            }
            let json = #"{"items":[\#(rows.joined(separator: ","))],"has_more":\#(offset + limit < 5),"total":5}"#
            return try JSONDecoder.rally.decode(ArchivePage<Note>.self, from: Data(json.utf8))
        }
        await loader.reload()
        await loader.loadMore()
        #expect(loader.items.count == 4 && loader.hasMore)
        await loader.refreshLoaded() // an edit must not jump back to page one
        #expect(loader.items.count == 4)
        #expect(backend.calls.last! == ("", 4, 0))
    }
}
