import Foundation

public enum CalendarMode: String, CaseIterable, Sendable, Identifiable {
    case calendar, agenda
    public var id: String { rawValue }
    public var title: String { self == .calendar ? "Calendar" : "Agenda" }
}

/// `View` picks the renderer and `Range` picks the slice of time. They used to
/// be one dropdown, which is why Day and Week both rendered agenda lists: there
/// was no way to say "a week, drawn as a calendar".
public enum CalendarRange: String, CaseIterable, Sendable, Identifiable {
    case day, week, month, rolling30
    public var id: String { rawValue }
    public var title: String {
        switch self { case .day: "Day"; case .week: "Week"; case .month: "Month"; case .rolling30: "Next 30 days" }
    }
    /// A grid of 30 arbitrary days starting on a Wednesday is not a calendar, so
    /// the rolling window is offered in Agenda only.
    public static func offered(in mode: CalendarMode) -> [CalendarRange] {
        mode == .calendar ? [.day, .week, .month] : allCases
    }
}

public struct MonthCell: Identifiable, Equatable, Sendable {
    public let iso: String
    public let dayNumber: Int
    public let isOutside: Bool
    public let isToday: Bool
    public let shown: [Occurrence]
    public let hidden: Int
    public var id: String { iso }
}

/// A block on the time grid, placed.
public struct PlacedBlock: Identifiable, Equatable, Sendable {
    public let occurrence: Occurrence
    public let iso: String
    public let paint: Paint
    public var column = 0
    public var columns = 1
    public var id: String { "\(iso)|\(occurrence.id)" }
}

public struct Paint: Equatable, Sendable {
    public let start: Int      // minutes from midnight, snapped to 5
    public let minutes: Int    // the body: at least 30, proportional above
    public let tabMinutes: Int // the event's own rounded length
    public let short: Bool
}

/// The calendar's date arithmetic, windows and grid geometry — pure, so the rules
/// the web page keeps in its script are pinned by tests here.
public struct CalendarMath: Sendable {
    public static let agendaDays = 30
    public static let minutesPerDay = 24 * 60
    /// Starts and heights snap to five minutes, the resolution a calendar is read at.
    public static let snap = 5
    /// `--timegrid-hour` is 88pt so that 30 minutes is exactly 44pt: the shortest
    /// body and the hit-area floor are pinned to each other.
    public static let minBodyMinutes = 30
    public static let minTabMinutes = 5

    public let calendar: Calendar

    /// The week starts Sunday, which is what the family reads a calendar as.
    public init(timeZone: TimeZone) {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = timeZone
        c.firstWeekday = 1
        self.calendar = c
    }

    public init(calendar: Calendar) {
        var c = calendar; c.firstWeekday = 1; self.calendar = c
    }

    public func iso(_ date: Date) -> String { DayString.string(date, calendar: calendar) }
    public func date(_ iso: String) -> Date? { DayString.date(iso, calendar: calendar) }
    public func startOfDay(_ d: Date) -> Date { calendar.startOfDay(for: d) }
    public func addDays(_ d: Date, _ n: Int) -> Date { calendar.date(byAdding: .day, value: n, to: d) ?? d }
    public func startOfWeek(_ d: Date) -> Date { addDays(startOfDay(d), -(calendar.component(.weekday, from: d) - 1)) }
    public func startOfMonth(_ d: Date) -> Date { calendar.date(from: calendar.dateComponents([.year, .month], from: d)) ?? d }
    /// The Sunday on or before the first of the month, so the grid starts on a full week.
    public func gridStart(_ d: Date) -> Date { startOfWeek(startOfMonth(d)) }

    public func today(now: Date = .now) -> String { iso(now) }
    public func nowMinutes(now: Date = .now) -> Int {
        calendar.component(.hour, from: now) * 60 + calendar.component(.minute, from: now)
    }

    // MARK: Windows

    /// The window every renderer and the fetch agree on. `end` is exclusive. Month
    /// is the one range whose window depends on the mode: a grid has to square
    /// itself off, so it draws the 42-day block containing the month, while a list
    /// has no reason to carry the leading and trailing days that block adds.
    public func window(mode: CalendarMode, range: CalendarRange, anchor: Date) -> (start: Date, end: Date) {
        switch range {
        case .day: let s = startOfDay(anchor); return (s, addDays(s, 1))
        case .week: let s = startOfWeek(anchor); return (s, addDays(s, 7))
        case .rolling30: let s = startOfDay(anchor); return (s, addDays(s, Self.agendaDays))
        case .month:
            if mode == .calendar { let s = gridStart(anchor); return (s, addDays(s, 42)) }
            let s = startOfMonth(anchor)
            return (s, calendar.date(byAdding: .month, value: 1, to: s) ?? s)
        }
    }

    public func dayCount(mode: CalendarMode, range: CalendarRange, anchor: Date) -> Int {
        let w = window(mode: mode, range: range, anchor: anchor)
        return calendar.dateComponents([.day], from: startOfDay(w.start), to: startOfDay(w.end)).day ?? 0
    }

    public func days(mode: CalendarMode, range: CalendarRange, anchor: Date) -> [Date] {
        let w = window(mode: mode, range: range, anchor: anchor)
        return (0..<dayCount(mode: mode, range: range, anchor: anchor)).map { addDays(w.start, $0) }
    }

    /// Prev/Next move by the selected *range*, in both modes.
    public func shifted(_ anchor: Date, range: CalendarRange, by direction: Int) -> Date {
        switch range {
        case .day: return addDays(startOfDay(anchor), direction)
        case .week: return addDays(startOfWeek(anchor), direction * 7)
        case .rolling30: return addDays(startOfDay(anchor), direction * Self.agendaDays)
        case .month: return calendar.date(byAdding: .month, value: direction, to: startOfMonth(anchor)) ?? anchor
        }
    }

    /// The start of the slice of `range` that contains `base`.
    public func anchor(for range: CalendarRange, containing base: Date) -> Date {
        switch range {
        case .month: startOfMonth(base)
        case .week: startOfWeek(base)
        default: startOfDay(base)
        }
    }

    /// Add Event opens on the day you are looking at: reading Saturday the 22nd
    /// and adding an event means adding it to the 22nd. Where the slice is wider
    /// than a day, today is still the obvious default as long as it is in the
    /// slice; otherwise the slice's first day is.
    public func defaultEventDate(mode: CalendarMode, range: CalendarRange, anchor: Date, now: Date = .now) -> String {
        if range == .day { return iso(startOfDay(anchor)) }
        if range == .month {
            // Measured against the anchored month, not the 42-cell grid: being
            // handed August 31st while reading September is the same wrong answer.
            let sameMonth = calendar.isDate(now, equalTo: anchor, toGranularity: .month)
            return sameMonth ? today(now: now) : iso(startOfMonth(anchor))
        }
        let w = window(mode: mode, range: range, anchor: anchor)
        let t = startOfDay(now)
        return (t >= w.start && t < w.end) ? today(now: now) : iso(startOfDay(w.start))
    }

    // MARK: Labels

    private func format(_ date: Date, _ template: String) -> String {
        let f = DateFormatter()
        f.timeZone = calendar.timeZone
        f.setLocalizedDateFormatFromTemplate(template)
        return f.string(from: date)
    }

    public func rangeLabel(range: CalendarRange, anchor: Date) -> String {
        switch range {
        case .rolling30:
            let end = addDays(startOfDay(anchor), Self.agendaDays - 1)
            return "\(format(anchor, "MMMMd")) – \(format(end, "MMMMdy"))"
        case .day:
            return format(startOfDay(anchor), "EEEEMMMMdy")
        case .week:
            let s = startOfWeek(anchor), e = addDays(s, 6)
            // One month reads "August 9 – 15, 2026"; a week that straddles repeats the month.
            let right = calendar.component(.month, from: s) == calendar.component(.month, from: e)
                ? "\(calendar.component(.day, from: e)), \(calendar.component(.year, from: e))" : format(e, "MMMMdy")
            return "\(format(s, "MMMMd")) – \(right)"
        case .month:
            return format(anchor, "MMMMy")
        }
    }

    public func dayHeading(_ date: Date) -> String { format(date, "EEEEMMMMd") }
    public func weekdaySymbol(_ date: Date) -> String {
        calendar.shortWeekdaySymbols[calendar.component(.weekday, from: date) - 1]
    }
    public func dayNumber(_ date: Date) -> Int { calendar.component(.day, from: date) }

    // MARK: Occurrences

    public func occurrences(on iso: String, in all: [Occurrence]) -> [Occurrence] { all.filter { $0.dates.contains(iso) } }

    /// "9:30 AM Standup" truncates to "9:30 AM Sta…" in a month cell: the time
    /// survives and the title, which identifies the event, does not. The compact
    /// form buys back the characters that matter.
    public static func compactTime(_ label: String) -> String {
        label.replacingOccurrences(of: ":00", with: "").replacingOccurrences(of: " AM", with: "a").replacingOccurrences(of: " PM", with: "p")
    }

    /// The 6×7 month grid. A cell carries at most three events and says how many
    /// more there are — the day number opens that day.
    public func monthCells(anchor: Date, occurrences all: [Occurrence], now: Date = .now) -> [[MonthCell]] {
        let start = gridStart(anchor)
        let todayISO = today(now: now)
        let month = calendar.component(.month, from: anchor)
        return (0..<6).map { week in
            (0..<7).map { dow in
                let day = addDays(start, week * 7 + dow)
                let key = iso(day)
                let events = occurrences(on: key, in: all)
                return MonthCell(iso: key, dayNumber: dayNumber(day), isOutside: calendar.component(.month, from: day) != month,
                                 isToday: key == todayISO, shown: Array(events.prefix(3)), hidden: max(0, events.count - 3))
            }
        }
    }

    // MARK: Time grid geometry

    /// "5:30 PM" → 1050. Times are read back off the server-rendered labels
    /// rather than derived from the UTC instant, because those labels are already
    /// in the family's configured zone. Doing the arithmetic on the phone would put
    /// blocks where the labels beside them disagree, for anybody travelling.
    public static func minutes(fromLabel label: String) -> Int? {
        let parts = label.trimmingCharacters(in: .whitespaces).uppercased().split(whereSeparator: { $0 == " " || $0 == ":" })
        guard parts.count == 3, let h = Int(parts[0]), let m = Int(parts[1]), parts[2] == "AM" || parts[2] == "PM" else { return nil }
        return ((h % 12) + (parts[2] == "PM" ? 12 : 0)) * 60 + m
    }

    /// The slice of one date an occurrence covers, in minutes from midnight. An
    /// event crossing midnight runs to the bottom of the first day and from the
    /// top of the next; drawing it once is how a 9 PM–1 AM shift vanishes from tomorrow.
    public static func timedSpan(_ o: Occurrence, on iso: String) -> (start: Int, end: Int)? {
        let isFirst = o.startDate == iso, isLast = o.endDate == iso
        guard let start = isFirst ? minutes(fromLabel: o.timeLabel) : 0,
              let end = isLast ? minutes(fromLabel: o.endTimeLabel) : minutesPerDay else { return nil }
        // An event ending at midnight is dated to the following day as well, where it
        // would draw a zero-height sliver at the top of a day it does not touch.
        if !isFirst && end <= 0 { return nil }
        return (start, max(end, start))
    }

    public static func snapToFive(_ minutes: Int) -> Int { Int((Double(minutes) / Double(snap)).rounded()) * snap }

    /// Everything about where a block is painted, decided once — layout and
    /// rendering both read it, so the columns are packed against the rectangle
    /// that reaches the screen. Rounding happens *before* the short test: a
    /// 28-minute event rounds to 30 and is not short.
    public static func paint(_ span: (start: Int, end: Int)) -> Paint {
        let rounded = max(minTabMinutes, snapToFive(span.end - span.start))
        return Paint(start: snapToFive(span.start), minutes: max(minBodyMinutes, rounded), tabMinutes: rounded, short: rounded < minBodyMinutes)
    }

    /// Side-by-side placement. Overlapping events cluster transitively; each takes
    /// the first column free at its start and every event in the cluster is sized
    /// 1/n. Events that merely touch are separate clusters and keep the full width.
    /// Packing is on the **painted** extent, not the true one: a 15-minute event
    /// at 3:45 paints to 4:15, so it and a 4:00 event sit side by side rather than
    /// the first covering the second's title.
    public static func layout(_ blocks: [PlacedBlock]) -> [PlacedBlock] {
        let sorted = blocks.sorted { a, b in
            if a.paint.start != b.paint.start { return a.paint.start < b.paint.start }
            let ea = a.paint.start + a.paint.minutes, eb = b.paint.start + b.paint.minutes
            if ea != eb { return ea > eb }
            return a.occurrence.title.localizedCompare(b.occurrence.title) == .orderedAscending
        }
        var out: [PlacedBlock] = []
        var cluster: [PlacedBlock] = []
        var clusterEnd = -1

        func flush() {
            var columnEnds: [Int] = []
            var placed = cluster
            for i in placed.indices {
                let start = placed[i].paint.start
                var index = columnEnds.firstIndex { $0 <= start } ?? -1
                if index == -1 { index = columnEnds.count; columnEnds.append(0) }
                columnEnds[index] = start + placed[i].paint.minutes
                placed[i].column = index
            }
            for i in placed.indices { placed[i].columns = columnEnds.count }
            out += placed
            cluster = []
        }

        for block in sorted {
            if !cluster.isEmpty && block.paint.start >= clusterEnd { flush(); clusterEnd = -1 }
            cluster.append(block)
            let end = block.paint.start + block.paint.minutes
            clusterEnd = cluster.count == 1 ? end : max(clusterEnd, end)
        }
        if !cluster.isEmpty { flush() }
        return out
    }

    /// The placed, packed timed blocks for one day.
    public func blocks(on iso: String, in all: [Occurrence]) -> [PlacedBlock] {
        let raw = occurrences(on: iso, in: all).filter { !$0.allDay }.compactMap { o -> PlacedBlock? in
            guard let span = Self.timedSpan(o, on: iso) else { return nil }
            return PlacedBlock(occurrence: o, iso: iso, paint: Self.paint(span))
        }
        return Self.layout(raw)
    }

    /// Where the grid opens: an hour before the current hour, whatever is on it.
    /// Where to open is a question about when you are asking, not about what the
    /// day contains. Hour-aligned, so the top of the viewport is always an hour
    /// label, and clamped at midnight.
    public static func openingScrollMinutes(nowMinutes: Int) -> Int { max(0, (nowMinutes / 60 - 1) * 60) }

    /// All-day bars across the displayed days: one bar across four columns for a
    /// Thu–Sun trip rather than four disconnected chips.
    public func allDayBars(days: [Date], in all: [Occurrence]) -> [(occurrence: Occurrence, first: Int, last: Int)] {
        let isos = days.map(iso)
        return all.filter(\.allDay).compactMap { o in
            let covered = isos.enumerated().filter { o.dates.contains($0.element) }.map(\.offset)
            guard let first = covered.first, let last = covered.last else { return nil }
            return (o, first, last)
        }
    }
}

public enum CalendarText {
    public static func sentenceCase(_ text: String) -> String { text.prefix(1).uppercased() + text.dropFirst() }

    public static func emptyMessage(range: CalendarRange) -> String {
        switch range {
        case .day: "Nothing scheduled on this day."
        case .week: "Nothing scheduled this week."
        case .month: "Nothing scheduled this month."
        case .rolling30: "Nothing scheduled in this stretch."
        }
    }

    /// The detail view's rows, in order, each with a stable key.
    public static func detailRows(_ o: Occurrence) -> [(key: String, label: String, value: String)] {
        let when = o.allDay
            ? (o.startDate == o.endDate ? "All day" : "All day, \(o.startDate) – \(o.endDate)")
            : "\(o.timeLabel) – \(o.endTimeLabel)"
        var rows: [(String, String, String)] = [("when", "When", "\(o.startDate) · \(when)")]
        if !o.recurrenceText.isEmpty { rows.append(("repeats", "Repeats", sentenceCase(o.recurrenceText))) }
        else if o.recurring { rows.append(("repeats", "Repeats", "Yes — Rally could not read the schedule.")) }
        rows.append(("where", "Where", o.location.isEmpty ? "—" : o.location))
        rows.append(("who", "Who", o.attendees.isEmpty ? (o.member ?? "Nobody in particular") : o.attendees.joined(separator: ", ")))
        rows.append(("calendar", "Calendar", o.calendarLabel.isEmpty ? o.source : o.calendarLabel))
        rows.append(("notes", "Notes", o.description.isEmpty ? "—" : o.description))
        if let m = o.notifyMinutesBefore, m > 0 { rows.append(("reminder", "Reminder", "\(m) minutes before")) }
        // Answering "why can't I change this?" on the screen where it is asked.
        if !o.editable { rows.append(("readonly", "Read-only", "This event lives on an external calendar Rally can only read.")) }
        return rows
    }
}
