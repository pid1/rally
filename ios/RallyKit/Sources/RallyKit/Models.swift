import Foundation

public struct FamilyMember: Codable, Sendable, Identifiable, Equatable {
    public let id: Int
    public var name: String
    public var color: String
    public var pushoverUserKey: String?
    public var pushoverDevice: String?
    public var notifications: [String: Bool]

    enum CodingKeys: String, CodingKey {
        case id, name, color, notifications
        case pushoverUserKey = "pushover_user_key"
        case pushoverDevice = "pushover_device"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        color = try c.decode(String.self, forKey: .color)
        pushoverUserKey = try c.decodeIfPresent(String.self, forKey: .pushoverUserKey)
        pushoverDevice = try c.decodeIfPresent(String.self, forKey: .pushoverDevice)
        notifications = try c.decodeIfPresent([String: Bool].self, forKey: .notifications) ?? [:]
    }
}

public struct Device: Codable, Sendable, Identifiable, Equatable {
    public let id: String
    public var label: String?
    public let createdAt: Date
    public let lastSeenAt: Date
    public let answerCount: Int

    enum CodingKeys: String, CodingKey {
        case id, label
        case createdAt = "created_at"
        case lastSeenAt = "last_seen_at"
        case answerCount = "answer_count"
    }
}
