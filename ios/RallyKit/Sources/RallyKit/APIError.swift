import Foundation

public enum APIError: Error, Equatable, Sendable {
    /// The request never got an answer: no route, VPN off, server down, timeout.
    case unreachable(String)
    /// The server answered with an error. `detail` is FastAPI's own message
    /// when it sent one, which is written for the person pressing the button.
    case http(status: Int, detail: String?)
    /// `409` whose detail names the row that is already there — Notes uses it to
    /// switch to editing that day's note instead of refusing.
    case conflict(message: String, id: Int)
    /// A 2xx whose body was not what the client expected — usually a server
    /// that is not Rally, or one older than the app.
    case decoding(String)
    case invalidURL
}

extension APIError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .unreachable:
            return "Couldn't reach the server. If it's on your tailnet, check that Tailscale is connected."
        case .http(let status, let detail):
            return detail ?? "The server answered with an error (\(status))."
        case .conflict(let message, _):
            return message
        case .decoding:
            return "The server's answer wasn't what Rally expected. Is this a Rally server, and is it up to date?"
        case .invalidURL:
            return "That isn't a valid server address."
        }
    }
}
