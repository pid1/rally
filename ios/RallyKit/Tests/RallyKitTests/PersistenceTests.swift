import Foundation
import Testing
@testable import RallyKit

struct PersistenceTests {
    private func fresh() -> LocalSettings {
        LocalSettings(defaults: UserDefaults(suiteName: "rally.test.\(UUID().uuidString)")!)
    }

    @Test func deviceIDIsMintedOnceAndKept() {
        let settings = fresh()
        let first = settings.deviceID
        #expect(!first.isEmpty)
        #expect(settings.deviceID == first)
    }

    @Test func disconnectKeepsTheDeviceToken() {
        let settings = fresh()
        let token = settings.deviceID
        settings.serverURL = URL(string: "http://rally:8000")
        settings.memberID = 3

        settings.disconnect()

        #expect(settings.serverURL == nil)
        #expect(settings.memberID == nil)
        #expect(settings.deviceID == token)
    }
}
