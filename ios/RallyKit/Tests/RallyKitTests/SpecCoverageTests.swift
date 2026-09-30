import Foundation
import Testing
@testable import RallyKit

/// Every route the app calls must exist in the checked-in OpenAPI spec with the
/// same method. An endpoint renamed on the server then fails here, in `swift
/// test`, rather than as a blank screen on somebody's phone.
struct SpecCoverageTests {
    private static let specURL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("openapi.json")

    @Test func everyRouteIsInTheSpec() throws {
        let data = try Data(contentsOf: Self.specURL)
        let spec = try JSONDecoder().decode([String: JSONValue].self, from: data)
        guard case .object(let paths)? = spec["paths"] else { Issue.record("no paths"); return }

        for route in Route.allCases {
            guard case .object(let methods)? = paths[route.template] else {
                Issue.record("\(route.template) is not in ios/openapi.json")
                continue
            }
            #expect(methods[route.method.rawValue.lowercased()] != nil,
                    "\(route.method.rawValue) \(route.template) is not in ios/openapi.json")
        }
    }
}
