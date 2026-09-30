import Foundation

/// Everything the Add/Edit Event form holds, and the request it becomes.
public struct EventDraft: Sendable, Equatable {
    public var title = ""
    public var description = ""
    public var location = ""
    public var allDay = false
    /// `YYYY-MM-DDTHH:MM` — local wall times in the install's zone. The app does
    /// no timezone arithmetic: the server owns zones, DST gaps and overlaps.
    public var timedStart = ""
    public var timedEnd = ""
    /// `YYYY-MM-DD`. An all-day `end` is the **inclusive** last day.
    public var dayStart = ""
    public var dayEnd = ""
    public var calendarID: Int?
    public var attendees: Set<Int> = []
    public var notifyMinutes: Int?
    public var repeatChoice: RepeatChoice = .none
    public var custom = CustomRepeat()
    public var ends: Ends = .never

    /// The times the form opened on, so a save can tell "moved it" from "renamed
    /// it and left the time alone". `nil` while adding.
    var original: Original?
    struct Original: Sendable, Equatable {
        var allDay: Bool; var timedStart: String; var timedEnd: String; var dayStart: String; var dayEnd: String
    }

    public init() {}

    /// A new event on `day`, 9–10 AM, the default the Add dialog offers.
    public init(day: String) {
        timedStart = "\(day)T09:00"; timedEnd = "\(day)T10:00"; dayStart = day; dayEnd = day
    }

    /// Editing one occurrence of `series`. The occurrence's own start and end fill
    /// the form — the series' would put every occurrence after the first on the
    /// wrong date, and then send that date back.
    public init(series: EventSeries, occurrence: Occurrence) {
        title = series.title; description = series.description ?? ""; location = series.location ?? ""
        allDay = series.allDay
        let startForm = occurrence.startForm.isEmpty ? series.start : occurrence.startForm
        let endForm = occurrence.endForm.isEmpty ? series.end : occurrence.endForm
        let startDay = String(startForm.prefix(10))
        if series.allDay {
            dayStart = startForm; dayEnd = endForm
            timedStart = "\(startDay)T09:00"; timedEnd = "\(startDay)T10:00"
        } else {
            timedStart = startForm; timedEnd = endForm
            dayStart = startDay; dayEnd = String(endForm.prefix(10))
        }
        // Opens where this occurrence sits, which an override may have moved off the series' calendar.
        calendarID = occurrence.calendarID ?? series.calendarID
        attendees = Set(series.attendeeIDs)
        notifyMinutes = series.notifyMinutesBefore
        let seriesDay = String(series.start.prefix(10))
        repeatChoice = Recurrence.choice(from: series.rrule, startISO: seriesDay)
        custom = Recurrence.customFromRrule(series.rrule) ?? CustomRepeat()
        ends = Recurrence.ends(from: series.rrule)
        seriesRule = series.rrule
        original = Original(allDay: allDay, timedStart: timedStart, timedEnd: timedEnd, dayStart: dayStart, dayEnd: dayEnd)
    }

    var seriesRule: String?

    public var start: String { allDay ? dayStart : timedStart }
    public var end: String { allDay ? dayEnd : timedEnd }
    public var isEditing: Bool { original != nil }

    public var timesUntouched: Bool {
        guard let o = original, allDay == o.allDay else { return false }
        return allDay ? (dayStart == o.dayStart && dayEnd == o.dayEnd) : (timedStart == o.timedStart && timedEnd == o.timedEnd)
    }

    /// The rule the Repeats row currently describes.
    public var compiledRule: String? {
        Recurrence.compile(repeatChoice, custom: custom, ends: ends, startISO: start, allDay: allDay)
    }

    /// Picking a calendar checks its owner, because "on Maya's calendar" almost
    /// always means Maya is involved. It never *unchecks* anyone: attendees decide
    /// who a reminder reaches, and quietly dropping a recipient while somebody
    /// edits an unrelated field is how a person stops being told about their own event.
    public mutating func choose(calendar: CalendarFeed?) {
        calendarID = calendar?.id
        if let owner = calendar?.familyMemberID { attendees.insert(owner) }
    }

    /// Moving an event to another day means moving it, not restating how long it
    /// lasts: the end follows the start by the gap the two already had.
    public mutating func startChangedTimed(from old: String) {
        let gap = Self.minutes(from: old, to: timedEnd)
        timedEnd = Self.add(minutes: gap.map { $0 >= 0 ? $0 : 60 } ?? 60, to: timedStart)
    }

    public mutating func startChangedAllDay(from old: String) {
        var span = 0
        if let a = DayString.date(old, calendar: Self.utc), let b = DayString.date(dayEnd, calendar: Self.utc) {
            let days = Self.utc.dateComponents([.day], from: a, to: b).day ?? 0
            if days >= 0 { span = days }
        }
        if let s = DayString.date(dayStart, calendar: Self.utc), let e = Self.utc.date(byAdding: .day, value: span, to: s) {
            dayEnd = DayString.string(e, calendar: Self.utc)
        }
    }

    // MARK: Wall-clock arithmetic (no zone: the server owns zones)

    public static let utc: Calendar = { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "UTC")!; return c }()

    public static func wall(_ s: String) -> Date? {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = utc.timeZone
        f.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return f.date(from: s)
    }

    public static func wallString(_ d: Date) -> String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = utc.timeZone
        f.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return f.string(from: d)
    }

    static func minutes(from a: String, to b: String) -> Int? {
        guard let x = wall(a), let y = wall(b) else { return nil }
        return Int(y.timeIntervalSince(x) / 60)
    }

    static func add(minutes: Int, to s: String) -> String {
        guard let d = wall(s) else { return s }
        return wallString(d.addingTimeInterval(Double(minutes) * 60))
    }

    // MARK: The request

    func body(editing series: EventSeries?, timesUntouched untouched: Bool) throws -> PatchBody {
        func text(_ s: String) -> String? { let t = s.trimmingCharacters(in: .whitespacesAndNewlines); return t.isEmpty ? nil : t }
        var body = try PatchBody()
            .set("title", Patch.value(title.trimmingCharacters(in: .whitespaces)))
            .set("description", Patch.clearing(text(description)))
            .set("location", Patch.clearing(text(location)))
            .set("all_day", Patch.value(allDay))
        // An edit that did not touch the times says nothing about them, so the API's
        // "leave alone" path applies. Sending them regardless is what made a
        // title-only edit move an occurrence: `Only this event` wrote the form's
        // date onto the override, and `This and future` started the tail series
        // there instead of at the split, drawing the overlap twice.
        if !(series != nil && untouched) {
            body = try body.set("start", Patch.value(start)).set("end", Patch.value(end))
        }
        switch Recurrence.change(choice: repeatChoice, compiled: compiledRule, stored: series?.rrule, isEditing: series != nil) {
        case .leave: break
        case .set(let rule): body = try body.set("rrule", Patch.clearing(rule))
        }
        body = try body.set("notify_minutes_before", Patch.clearing(notifyMinutes))
            .set("attendee_ids", Patch.value(attendees.sorted()))
        if let calendarID { body = try body.set("calendar_id", Patch.value(calendarID)) }
        return body
    }
}
