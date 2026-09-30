import Foundation

/// What somebody types into the "server" field, turned into a base URL.
///
/// Rally has no Tailscale-specific code: the address is whatever the phone can
/// reach, and if the Tailscale VPN is up iOS routes it like any other. So this
/// only has to forgive the ways an address gets typed — no scheme, a trailing
/// slash, stray spaces — and refuse what could never be a server.
public enum ServerURL {
    public enum Problem: Error, Equatable, Sendable {
        case empty
        case invalid
        case unsupportedScheme(String)
    }

    /// A bare `host` or `host:port` is plain `http://`: Rally ships without
    /// TLS, and a tailnet already encrypts the transport. Anything else the
    /// person typed a scheme for is taken at its word.
    public static func normalize(_ input: String) throws -> URL {
        var text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw Problem.empty }

        if !text.contains("://") { text = "http://" + text }
        guard var parts = URLComponents(string: text), let host = parts.host, !host.isEmpty else {
            throw Problem.invalid
        }
        let scheme = (parts.scheme ?? "").lowercased()
        guard scheme == "http" || scheme == "https" else { throw Problem.unsupportedScheme(scheme) }

        parts.scheme = scheme
        parts.query = nil
        parts.fragment = nil
        while parts.path.hasSuffix("/") { parts.path.removeLast() }
        guard let url = parts.url else { throw Problem.invalid }
        return url
    }
}
