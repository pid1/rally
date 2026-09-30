import Foundation

public enum HTTPMethod: String, Sendable { case get = "GET", post = "POST", put = "PUT", delete = "DELETE" }

/// Every endpoint the app calls, by the path template the server declares.
///
/// Routes are an enum rather than string literals at call sites so that
/// `SpecCoverageTests` can check each one against `ios/openapi.json`: a
/// renamed endpoint then fails a test here instead of a screen in the app.
public enum Route: String, CaseIterable, Sendable {
    case listFamily
    case listDevices
    case announceDevice
    case forgetDevice
    case devicePreferences
    case saveMemberPreferences
    case preferenceCatalog
    case dashboard

    public var method: HTTPMethod {
        switch self {
        case .listFamily, .listDevices, .devicePreferences, .preferenceCatalog, .dashboard: .get
        case .announceDevice, .saveMemberPreferences: .put
        case .forgetDevice: .delete
        }
    }

    public var template: String {
        switch self {
        case .listFamily: "/api/family"
        case .listDevices: "/api/devices"
        case .announceDevice, .forgetDevice: "/api/devices/{device_id}"
        case .devicePreferences: "/api/devices/{device_id}/preferences"
        case .saveMemberPreferences: "/api/devices/{device_id}/preferences/{member_id}"
        case .preferenceCatalog: "/api/preferences/catalog"
        case .dashboard: "/api/dashboard"
        }
    }
}
