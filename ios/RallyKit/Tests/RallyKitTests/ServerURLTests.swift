import Testing
@testable import RallyKit

struct ServerURLTests {
    @Test(arguments: [
        ("rally.tail1234.ts.net:8000", "http://rally.tail1234.ts.net:8000"),
        ("  192.168.1.20:8000/  ", "http://192.168.1.20:8000"),
        ("http://rally:8000///", "http://rally:8000"),
        ("https://rally.example.com", "https://rally.example.com"),
        ("HTTPS://Rally.example.com/?x=1#frag", "https://Rally.example.com"),
        ("100.101.102.103:8000", "http://100.101.102.103:8000"),
    ])
    func normalizes(input: String, expected: String) throws {
        #expect(try ServerURL.normalize(input).absoluteString == expected)
    }

    @Test func keepsASubpath() throws {
        #expect(try ServerURL.normalize("https://home.example.com/rally/").absoluteString
                == "https://home.example.com/rally")
    }

    @Test func rejectsEmpty() {
        #expect(throws: ServerURL.Problem.empty) { try ServerURL.normalize("   ") }
    }

    @Test func rejectsOtherSchemes() {
        #expect(throws: ServerURL.Problem.unsupportedScheme("ftp")) { try ServerURL.normalize("ftp://rally") }
    }

    @Test func rejectsNoHost() {
        #expect(throws: ServerURL.Problem.invalid) { try ServerURL.normalize("http://") }
    }
}
