import Foundation
import Testing
@testable import RallyKit

struct PatchTests {
    private func json(_ body: PatchBody) throws -> [String: Any] {
        let data = try JSONEncoder().encode(body)
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }

    @Test func unsetKeyIsAbsent() throws {
        let body = try PatchBody().set("due_date", Patch<String>.unset)
        #expect(try json(body).isEmpty)
    }

    @Test func nullKeyIsSentAsNull() throws {
        let body = try PatchBody().set("assigned_to", Patch<Int>.null)
        let out = try json(body)
        #expect(out.keys.contains("assigned_to"))
        #expect(out["assigned_to"] is NSNull)
    }

    @Test func valueKeyIsSent() throws {
        let body = try PatchBody().set("title", Patch.value("Buy milk")).set("qty", Patch.value(2))
        let out = try json(body)
        #expect(out["title"] as? String == "Buy milk")
        #expect(out["qty"] as? Int == 2)
    }

    @Test func clearingNilMeansNull() throws {
        let none: Int? = nil
        #expect(try json(PatchBody().set("a", .clearing(none)))["a"] is NSNull)
        #expect(try json(PatchBody().set("a", .clearing(3)))["a"] as? Int == 3)
    }
}
