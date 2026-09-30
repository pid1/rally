import Foundation

extension APIClient {
    public func familyMembers() async throws -> [FamilyMember] {
        try await get(.listFamily)
    }

    public func devices() async throws -> [Device] {
        try await get(.listDevices)
    }

    /// Say hello as this device. Passing no `label` leaves a stored name alone,
    /// so launching the app cannot overwrite one somebody typed.
    @discardableResult
    public func announceDevice(id: String, label: String? = nil) async throws -> Device {
        let body = try PatchBody().set("label", label.map { Patch.value($0) } ?? Patch<String>.unset)
        return try await send(.announceDevice, ["device_id": id], body: body)
    }

    public func forgetDevice(id: String) async throws {
        try await perform(.forgetDevice, ["device_id": id])
    }
}
