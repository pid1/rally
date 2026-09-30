import Foundation
import Testing
@testable import RallyKit

/// Records what the client sent and answers with whatever the test queued.
final class Recorder: @unchecked Sendable {
    private let lock = NSLock()
    private(set) var requests: [URLRequest] = []
    var status = 200
    var body = Data("[]".utf8)
    var failure: Error?

    private func record(_ request: URLRequest) {
        lock.withLock { requests.append(request) }
    }

    func transport() -> Transport {
        { [self] request in
            record(request)
            if let failure { throw failure }
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
            return (body, response)
        }
    }
}

struct APIClientTests {
    let base = URL(string: "http://rally.test:8000")!

    @Test func buildsURLAndHeaders() async throws {
        let rec = Recorder()
        _ = try await APIClient(baseURL: base, transport: rec.transport()).familyMembers()
        let request = try #require(rec.requests.first)
        #expect(request.url?.absoluteString == "http://rally.test:8000/api/family")
        #expect(request.httpMethod == "GET")
        #expect(request.value(forHTTPHeaderField: "Accept") == "application/json")
    }

    @Test func keepsASubpathBase() async throws {
        let rec = Recorder()
        let client = APIClient(baseURL: URL(string: "https://h.example.com/rally")!, transport: rec.transport())
        _ = try await client.familyMembers()
        #expect(rec.requests.first?.url?.absoluteString == "https://h.example.com/rally/api/family")
    }

    @Test func escapesPathArguments() async throws {
        let rec = Recorder()
        try await APIClient(baseURL: base, transport: rec.transport()).forgetDevice(id: "a/b?c")
        #expect(rec.requests.first?.url?.absoluteString == "http://rally.test:8000/api/devices/a%2Fb%3Fc")
        #expect(rec.requests.first?.httpMethod == "DELETE")
    }

    @Test func announceOmitsLabelWhenNotGiven() async throws {
        let rec = Recorder()
        rec.body = Data(#"{"id":"d1","label":null,"created_at":"2026-09-30T10:00:00","last_seen_at":"2026-09-30T10:00:00.123456","answer_count":0}"#.utf8)
        let client = APIClient(baseURL: base, transport: rec.transport())
        try await client.announceDevice(id: "d1")
        #expect(String(data: rec.requests[0].httpBody!, encoding: .utf8) == "{}")
        try await client.announceDevice(id: "d1", label: "iPhone")
        #expect(String(data: rec.requests[1].httpBody!, encoding: .utf8) == #"{"label":"iPhone"}"#)
        #expect(rec.requests[1].value(forHTTPHeaderField: "Content-Type") == "application/json")
    }

    @Test func mapsTransportFailureToUnreachable() async {
        let rec = Recorder()
        rec.failure = URLError(.cannotConnectToHost)
        await #expect(throws: APIError.self) {
            _ = try await APIClient(baseURL: base, transport: rec.transport()).familyMembers()
        }
        let status = await APIClient(baseURL: base, transport: rec.transport()).checkConnection()
        #expect(status == .unreachable)
    }

    @Test func surfacesFastAPIDetail() async {
        let rec = Recorder()
        rec.status = 409
        rec.body = Data(#"{"detail":"Already there"}"#.utf8)
        do {
            _ = try await APIClient(baseURL: base, transport: rec.transport()).familyMembers()
            Issue.record("expected a throw")
        } catch {
            #expect(error as? APIError == .http(status: 409, detail: "Already there"))
        }
    }

    @Test func validationMessagesWrittenForPeopleAreShown() {
        let data = Data(#"{"detail":[{"loc":["body"],"msg":"Value error, Notes can't contain HTML tags."}]}"#.utf8)
        #expect(APIClient.detail(in: data) == "Notes can't contain HTML tags.")
        #expect(APIClient.detail(in: Data(#"{"detail":[{"loc":["body"]}]}"#.utf8)) == nil)
    }

    @Test func aConflictCarriesTheExistingRowsID() async {
        let rec = Recorder()
        rec.status = 409
        rec.body = Data(#"{"detail":{"message":"A note already exists for 2026-09-30","id":7}}"#.utf8)
        do {
            _ = try await APIClient(baseURL: base, transport: rec.transport()).familyMembers()
            Issue.record("expected a throw")
        } catch {
            #expect(error as? APIError == .conflict(message: "A note already exists for 2026-09-30", id: 7))
        }
    }

    @Test func connectionCheckClassifiesAnswers() async {
        let rec = Recorder()
        let client = APIClient(baseURL: base, transport: rec.transport())

        rec.body = Data(##"[{"id":1,"name":"Mom","color":"#315277","created_at":"2026-01-01T00:00:00","updated_at":"2026-01-01T00:00:00","notifications":{}}]"##.utf8)
        #expect(await client.checkConnection() == .connected(familyCount: 1))

        rec.body = Data("<html>nginx</html>".utf8)
        #expect(await client.checkConnection() == .notRally)

        rec.status = 404
        #expect(await client.checkConnection() == .notRally)
    }

    @Test(arguments: [
        "2026-09-30T14:05:00",
        "2026-09-30T14:05:00.123456",
        "2026-09-30T14:05:00Z",
        "2026-09-30T14:05:00.123456+00:00",
    ])
    func parsesBothDateShapesAsUTC(text: String) throws {
        let date = try #require(RallyDate.parse(text))
        #expect(Int(date.timeIntervalSince1970) == 1_790_777_100)
    }
}
