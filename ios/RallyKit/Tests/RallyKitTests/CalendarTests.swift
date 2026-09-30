import Foundation
import Testing
@testable import RallyKit

private let chicago = TimeZone(identifier: "America/Chicago")!
private let math = CalendarMath(timeZone: chicago)
private func day(_ s: String) -> Date { math.date(s)! }

private func occ(_ title: String, start: String, end: String? = nil, startLabel: String = "9:00 AM", endLabel: String = "10:00 AM",
                 allDay: Bool = false, dates: [String]? = nil, startDate: String = "2026-09-30", endDate: String? = nil,
                 color: String? = "#315277", editable: Bool = true) -> Occurrence {
    let e = endDate ?? startDate
    let json = """
    {"uid":"u-\(title)","source":"native","title":"\(title)","all_day":\(allDay),"start":"2026-09-30T14:00:00Z","end":"2026-09-30T15:00:00Z",
     "start_date":"\(startDate)","end_date":"\(e)","time_label":"\(startLabel)","end_time_label":"\(endLabel)",
     "start_form":"\(startDate)T09:00","end_form":"\(e)T10:00","dates":\((dates ?? [startDate]).map { "\"\($0)\"" }.joined(separator: ",").wrapped),
     "calendar_id":1,"calendar_label":"Mom","member":"Mom","member_color":\(color.map { "\"\($0)\"" } ?? "null"),
     "attendees":["Mom"],"event_id":1,"occurrence_date":"\(startDate)","recurring":false,"editable":\(editable)}
    """
    return try! JSONDecoder.rally.decode(Occurrence.self, from: Data(json.utf8))
}
private extension String { var wrapped: String { "[\(self)]" } }

struct CalendarWindowTests {
    @Test func weekStartsSunday() {
        // 2026-09-30 is a Wednesday; its week starts Sunday the 27th.
        #expect(math.iso(math.startOfWeek(day("2026-09-30"))) == "2026-09-27")
        #expect(math.iso(math.startOfWeek(day("2026-09-27"))) == "2026-09-27")
        #expect(math.iso(math.startOfWeek(day("2026-10-03"))) == "2026-09-27")
    }

    @Test func monthGridIs42DaysFromTheSundayOnOrBeforeThe1st() {
        let w = math.window(mode: .calendar, range: .month, anchor: day("2026-09-15"))
        #expect(math.iso(w.start) == "2026-08-30") // Sep 1 2026 is a Tuesday
        #expect(math.dayCount(mode: .calendar, range: .month, anchor: day("2026-09-15")) == 42)
    }

    @Test func monthListCarriesOnlyTheMonth() {
        let w = math.window(mode: .agenda, range: .month, anchor: day("2026-09-15"))
        #expect(math.iso(w.start) == "2026-09-01" && math.iso(w.end) == "2026-10-01")
    }

    @Test func otherWindows() {
        #expect(math.dayCount(mode: .calendar, range: .day, anchor: day("2026-09-30")) == 1)
        #expect(math.dayCount(mode: .agenda, range: .week, anchor: day("2026-09-30")) == 7)
        let r = math.window(mode: .agenda, range: .rolling30, anchor: day("2026-09-30"))
        #expect(math.iso(r.end) == "2026-10-30")
    }

    @Test func shiftMovesByTheSelectedRange() {
        #expect(math.iso(math.shifted(day("2026-09-30"), range: .day, by: 1)) == "2026-10-01")
        #expect(math.iso(math.shifted(day("2026-09-30"), range: .week, by: -1)) == "2026-09-20")
        #expect(math.iso(math.shifted(day("2026-09-30"), range: .month, by: 1)) == "2026-10-01")
        #expect(math.iso(math.shifted(day("2026-09-30"), range: .rolling30, by: 1)) == "2026-10-30")
    }

    @Test func rollingIsOfferedInAgendaOnly() {
        #expect(!CalendarRange.offered(in: .calendar).contains(.rolling30))
        #expect(CalendarRange.offered(in: .agenda).contains(.rolling30))
    }

    @Test func addEventOpensOnTheDayYouAreLookingAt() {
        let now = day("2026-09-30")
        #expect(math.defaultEventDate(mode: .calendar, range: .day, anchor: day("2026-10-22"), now: now) == "2026-10-22")
        #expect(math.defaultEventDate(mode: .calendar, range: .month, anchor: day("2026-09-01"), now: now) == "2026-09-30")
        #expect(math.defaultEventDate(mode: .calendar, range: .month, anchor: day("2026-11-01"), now: now) == "2026-11-01")
        #expect(math.defaultEventDate(mode: .agenda, range: .week, anchor: day("2026-09-27"), now: now) == "2026-09-30")
        #expect(math.defaultEventDate(mode: .agenda, range: .week, anchor: day("2026-10-11"), now: now) == "2026-10-11")
    }

    @Test func labels() {
        #expect(math.rangeLabel(range: .week, anchor: day("2026-08-12")).contains("– 15, 2026"))
        #expect(math.rangeLabel(range: .week, anchor: day("2026-09-30")).contains("October"), "a straddling week repeats the month")
    }

    @Test func monthCellsShowThreeAndCountTheRest() {
        let many = (1...5).map { occ("E\($0)", start: "x") }
        let cells = math.monthCells(anchor: day("2026-09-15"), occurrences: many, now: day("2026-09-30"))
        #expect(cells.count == 6 && cells.allSatisfy { $0.count == 7 })
        let cell = cells.flatMap { $0 }.first { $0.iso == "2026-09-30" }!
        #expect(cell.shown.count == 3 && cell.hidden == 2 && cell.isToday && !cell.isOutside)
        #expect(cells[0][0].isOutside && cells[0][0].iso == "2026-08-30")
    }
}

struct TimeGridGeometryTests {
    @Test func labelsToMinutes() {
        #expect(CalendarMath.minutes(fromLabel: "5:30 PM") == 1050)
        #expect(CalendarMath.minutes(fromLabel: "12:00 AM") == 0)
        #expect(CalendarMath.minutes(fromLabel: "12:15 PM") == 735)
        #expect(CalendarMath.minutes(fromLabel: "nonsense") == nil)
    }

    @Test func crossingMidnightDrawsTwice() {
        let o = occ("Shift", start: "x", startLabel: "9:00 PM", endLabel: "1:00 AM", dates: ["2026-09-30", "2026-10-01"], endDate: "2026-10-01")
        let first = CalendarMath.timedSpan(o, on: "2026-09-30")!
        #expect(first.start == 1260 && first.end == 1440)
        let second = CalendarMath.timedSpan(o, on: "2026-10-01")!
        #expect(second.start == 0 && second.end == 60)
    }

    @Test func anEventEndingAtMidnightDrawsNoSliverOnTheNextDay() {
        let o = occ("Late", start: "x", startLabel: "9:00 PM", endLabel: "12:00 AM", dates: ["2026-09-30", "2026-10-01"], endDate: "2026-10-01")
        #expect(CalendarMath.timedSpan(o, on: "2026-10-01") == nil)
    }

    @Test func aBodyIsNeverShorterThanThirtyMinutes() {
        let p = CalendarMath.paint((start: 600, end: 660))
        #expect(p.minutes == 60 && !p.short)
        let q = CalendarMath.paint((start: 600, end: 615))
        #expect(q.minutes == 30 && q.tabMinutes == 15 && q.short)
    }

    @Test func roundingHappensBeforeTheShortTest() {
        let p = CalendarMath.paint((start: 600, end: 628)) // 28 minutes rounds to 30
        #expect(!p.short && p.minutes == 30)
        let tiny = CalendarMath.paint((start: 600, end: 601))
        #expect(tiny.tabMinutes == 5 && tiny.short, "a one-minute event still gets a tab")
    }

    @Test func startsSnapToFive() {
        #expect(CalendarMath.paint((start: 602, end: 700)).start == 600)
        #expect(CalendarMath.paint((start: 603, end: 700)).start == 605)
    }

    private func block(_ title: String, _ start: Int, _ end: Int) -> PlacedBlock {
        PlacedBlock(occurrence: occ(title, start: "x"), iso: "2026-09-30", paint: CalendarMath.paint((start, end)))
    }

    @Test func overlappingEventsShareTheColumnAndTouchingOnesDoNot() {
        let placed = CalendarMath.layout([block("A", 600, 660), block("B", 630, 690), block("C", 690, 750)])
        let a = placed.first { $0.occurrence.title == "A" }!, b = placed.first { $0.occurrence.title == "B" }!
        let c = placed.first { $0.occurrence.title == "C" }!
        #expect(a.columns == 2 && b.columns == 2 && a.column != b.column)
        #expect(c.columns == 1 && c.column == 0, "an event starting as another ends keeps the full width")
    }

    @Test func packingUsesThePaintedExtent() {
        // 3:45–4:00 paints to 4:15, so it and a 4:00 event sit side by side.
        let placed = CalendarMath.layout([block("Short", 945, 960), block("Four", 960, 1020)])
        #expect(placed.allSatisfy { $0.columns == 2 })
    }

    @Test func clustersAreTransitive() {
        let placed = CalendarMath.layout([block("A", 600, 720), block("B", 660, 780), block("C", 740, 800)])
        #expect(placed.allSatisfy { $0.columns >= 2 })
        #expect(placed.first { $0.occurrence.title == "C" }!.column == placed.first { $0.occurrence.title == "A" }!.column,
                "C reuses A's column once A has ended")
    }

    @Test func theGridOpensAnHourBeforeNowAndNeverAboveMidnight() {
        #expect(CalendarMath.openingScrollMinutes(nowMinutes: 16 * 60 + 30) == 15 * 60)
        #expect(CalendarMath.openingScrollMinutes(nowMinutes: 30) == 0)
        #expect(CalendarMath.openingScrollMinutes(nowMinutes: 0) == 0)
    }

    @Test func allDayBarsSpanTheirColumns() {
        let trip = occ("Trip", start: "x", allDay: true, dates: ["2026-10-01", "2026-10-02", "2026-10-03"], startDate: "2026-10-01", endDate: "2026-10-03")
        let days = (0..<7).map { math.addDays(day("2026-09-27"), $0) }
        let bars = math.allDayBars(days: days, in: [trip])
        #expect(bars.count == 1 && bars[0].first == 4 && bars[0].last == 6)
    }

    @Test func compactTimes() {
        #expect(CalendarMath.compactTime("9:00 AM") == "9a")
        #expect(CalendarMath.compactTime("5:30 PM") == "5:30p")
    }
}

struct RecurrenceTests {
    // 2026-09-30 is a Wednesday.
    @Test func theFixedChoices() {
        #expect(Recurrence.rrule(for: .daily, startISO: "2026-09-30") == "FREQ=DAILY")
        #expect(Recurrence.rrule(for: .weekly, startISO: "2026-09-30") == "FREQ=WEEKLY;BYDAY=WE")
        #expect(Recurrence.rrule(for: .biweekly, startISO: "2026-09-30T09:00") == "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE")
        #expect(Recurrence.rrule(for: .monthly, startISO: "2026-09-30") == "FREQ=MONTHLY;BYMONTHDAY=30")
        #expect(Recurrence.rrule(for: .yearly, startISO: "2026-09-30") == "FREQ=YEARLY")
        #expect(Recurrence.rrule(for: .none, startISO: "2026-09-30") == nil)
    }

    @Test func customWeeklyIsWrittenInWeekOrderNotClickOrder() {
        var c = CustomRepeat(); c.freq = .weekly; c.interval = 2; c.weekdays = ["TH", "TU", "SU"]
        #expect(c.cadence() == "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU,TU,TH")
    }

    @Test func aWeeklyRuleWithNoDaysIsNotARule() {
        var c = CustomRepeat(); c.freq = .weekly; c.weekdays = []
        #expect(c.cadence() == nil)
    }

    @Test func everyWeekdayPinsTheIntervalToOne() {
        var c = CustomRepeat(); c.freq = .daily; c.interval = 3; c.weekdaysOnly = true
        #expect(c.cadence() == "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR")
    }

    @Test func monthlyByDateByPositionAndLastDay() {
        var c = CustomRepeat(); c.freq = .monthly
        c.monthlyMode = .day; c.monthDay = -1
        #expect(c.cadence() == "FREQ=MONTHLY;BYMONTHDAY=-1")
        c.monthlyMode = .weekday; c.ordinal = "-1"; c.weekday = "FR"; c.interval = 3
        #expect(c.cadence() == "FREQ=MONTHLY;INTERVAL=3;BYDAY=-1FR")
    }

    @Test func endsBoundAnyRepeatingEvent() {
        let until = Recurrence.compile(.weekly, custom: CustomRepeat(), ends: .until("2026-12-18"), startISO: "2026-09-30", allDay: false)
        #expect(until == "FREQ=WEEKLY;BYDAY=WE;UNTIL=20261218T235959Z")
        let allDay = Recurrence.compile(.daily, custom: CustomRepeat(), ends: .until("2026-12-18"), startISO: "2026-09-30", allDay: true)
        #expect(allDay == "FREQ=DAILY;UNTIL=20261218")
        #expect(Recurrence.compile(.daily, custom: CustomRepeat(), ends: .count(10), startISO: "2026-09-30", allDay: false) == "FREQ=DAILY;COUNT=10")
        #expect(Recurrence.compile(.none, custom: CustomRepeat(), ends: .count(10), startISO: "2026-09-30", allDay: false) == nil)
    }

    @Test func aStoredRuleIsMatchedByComparisonNeverByPrefix() {
        #expect(Recurrence.choice(from: "FREQ=WEEKLY;BYDAY=WE", startISO: "2026-09-30") == .weekly)
        #expect(Recurrence.choice(from: "FREQ=WEEKLY;BYDAY=WE;UNTIL=20261218T235959Z", startISO: "2026-09-30") == .weekly)
        #expect(Recurrence.choice(from: "FREQ=WEEKLY;BYDAY=TU,TH", startISO: "2026-09-30") == .custom)
        #expect(Recurrence.choice(from: "FREQ=WEEKLY;BYDAY=TH", startISO: "2026-09-30") == .custom, "weekly on a different day is not this day's weekly")
        #expect(Recurrence.choice(from: nil, startISO: "2026-09-30") == .none)
    }

    @Test func aRuleRicherThanTheControlsIsPreservedNotNarrowed() {
        #expect(Recurrence.choice(from: "FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", startISO: "2026-09-30") == .other)
        #expect(Recurrence.choice(from: "FREQ=MONTHLY;BYSETPOS=-1;BYDAY=MO,TU,WE,TH,FR", startISO: "2026-09-30") == .other)
        #expect(Recurrence.choice(from: "FREQ=DAILY;BYDAY=MO,WE", startISO: "2026-09-30") == .other)
    }

    @Test func customRoundTrips() {
        for rule in ["FREQ=DAILY;INTERVAL=3", "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH",
                     "FREQ=MONTHLY;BYMONTHDAY=-1", "FREQ=MONTHLY;INTERVAL=2;BYDAY=-1FR", "FREQ=YEARLY;INTERVAL=2"] {
            let parsed = Recurrence.customFromRrule(rule)
            #expect(parsed?.cadence() == rule, "\(rule) should round-trip")
        }
    }

    @Test func endsAreReadBack() {
        #expect(Recurrence.ends(from: "FREQ=DAILY;UNTIL=20261218T235959Z") == .until("2026-12-18"))
        #expect(Recurrence.ends(from: "FREQ=DAILY;UNTIL=20261218") == .until("2026-12-18"))
        #expect(Recurrence.ends(from: "FREQ=DAILY;COUNT=10") == .count(10))
        #expect(Recurrence.ends(from: "FREQ=DAILY") == .never)
    }

    @Test func recurrenceIsOnlySentWhenItChanged() {
        #expect(Recurrence.change(choice: .weekly, compiled: "FREQ=WEEKLY;BYDAY=WE", stored: "BYDAY=WE;FREQ=WEEKLY", isEditing: true) == .leave)
        #expect(Recurrence.change(choice: .daily, compiled: "FREQ=DAILY", stored: "FREQ=WEEKLY;BYDAY=WE", isEditing: true) == .set("FREQ=DAILY"))
        #expect(Recurrence.change(choice: .none, compiled: nil, stored: "FREQ=DAILY", isEditing: true) == .set(nil))
        #expect(Recurrence.change(choice: .other, compiled: nil, stored: "FREQ=YEARLY;BYMONTH=3", isEditing: true) == .leave)
        #expect(Recurrence.change(choice: .none, compiled: nil, stored: nil, isEditing: false) == .set(nil))
    }
}

struct EventDraftTests {
    let series = try! JSONDecoder.rally.decode(EventSeries.self, from: Data("""
    {"id":7,"calendar_id":1,"title":"Standup","description":null,"location":null,"all_day":false,"start":"2026-09-01T09:00",
     "end":"2026-09-01T09:30","rrule":"FREQ=WEEKLY;BYDAY=TU","notify_minutes_before":15,"attendee_ids":[1,2],"overrides":[]}
    """.utf8))

    private func json(_ body: PatchBody) -> [String: Any] {
        try! JSONSerialization.jsonObject(with: JSONEncoder().encode(body)) as! [String: Any]
    }

    @Test func opensOnTheOccurrenceNotTheSeries() {
        let o = occ("Standup", start: "x", startDate: "2026-09-29") // startForm 2026-09-29T09:00
        let draft = EventDraft(series: series, occurrence: o)
        #expect(draft.timedStart == "2026-09-29T09:00", "the series' own date would be wrong for every occurrence after the first")
        #expect(draft.repeatChoice == .weekly && draft.attendees == [1, 2] && draft.notifyMinutes == 15)
    }

    @Test func aTitleOnlyEditSaysNothingAboutTimesOrRecurrence() throws {
        let o = occ("Standup", start: "x", startDate: "2026-09-29")
        var draft = EventDraft(series: series, occurrence: o)
        draft.title = "Daily standup"
        #expect(draft.timesUntouched)
        let body = json(try draft.body(editing: series, timesUntouched: draft.timesUntouched))
        #expect(body["start"] == nil && body["end"] == nil, "sending them is what made a rename move an occurrence")
        #expect(body["rrule"] == nil)
        #expect(body["title"] as? String == "Daily standup")
    }

    @Test func movingTheTimeSendsIt() throws {
        let o = occ("Standup", start: "x", startDate: "2026-09-29")
        var draft = EventDraft(series: series, occurrence: o)
        draft.timedStart = "2026-09-29T10:00"; draft.timedEnd = "2026-09-29T10:30"
        #expect(!draft.timesUntouched)
        let body = json(try draft.body(editing: series, timesUntouched: draft.timesUntouched))
        #expect(body["start"] as? String == "2026-09-29T10:00" && body["end"] as? String == "2026-09-29T10:30")
    }

    @Test func clearingRecurrenceIsAnExplicitNull() throws {
        let o = occ("Standup", start: "x", startDate: "2026-09-29")
        var draft = EventDraft(series: series, occurrence: o)
        draft.repeatChoice = .none
        #expect(json(try draft.body(editing: series, timesUntouched: true))["rrule"] is NSNull)
    }

    @Test func addingSendsEverythingAndDefaultsToAnHour() throws {
        var draft = EventDraft(day: "2026-10-05")
        draft.title = " Dentist "
        let body = json(try draft.body(editing: nil, timesUntouched: false))
        #expect(body["start"] as? String == "2026-10-05T09:00" && body["end"] as? String == "2026-10-05T10:00")
        #expect(body["title"] as? String == "Dentist" && body["calendar_id"] == nil)
        #expect(body["description"] is NSNull && body["attendee_ids"] as? [Int] == [])
    }

    @Test func anAllDayEventSendsDatesAndItsEndIsInclusive() throws {
        var draft = EventDraft(day: "2026-10-05"); draft.title = "Trip"; draft.allDay = true; draft.dayEnd = "2026-10-07"
        let body = json(try draft.body(editing: nil, timesUntouched: false))
        #expect(body["start"] as? String == "2026-10-05" && body["end"] as? String == "2026-10-07" && body["all_day"] as? Bool == true)
    }

    @Test func pickingACalendarChecksItsOwnerAndNeverUnchecksAnyone() {
        var draft = EventDraft(day: "2026-10-05"); draft.attendees = [2]
        let calendar = try! JSONDecoder.rally.decode(CalendarFeed.self, from: Data(#"{"id":4,"label":"Emma","family_member_id":3,"cal_type":"native"}"#.utf8))
        draft.choose(calendar: calendar)
        #expect(draft.attendees == [2, 3] && draft.calendarID == 4)
        draft.choose(calendar: try! JSONDecoder.rally.decode(CalendarFeed.self, from: Data(#"{"id":5,"label":"Jake","family_member_id":1,"cal_type":"native"}"#.utf8)))
        #expect(draft.attendees == [1, 2, 3], "moving an event checks the new owner and leaves the previous one")
    }

    @Test func movingTheStartMovesTheEndByTheSameGap() {
        var draft = EventDraft(day: "2026-10-05")
        let old = draft.timedStart
        draft.timedStart = "2026-10-06T14:00"
        draft.startChangedTimed(from: old)
        #expect(draft.timedEnd == "2026-10-06T15:00")
        draft.timedEnd = "2026-10-06T16:30"
        let old2 = draft.timedStart
        draft.timedStart = "2026-10-07T08:00"
        draft.startChangedTimed(from: old2)
        #expect(draft.timedEnd == "2026-10-07T10:30")
    }

    @Test func allDayEndFollowsTheStart() {
        var draft = EventDraft(day: "2026-10-05"); draft.dayEnd = "2026-10-07"
        let old = draft.dayStart
        draft.dayStart = "2026-10-10"
        draft.startChangedAllDay(from: old)
        #expect(draft.dayEnd == "2026-10-12")
    }
}

struct CalendarTextTests {
    @Test func detailRowsNameTheCadenceAndWhyItIsReadOnly() {
        let o = occ("Game", start: "x", editable: false)
        let keys = CalendarText.detailRows(o).map(\.key)
        #expect(keys.contains("readonly") && !keys.contains("repeats"))
    }

    @Test func notifyResultNamesEveryOutcome() {
        let r = try! JSONDecoder().decode(NotifyResult.self, from: Data(#"{"sent":["Jon"],"skipped":["Emma"],"muted":["Jake"],"failed":[],"error":null}"#.utf8))
        #expect(r.summary == "Sent to Jon. No Pushover key for Emma. Jake turned event reminders off.")
    }
}

@MainActor
struct CalendarModelTests {
    let client = APIClient(baseURL: URL(string: "http://x")!) { _ in throw URLError(.notConnectedToInternet) }

    @Test func autoMeansDayOnAPhoneAndMonthOnAnythingWider() {
        #expect(CalendarModel(client: client, math: math, narrow: true).range == .day)
        let wide = CalendarModel(client: client, math: math, narrow: false)
        #expect(wide.range == .month && wide.mode == .calendar)
    }

    @Test func aLandingViewIsAppliedWhenReadableAndIgnoredOtherwise() {
        let m = CalendarModel(client: client, math: math, narrow: true)
        m.applyLandingView("agenda:week")
        #expect(m.mode == .agenda && m.range == .week)
        let n = CalendarModel(client: client, math: math, narrow: true)
        for bad in [nil, "auto", "garbage", "calendar:rolling30", "agenda:decade"] { n.applyLandingView(bad) }
        #expect(n.mode == .calendar && n.range == .day, "unreadable values fall through to the width rule")
    }

    @Test func switchingToCalendarFromTheRollingWindowFallsBackToMonth() {
        let m = CalendarModel(client: client, math: math, narrow: false)
        m.set(mode: .agenda); m.set(range: .rolling30)
        m.set(mode: .calendar)
        #expect(m.range == .month)
    }

    @Test func drillingIntoADayKeepsTheMode() {
        let m = CalendarModel(client: client, math: math, narrow: false)
        m.set(mode: .agenda)
        m.drill(into: "2026-10-22")
        #expect(m.range == .day && m.mode == .agenda && math.iso(m.anchor) == "2026-10-22")
    }

    @Test func aDateRolloverMakesDayFollowTheDateButWeekStaysPut() {
        let now = day("2026-09-30")
        let m = CalendarModel(client: client, math: math, narrow: true, now: now)
        #expect(!m.tick(now: now))
        #expect(m.tick(now: day("2026-10-01")) && math.iso(m.anchor) == "2026-10-01")
        let w = CalendarModel(client: client, math: math, narrow: false, now: now)
        w.set(range: .week)
        let before = w.anchor
        #expect(w.tick(now: day("2026-10-01")) && w.anchor == before)
    }
}
