import Foundation

/// Where this phone keeps what it knows about Rally. `UserDefaults` is
/// injected so tests get an empty, private store.
public struct LocalSettings: @unchecked Sendable {
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    // MARK: Server

    public var serverURL: URL? {
        get { defaults.string(forKey: "rally.server").flatMap(URL.init(string:)) }
        nonmutating set { defaults.set(newValue?.absoluteString, forKey: "rally.server") }
    }

    // MARK: This device

    /// The token this install answers to. Minted on first use and kept: every
    /// stored answer hangs on it, so it must not change between launches.
    /// Reading it is what creates it, so there is no state where it is missing.
    public var deviceID: String {
        if let existing = defaults.string(forKey: "rally.deviceID"), !existing.isEmpty { return existing }
        let fresh = UUID().uuidString.lowercased()
        defaults.set(fresh, forKey: "rally.deviceID")
        return fresh
    }

    /// Which family member is holding this device. The other half of the key
    /// for a person's per-device settings; it never leaves the phone except as
    /// the id in a URL.
    public var memberID: Int? {
        get { defaults.object(forKey: "rally.memberID") as? Int }
        nonmutating set { defaults.set(newValue, forKey: "rally.memberID") }
    }

    /// Forget the server and who this is, but keep the device token: signing
    /// out of a server is not becoming a different device.
    public func disconnect() {
        defaults.removeObject(forKey: "rally.server")
        defaults.removeObject(forKey: "rally.memberID")
    }
}
