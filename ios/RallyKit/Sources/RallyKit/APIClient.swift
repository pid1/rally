import Foundation

/// How a request reaches the network. A closure, not a protocol, so a test
/// hands the client canned answers without a server or `URLProtocol` tricks.
public typealias Transport = @Sendable (URLRequest) async throws -> (Data, HTTPURLResponse)

public struct APIClient: Sendable {
    public let baseURL: URL
    private let transport: Transport
    private let decoder: JSONDecoder = .rally

    public init(baseURL: URL, transport: @escaping Transport = APIClient.urlSessionTransport) {
        self.baseURL = baseURL
        self.transport = transport
    }

    /// Short timeouts on purpose: a tailnet that is down should say so in
    /// seconds, not leave a spinner for a minute.
    public static let urlSessionTransport: Transport = { request in
        let (data, response) = try await URLSession.rally.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        return (data, http)
    }

    // MARK: Requests

    public func get<T: Decodable & Sendable>(
        _ route: Route, _ args: [String: String] = [:], query: [URLQueryItem] = []
    ) async throws -> T {
        try decode(try await execute(route, args, query: query, body: nil))
    }

    /// A request with a JSON body, decoding the answer.
    public func send<B: Encodable & Sendable, T: Decodable & Sendable>(
        _ route: Route, _ args: [String: String] = [:], query: [URLQueryItem] = [], body: B
    ) async throws -> T {
        try decode(try await execute(route, args, query: query, body: JSONEncoder().encode(body)))
    }

    /// A request with a JSON body whose answer is not used.
    public func perform<B: Encodable & Sendable>(
        _ route: Route, _ args: [String: String] = [:], query: [URLQueryItem] = [], body: B
    ) async throws {
        _ = try await execute(route, args, query: query, body: JSONEncoder().encode(body))
    }

    /// Fire a request whose answer is not used (`DELETE` answers `204`).
    public func perform(_ route: Route, _ args: [String: String] = [:], query: [URLQueryItem] = []) async throws {
        _ = try await execute(route, args, query: query, body: nil)
    }

    // MARK: Plumbing

    func execute(_ route: Route, _ args: [String: String], query: [URLQueryItem], body: Data?) async throws -> Data {
        let request = try makeRequest(route, args, query: query, body: body)
        let data: Data
        let response: HTTPURLResponse
        do {
            (data, response) = try await transport(request)
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.unreachable(error.localizedDescription)
        }
        guard (200..<300).contains(response.statusCode) else {
            throw APIError.http(status: response.statusCode, detail: Self.detail(in: data))
        }
        return data
    }

    func makeRequest(_ route: Route, _ args: [String: String], query: [URLQueryItem], body: Data?) throws -> URLRequest {
        var path = route.template
        for (key, value) in args {
            // Path segments, not query values: `/` and `?` in an id must not escape.
            let allowed = CharacterSet.urlPathAllowed.subtracting(CharacterSet(charactersIn: "/?#"))
            guard let encoded = value.addingPercentEncoding(withAllowedCharacters: allowed) else {
                throw APIError.invalidURL
            }
            path = path.replacingOccurrences(of: "{\(key)}", with: encoded)
        }
        guard !path.contains("{"),
              var parts = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)
        else { throw APIError.invalidURL }

        parts.percentEncodedPath = parts.percentEncodedPath.trimmingSlash + path
        parts.queryItems = query.isEmpty ? nil : query
        guard let url = parts.url else { throw APIError.invalidURL }

        var request = URLRequest(url: url)
        request.httpMethod = route.method.rawValue
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func decode<T: Decodable>(_ data: Data) throws -> T {
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError.decoding(String(describing: error)) }
    }

    /// FastAPI's `{"detail": "…"}`. A validation failure's `detail` is a list
    /// of objects instead, which is not something to show a person.
    static func detail(in data: Data) -> String? {
        guard let object = try? JSONDecoder().decode([String: JSONValue].self, from: data),
              case .string(let text)? = object["detail"] else { return nil }
        return text
    }
}

private extension String {
    var trimmingSlash: String {
        var s = self
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }
}

extension URLSession {
    static let rally: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 10
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()
}
