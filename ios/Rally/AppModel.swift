import Observation
import RallyKit
import SwiftUI

/// What the whole app knows: which server, whether it answers, who is in the
/// family and which of them is holding this phone.
@MainActor @Observable
final class AppModel {
    let settings: LocalSettings
    private(set) var client: APIClient?
    private(set) var connection: ConnectionStatus?
    private(set) var members: [FamilyMember] = []
    private(set) var memberID: Int?
    private(set) var isRefreshing = false

    init(settings: LocalSettings = LocalSettings()) {
        // UI tests start from a blank slate; nothing else sets this.
        if ProcessInfo.processInfo.arguments.contains("-rally-reset") { settings.disconnect() }
        self.settings = settings
        self.memberID = settings.memberID
        if let url = settings.serverURL { client = APIClient(baseURL: url) }
    }

    var serverURL: URL? { settings.serverURL }
    var hasServer: Bool { client != nil }
    var deviceID: String { settings.deviceID }
    var boundMember: FamilyMember? { members.first { $0.id == memberID } }
    var isUnreachable: Bool { connection == .unreachable }

    var deviceLabel: String {
        #if canImport(UIKit)
        UIDevice.current.model // "iPhone" / "iPad": the name the web registry guesses too
        #else
        "Mac"
        #endif
    }

    // MARK: Connecting

    enum ConnectResult: Equatable { case connected(familyCount: Int), failed(String) }

    /// Validate an address and, only if a Rally server answers there, keep it.
    /// Nothing is saved on failure, so a typo never replaces a working server.
    func connect(to input: String) async -> ConnectResult {
        let url: URL
        do { url = try ServerURL.normalize(input) }
        catch ServerURL.Problem.empty { return .failed("Enter your Rally server's address.") }
        catch ServerURL.Problem.unsupportedScheme { return .failed("Use an http:// or https:// address.") }
        catch { return .failed("That isn't a valid server address.") }

        let candidate = APIClient(baseURL: url)
        switch await candidate.checkConnection() {
        case .connected(let count):
            settings.serverURL = url
            client = candidate
            connection = .connected(familyCount: count)
            await refresh()
            return .connected(familyCount: count)
        case .unreachable:
            return .failed("Couldn't reach \(url.host() ?? "that address"). If it's on your tailnet, check that Tailscale is connected on this phone.")
        case .notRally:
            return .failed("Something answered, but it doesn't look like Rally. Check the address and port.")
        case .failed(let message):
            return .failed(message)
        }
    }

    func disconnect() {
        settings.disconnect()
        client = nil
        connection = nil
        members = []
        memberID = nil
    }

    // MARK: Refreshing

    /// Re-check the server, say hello as this device, and reload the family.
    func refresh() async {
        guard let client, !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }

        connection = await client.checkConnection()
        guard case .connected = connection else { return }

        do { members = try await client.familyMembers() } catch { /* the check above already reported */ }
        // No label: launching must not overwrite a name somebody typed. The
        // first hello needs one so the registry is not a column of tokens.
        let known = (try? await client.devices())?.contains { $0.id == deviceID } ?? false
        _ = try? await client.announceDevice(id: deviceID, label: known ? nil : deviceLabel)

        // A member deleted on the server cannot stay bound to this phone.
        if let memberID, !members.contains(where: { $0.id == memberID }) { bind(to: nil) }
    }

    func bind(to id: Int?) {
        memberID = id
        settings.memberID = id
    }

    func rename(device label: String) async throws {
        guard let client else { return }
        try await client.announceDevice(id: deviceID, label: label)
    }

    func currentDeviceLabel() async -> String? {
        guard let client else { return nil }
        return (try? await client.devices())?.first { $0.id == deviceID }?.label
    }
}
