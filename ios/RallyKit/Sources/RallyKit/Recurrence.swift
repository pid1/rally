import Foundation

/// The Repeats choices Rally offers, and what each compiles to. The UI speaks
/// Rally's dialect; the API stores RFC 5545. The mapping is deliberately small —
/// anything more expressive is typed as Custom… or preserved read-only as `other`.
public enum RepeatChoice: String, CaseIterable, Sendable, Identifiable {
    case none, daily, weekly, biweekly, monthly, yearly, custom
    /// A stored rule richer than even the custom controls (BYSETPOS, BYMONTH…).
    /// Selecting it only ever means "leave it alone"; it is never offered.
    case other
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .none: "Does not repeat"
        case .daily: "Daily"
        case .weekly: "Weekly"
        case .biweekly: "Every 2 weeks"
        case .monthly: "Monthly"
        case .yearly: "Yearly"
        case .custom: "Custom…"
        case .other: "Custom schedule"
        }
    }
}

public enum Ends: Equatable, Sendable {
    case never
    case until(String)   // YYYY-MM-DD, inclusive
    case count(Int)
}

/// Everything the Custom… controls can build.
public struct CustomRepeat: Equatable, Sendable {
    public enum Freq: String, CaseIterable, Sendable { case daily, weekly, monthly, yearly }
    public enum MonthlyMode: String, Sendable { case day, weekday }

    public var freq: Freq = .weekly
    public var interval = 1
    public var weekdaysOnly = false
    /// Two-letter codes, SU…SA.
    public var weekdays: Set<String> = []
    public var monthlyMode: MonthlyMode = .day
    /// 1…31, or -1 for "Last day". `BYMONTHDAY=31` *skips* months with no 31st;
    /// `-1` lands on the end of every month, which is what rent and meter
    /// readings mean and there is no other way to say.
    public var monthDay = 1
    /// `1`, `2`, `3`, `4` or `-1` (the last one).
    public var ordinal = "1"
    public var weekday = "MO"

    public init() {}

    /// The controls as a cadence, or `nil` when they describe none — a weekly
    /// rule with no days is not a rule, and saving one is saving something
    /// nobody meant.
    public func cadence() -> String? {
        let n = max(1, interval)
        let every = n > 1 ? ";INTERVAL=\(n)" : ""
        switch freq {
        case .daily:
            // BYDAY on a daily rule is how "every weekday" is written, and an
            // interval cannot ride with it: RRULE cannot say "every third weekday".
            return weekdaysOnly ? "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR" : "FREQ=DAILY\(every)"
        case .weekly:
            guard !weekdays.isEmpty else { return nil }
            // Week order, not click order, so one selection compiles to one string.
            let days = Recurrence.weekdayCodes.filter(weekdays.contains).joined(separator: ",")
            return "FREQ=WEEKLY\(every);BYDAY=\(days)"
        case .monthly:
            return monthlyMode == .weekday
                ? "FREQ=MONTHLY\(every);BYDAY=\(ordinal)\(weekday)"
                : "FREQ=MONTHLY\(every);BYMONTHDAY=\(monthDay)"
        case .yearly:
            return "FREQ=YEARLY\(every)"
        }
    }
}

public enum RruleChange: Equatable, Sendable {
    /// Say nothing about recurrence — the API's "leave the stored rule alone".
    case leave
    /// Set it (`nil` clears it, making the event a single one).
    case set(String?)
}

public enum Recurrence {
    /// Week order, Sunday first — the order the grid, the weekday picker and the
    /// read-back sentence all use.
    public static let weekdayCodes = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]
    static let customParts: Set<String> = ["FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "UNTIL", "COUNT"]

    private static let utc: Calendar = { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "UTC")!; return c }()

    /// The weekday code and day-of-month of a `YYYY-MM-DD…` string.
    static func dayInfo(_ iso: String) -> (code: String, day: Int)? {
        guard let date = DayString.date(String(iso.prefix(10)), calendar: utc) else { return nil }
        return (weekdayCodes[utc.component(.weekday, from: date) - 1], utc.component(.day, from: date))
    }

    public static func rrule(for choice: RepeatChoice, startISO: String) -> String? {
        guard let info = dayInfo(startISO) else { return choice == .daily ? "FREQ=DAILY" : nil }
        switch choice {
        case .daily: return "FREQ=DAILY"
        case .weekly: return "FREQ=WEEKLY;BYDAY=\(info.code)"
        case .biweekly: return "FREQ=WEEKLY;INTERVAL=2;BYDAY=\(info.code)"
        case .monthly: return "FREQ=MONTHLY;BYMONTHDAY=\(info.day)"
        case .yearly: return "FREQ=YEARLY"
        default: return nil
        }
    }

    /// RFC 5545 requires `UNTIL` to match `DTSTART`'s value type: a bare date for
    /// an all-day event, a UTC instant otherwise. End-of-day is what makes the
    /// chosen day inclusive, as the field says it is.
    public static func untilPart(_ dateISO: String, allDay: Bool) -> String? {
        let digits = dateISO.replacingOccurrences(of: "-", with: "")
        guard digits.count == 8, digits.allSatisfy(\.isNumber) else { return nil }
        return allDay ? "UNTIL=\(digits)" : "UNTIL=\(digits)T235959Z"
    }

    static func endsPart(_ ends: Ends, allDay: Bool) -> String? {
        switch ends {
        case .never: nil
        case .until(let day): untilPart(day, allDay: allDay)
        case .count(let n): n > 0 ? "COUNT=\(n)" : nil
        }
    }

    /// A complete rule from whatever the Repeats row shows. `Ends` bounds any
    /// repeating event, not only a custom one: a bound is orthogonal to a cadence.
    public static func compile(_ choice: RepeatChoice, custom: CustomRepeat, ends: Ends, startISO: String, allDay: Bool) -> String? {
        let cadence = choice == .custom ? custom.cadence() : rrule(for: choice, startISO: startISO)
        guard let cadence else { return nil }
        if let bound = endsPart(ends, allDay: allDay) { return "\(cadence);\(bound)" }
        return cadence
    }

    public static func parts(_ rrule: String?) -> [String: String] {
        var out: [String: String] = [:]
        let body = (rrule ?? "").uppercased().replacingOccurrences(of: "RRULE:", with: "")
        for chunk in body.split(separator: ";") {
            let pair = chunk.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            if let name = pair.first, !name.isEmpty { out[name] = pair.count > 1 ? pair[1] : "" }
        }
        return out
    }

    /// Every part sorted, the bound included: a rule differing only by its end
    /// date is a real edit rather than something to preserve behind the form's back.
    public static func normalize(_ rrule: String?) -> String {
        guard let rrule else { return "" }
        return rrule.uppercased().replacingOccurrences(of: "RRULE:", with: "")
            .split(separator: ";").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }.sorted().joined(separator: ";")
    }

    /// The cadence half only — `UNTIL`/`COUNT` answer "until when", not "how often".
    static func cadenceParts(_ rrule: String?) -> String {
        guard let rrule else { return "" }
        return rrule.uppercased().replacingOccurrences(of: "RRULE:", with: "")
            .split(separator: ";").map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && !$0.hasPrefix("UNTIL=") && !$0.hasPrefix("COUNT=") }.sorted().joined(separator: ";")
    }

    static func sameCadence(_ a: String?, _ b: String?) -> Bool { cadenceParts(a) == cadenceParts(b) }

    /// A stored rule as custom-control values, or `nil` when the controls cannot
    /// hold it — which is what decides `custom` against `other`.
    public static func customFromRrule(_ rrule: String?) -> CustomRepeat? {
        let p = parts(rrule)
        if p.keys.contains(where: { !customParts.contains($0) }) { return nil }
        guard let freq = CustomRepeat.Freq(rawValue: (p["FREQ"] ?? "").lowercased()) else { return nil }
        let interval = p["INTERVAL"].flatMap { Int($0) } ?? 1
        guard interval > 0 else { return nil }
        let byday = (p["BYDAY"] ?? "").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }

        var out = CustomRepeat()
        out.freq = freq
        out.interval = interval
        switch freq {
        case .daily:
            if byday.isEmpty { return out }
            // A partial weekday set on a daily rule is legal but not something
            // these controls can build, so it stays read-only.
            guard Set(byday) == ["MO", "TU", "WE", "TH", "FR"], interval == 1 else { return nil }
            out.weekdaysOnly = true
            return out
        case .weekly:
            guard !byday.isEmpty, byday.allSatisfy(weekdayCodes.contains) else { return nil }
            out.weekdays = Set(byday)
            return out
        case .monthly:
            if let monthDay = p["BYMONTHDAY"], byday.isEmpty {
                guard let day = Int(monthDay), (1...31).contains(day) || day == -1 else { return nil }
                out.monthlyMode = .day; out.monthDay = day
                return out
            }
            if byday.count == 1, p["BYMONTHDAY"] == nil {
                let text = byday[0]
                let code = String(text.suffix(2))
                guard weekdayCodes.contains(code), let ordinal = Int(text.dropLast(2)), [1, 2, 3, 4, -1].contains(ordinal) else { return nil }
                out.monthlyMode = .weekday; out.ordinal = String(ordinal); out.weekday = code
                return out
            }
            return nil
        case .yearly:
            // Yearly takes an interval and nothing else.
            return byday.isEmpty && p["BYMONTHDAY"] == nil ? out : nil
        }
    }

    /// Which choice a stored rule came from — a comparison against what each
    /// choice compiles to, never a prefix test. `FREQ=WEEKLY;BYDAY=TU,TH` starts
    /// with `FREQ=WEEKLY` but is not "Weekly on this day", and answering that it
    /// is, is how a rule gets narrowed on the next save.
    public static func choice(from rrule: String?, startISO: String) -> RepeatChoice {
        guard let rrule, !rrule.isEmpty else { return .none }
        for choice in [RepeatChoice.daily, .weekly, .biweekly, .monthly, .yearly]
        where sameCadence(rrule, Self.rrule(for: choice, startISO: startISO)) { return choice }
        return customFromRrule(rrule) != nil ? .custom : .other
    }

    public static func ends(from rrule: String?) -> Ends {
        let p = parts(rrule)
        if let until = p["UNTIL"], until.count >= 8 {
            let d = Array(until.prefix(8))
            return .until("\(String(d[0..<4]))-\(String(d[4..<6]))-\(String(d[6..<8]))")
        }
        if let count = p["COUNT"].flatMap({ Int($0) }), count > 0 { return .count(count) }
        return .never
    }

    /// What to send for `rrule`. Omitting it is meaningful: `EventUpdate` defaults
    /// it to unset, so the form only speaks about recurrence when it was changed.
    public static func change(choice: RepeatChoice, compiled: String?, stored: String?, isEditing: Bool) -> RruleChange {
        // `other` names the stored rule rather than building one, so choosing it
        // can only mean "leave it".
        if choice == .other { return .leave }
        if !isEditing { return .set(compiled) }
        return normalize(compiled) == normalize(stored) ? .leave : .set(compiled)
    }
}
