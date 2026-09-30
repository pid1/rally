import Foundation

/// One field of a partial update.
///
/// Rally's `PUT` endpoints distinguish three things that Swift's `Optional`
/// cannot: a key that is **absent** (leave the stored value alone), a key that
/// is **`null`** (clear it), and a key with a **value**. Un-assigning a task or
/// clearing a due date is the `null` case, and a client that can only omit or
/// set can never do either. That is why the API layer is hand-written rather
/// than generated: the generated types turn `nil` into an omitted key.
public enum Patch<Value: Encodable & Sendable>: Sendable {
    case unset
    case null
    case value(Value)

    /// `nil` clears the field, which is what "no assignee" means.
    public static func clearing(_ value: Value?) -> Patch<Value> {
        value.map { .value($0) } ?? .null
    }
}

/// A request body assembled key by key, so an ``Patch/unset`` field is not in
/// the JSON at all.
public struct PatchBody: Sendable, Encodable {
    public private(set) var fields: [String: JSONValue] = [:]

    public init() {}

    public func set<V: Encodable & Sendable>(_ key: String, _ patch: Patch<V>) throws -> PatchBody {
        var copy = self
        switch patch {
        case .unset: break
        case .null: copy.fields[key] = .null
        case .value(let v):
            let data = try JSONEncoder().encode(v)
            copy.fields[key] = try JSONDecoder().decode(JSONValue.self, from: data)
        }
        return copy
    }

    public func encode(to encoder: Encoder) throws {
        try fields.encode(to: encoder)
    }
}
