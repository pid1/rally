import Foundation
import Observation

public extension APIClient {
    /// Every member's answers on this device, resolved, keyed by member id.
    func devicePreferences(deviceID: String) async throws -> [String: [String: String]] {
        struct Reply: Decodable, Sendable { let members: [String: [String: String]] }
        let reply: Reply = try await get(.devicePreferences, ["device_id": deviceID])
        return reply.members
    }

    /// Write one member's answers on this device. Partial: a setting left out keeps its answer.
    func saveMemberPreferences(deviceID: String, memberID: Int, _ values: [String: String]) async throws {
        try await perform(.saveMemberPreferences, ["device_id": deviceID, "member_id": String(memberID)],
                          body: PatchBody().set("values", Patch.value(values)))
    }
}

/// The calendar screen's state: what is being looked at and what was fetched.
@MainActor @Observable
public final class CalendarModel {
    public private(set) var mode: CalendarMode = .calendar
    public private(set) var range: CalendarRange = .month
    public private(set) var anchor: Date
    public private(set) var occurrences: [Occurrence] = []
    public private(set) var failures: [String] = []
    public private(set) var nativeCalendars: [CalendarFeed] = []
    public private(set) var hasLoaded = false
    public private(set) var loadFailed = false
    public var selectedMembers: Set<String> = []
    public var errorMessage: String?

    public private(set) var math: CalendarMath
    private let client: APIClient
    private var generation = 0
    private var tickDate: String

    /// `narrow` is Rally's own rule for `auto`: Day on a phone, Month on anything
    /// wider. Only the page that holds the rule can resolve `auto`, so the width is
    /// passed in by the screen rather than read here.
    public init(client: APIClient, math: CalendarMath, narrow: Bool, now: Date = .now) {
        self.client = client
        self.math = math
        self.anchor = math.startOfDay(now)
        self.tickDate = math.today(now: now)
        self.range = narrow ? .day : .month
        self.anchor = math.anchor(for: range, containing: now)
    }

    public func update(math: CalendarMath) { self.math = math }

    // MARK: Landing view

    /// Where this device is set to start for the person using it. The stored value
    /// *is* the pair the toolbar draws (`agenda:week`). `auto` — and anything
    /// unreadable — means the width rule already applied in `init`.
    public func applyLandingView(_ value: String?, now: Date = .now) {
        guard let value, value != "auto" else { return }
        let parts = value.split(separator: ":").map(String.init)
        guard parts.count == 2, let m = CalendarMode(rawValue: parts[0]), let r = CalendarRange(rawValue: parts[1]),
              CalendarRange.offered(in: m).contains(r) else { return }
        mode = m; range = r
        anchor = math.anchor(for: r, containing: now)
    }

    // MARK: Navigation (nothing here is persisted — where you *start* is a setting, where you go is not)

    public func set(mode next: CalendarMode, now: Date = .now) {
        mode = next
        // Calendar cannot draw `Next 30 days`; fall back to Month, the nearest thing a grid has.
        if next == .calendar && range == .rolling30 { range = .month; anchor = math.anchor(for: .month, containing: anchor) }
    }

    public func set(range next: CalendarRange) {
        range = next
        anchor = math.anchor(for: next, containing: anchor)
    }

    public func shift(_ direction: Int) { anchor = math.shifted(anchor, range: range, by: direction) }

    public func goToToday(now: Date = .now) { anchor = math.anchor(for: range, containing: now) }

    /// Tapping a day drills into that day. The mode is left alone — drilling in
    /// from a grid keeps you in the grid, and from a list, in the list.
    public func drill(into iso: String) {
        range = .day
        if let day = math.date(iso) { anchor = math.startOfDay(day) }
    }

    public var window: (start: Date, end: Date) { math.window(mode: mode, range: range, anchor: anchor) }
    public var rangeLabel: String { math.rangeLabel(range: range, anchor: anchor) }
    public var marksToday: Bool { range == .day && math.iso(anchor) == math.today() }

    /// When the date rolls over, everything that says which day it is goes stale at
    /// once. Day follows the date (a wall tablet nobody touches must show today at
    /// breakfast); Week and Month name a span and keep the one they are on.
    /// Returns whether a reload is needed.
    @discardableResult
    public func tick(now: Date = .now) -> Bool {
        let today = math.today(now: now)
        guard today != tickDate else { return false }
        tickDate = today
        if range == .day { anchor = math.startOfDay(now) }
        return true
    }

    // MARK: Loading

    public func toggleMember(_ name: String) {
        if selectedMembers.contains(name) { selectedMembers.remove(name) } else { selectedMembers.insert(name) }
    }

    public func load() async {
        generation += 1
        let mine = generation
        let w = window
        do {
            let page = try await client.occurrences(start: math.iso(w.start), end: math.iso(w.end), members: selectedMembers)
            // A slow answer for a window the person has already left must not replace the new one.
            guard mine == generation else { return }
            occurrences = page.occurrences; failures = page.failures; loadFailed = false
        } catch {
            guard mine == generation else { return }
            loadFailed = true
        }
        hasLoaded = true
    }

    public func loadNativeCalendars() async {
        nativeCalendars = (try? await client.nativeCalendars()) ?? nativeCalendars
    }

    // MARK: Events

    public func series(for occurrence: Occurrence) async -> EventSeries? {
        guard let id = occurrence.eventID else { return nil }
        do { return try await client.event(id: id) }
        catch { errorMessage = "Could not load that event."; return nil }
    }

    @discardableResult
    public func create(_ draft: EventDraft) async -> Bool {
        do { _ = try await client.createEvent(draft); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    public func update(_ draft: EventDraft, series: EventSeries, scope: EditScope, occurrenceDate: String?) async -> Bool {
        do { _ = try await client.updateEvent(id: series.id, draft, editing: series, scope: scope, occurrenceDate: occurrenceDate); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    public func delete(series: EventSeries, scope: EditScope, occurrenceDate: String?) async -> Bool {
        do { try await client.deleteEvent(id: series.id, scope: scope, occurrenceDate: occurrenceDate); await load(); return true }
        catch { errorMessage = error.localizedDescription; return false }
    }
}
