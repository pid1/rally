import Foundation

public enum ConnectionStatus: Equatable, Sendable {
    case connected(familyCount: Int)
    /// No answer at all. The usual cause is the VPN being off.
    case unreachable
    /// Something answered, and it was not Rally.
    case notRally
    /// iOS refused the address itself (plain HTTP under ATS); nothing was sent.
    case blocked
    case failed(String)
}

extension APIClient {
    /// Ask the one question every Rally install answers: who is in the family.
    /// Telling "nobody is there" from "something is there but it is not Rally"
    /// matters because the fix differs — connect to the VPN, or fix the address.
    public func checkConnection() async -> ConnectionStatus {
        do {
            return .connected(familyCount: try await familyMembers().count)
        } catch let error as APIError {
            switch error {
            case .unreachable: return .unreachable
            case .blockedByATS: return .blocked
            case .decoding: return .notRally
            case .http(let status, _) where status == 404: return .notRally
            default: return .failed(error.localizedDescription)
            }
        } catch {
            return .failed(error.localizedDescription)
        }
    }
}
