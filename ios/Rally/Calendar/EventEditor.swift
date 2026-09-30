import RallyKit
import SwiftUI

/// Add or edit an event. Wall times are shown exactly as stored (the pickers run in
/// UTC over the wall-clock value) — the app does no timezone arithmetic.
struct EventEditor: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var app
    let model: CalendarModel
    let target: EventEditorTarget

    @State private var draft = EventDraft()
    @State private var series: EventSeries?
    @State private var occurrence: Occurrence?
    @State private var saving = false
    /// `onAppear` fires again when a pushed picker pops back, which reset the draft and
    /// wiped the title somebody had just typed. Setup happens once.
    @State private var didSetUp = false
    @State private var scopePrompt: ScopeAction?
    @State private var confirmDelete = false
    @State private var readBack = ""
    @State private var storedRuleText = ""
    @FocusState private var titleFocused: Bool

    enum ScopeAction: Identifiable { case save, delete; var id: Int { self == .save ? 0 : 1 } }

    private static let utc = TimeZone(identifier: "UTC")!
    private var isRecurringEdit: Bool { series?.rrule != nil }
    private var owners: [(member: FamilyMember?, calendars: [CalendarFeed])] {
        var groups: [(FamilyMember?, [CalendarFeed])] = app.members.compactMap { m in
            let owned = model.nativeCalendars.filter { $0.familyMemberID == m.id }
            return owned.isEmpty ? nil : (m, owned)
        }
        // A calendar whose owner is no longer a family member would otherwise vanish
        // from the picker while still holding events.
        let orphans = model.nativeCalendars.filter { c in !app.members.contains { $0.id == c.familyMemberID } }
        if !orphans.isEmpty { groups.append((nil, orphans)) }
        return groups
    }
    private var needsCalendarChoice: Bool { model.nativeCalendars.count > 1 }
    private var canSave: Bool {
        !draft.title.trimmingCharacters(in: .whitespaces).isEmpty && !draft.start.isEmpty && !saving
            && (!needsCalendarChoice || draft.calendarID != nil)
            && !(draft.repeatChoice == .custom && draft.compiledRule == nil)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Title", text: $draft.title).focused($titleFocused).accessibilityIdentifier("event-title")
                    TextField("Location", text: $draft.location)
                    TextField("Description", text: $draft.description, axis: .vertical)
                }
                calendarSection
                timeSection
                repeatSection
                reminderSection
                attendeesSection
                deleteSection
            }
            .navigationTitle(series == nil ? "Add Event" : "Edit Event")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { attemptSave() }
                        .disabled(!canSave).accessibilityIdentifier("event-save")
                }
            }
            .onAppear(perform: setUp)
            .task(id: draft.compiledRule ?? draft.repeatChoice.rawValue) { await refreshReadBack() }
            .confirmationDialog(scopePrompt == .delete ? "Delete which events?" : "Apply changes to…", isPresented: Binding(
                get: { scopePrompt != nil }, set: { if !$0 { scopePrompt = nil } }), titleVisibility: .visible) {
                let action = scopePrompt
                Button("Only this event") { Task { await run(action, .this) } }
                Button("This and following events") { Task { await run(action, .following) } }
                Button("All events", role: action == .delete ? .destructive : nil) { Task { await run(action, .all) } }
            } message: { Text(scopeHelp) }
            .confirmationDialog("Delete this event?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { Task { await run(.delete, .all) } }
            }
        }
        .presentationDetents([.large])
    }

    // MARK: Sections

    private var reminderSection: some View {
        Section {
            Picker("Reminder", selection: $draft.notifyMinutes) {
                Text("None").tag(Int?.none)
                ForEach([0, 5, 10, 15, 30, 60, 120, 1440, 2880], id: \.self) { minutes in
                    Text(Self.reminderTitle(minutes)).tag(Int?.some(minutes))
                }
            }
        } footer: { Text(notifyHelp) }
    }

    private var attendeesSection: some View {
        Section("Who's involved") {
            ForEach(app.members) { member in
                Toggle(isOn: attendeeBinding(member.id)) { MemberLabel(member: member) }
            }
        }
    }

    @ViewBuilder private var deleteSection: some View {
        if series != nil {
            Section {
                Button("Delete Event", role: .destructive) {
                    if isRecurringEdit { scopePrompt = .delete } else { confirmDelete = true }
                }
            }
        }
    }

    private func attendeeBinding(_ id: Int) -> Binding<Bool> {
        Binding(
            get: { draft.attendees.contains(id) },
            set: { on in if on { draft.attendees.insert(id) } else { draft.attendees.remove(id) } })
    }


    @ViewBuilder private var calendarSection: some View {
        if needsCalendarChoice {
            Section {
                // Opens unselected on Add and blocks Save until answered: landing on somebody's
                // calendar by position is what this replaced.
                Picker("Whose calendar", selection: Binding(
                    get: { draft.calendarID },
                    set: { id in draft.choose(calendar: model.nativeCalendars.first { $0.id == id }) }
                )) {
                    Text("Choose a calendar…").tag(Int?.none)
                    ForEach(Array(owners.enumerated()), id: \.offset) { _, group in
                        Section(group.member?.name ?? "Unassigned") {
                            ForEach(group.calendars) { Text($0.label).tag(Int?.some($0.id)) }
                        }
                    }
                }
                // Push-style: a menu picker with grouped sections froze the app on open.
                .pickerStyle(.navigationLink)
                .accessibilityIdentifier("event-calendar")
            }
        }
    }

    private var timeSection: some View {
        Section {
            Toggle("All day", isOn: $draft.allDay)
            if draft.allDay {
                DatePicker("Starts", selection: dayBinding(\.dayStart, shiftEnd: true), displayedComponents: .date)
                DatePicker("Ends", selection: dayBinding(\.dayEnd), in: dayBinding(\.dayStart).wrappedValue..., displayedComponents: .date)
            } else {
                DatePicker("Starts", selection: timedStartBinding, displayedComponents: [.date, .hourAndMinute])
                DatePicker("Ends", selection: timedBinding(\.timedEnd), displayedComponents: [.date, .hourAndMinute])
            }
        }
        .environment(\.timeZone, Self.utc)
    }

    @ViewBuilder private var repeatSection: some View {
        Section("Repeats") {
            Picker("Repeats", selection: $draft.repeatChoice) {
                ForEach(RepeatChoice.allCases.filter { $0 != .other || draft.repeatChoice == .other }) { Text($0.title).tag($0) }
            }
            if draft.repeatChoice == .custom { CustomRepeatFields(rule: $draft.custom) }
            if draft.repeatChoice != .none && draft.repeatChoice != .other { EndsFields(ends: $draft.ends, allDay: draft.allDay).environment(\.timeZone, Self.utc) }
            if draft.repeatChoice == .other {
                Text("This event repeats on a schedule too detailed to edit here. It will be kept as it is.")
                    .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
            }
            if !readBack.isEmpty {
                Text("Repeats \(readBack)").font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")).accessibilityIdentifier("event-readback")
            }
        }
    }

    // MARK: Bindings

    private func dayBinding(_ path: WritableKeyPath<EventDraft, String>, shiftEnd: Bool = false) -> Binding<Date> {
        Binding(
            get: { DayString.date(draft[keyPath: path], calendar: EventDraft.utc) ?? .now },
            set: { new in
                let old = draft[keyPath: path]
                draft[keyPath: path] = DayString.string(new, calendar: EventDraft.utc)
                if shiftEnd { draft.startChangedAllDay(from: old) }
            })
    }

    private var timedStartBinding: Binding<Date> {
        Binding(
            get: { EventDraft.wall(draft.timedStart) ?? .now },
            set: { new in let old = draft.timedStart; draft.timedStart = EventDraft.wallString(new); draft.startChangedTimed(from: old) })
    }

    private func timedBinding(_ path: WritableKeyPath<EventDraft, String>) -> Binding<Date> {
        Binding(get: { EventDraft.wall(draft[keyPath: path]) ?? .now }, set: { draft[keyPath: path] = EventDraft.wallString($0) })
    }

    // MARK: Help text

    private static func reminderTitle(_ m: Int) -> String {
        switch m { case 0: "At the start"; case 60: "1 hour before"; case 120: "2 hours before"; case 1440: "1 day before"; case 2880: "2 days before"
        default: "\(m) minutes before" }
    }

    /// Names who a reminder will actually reach, while it is being set. An event with a
    /// reminder and nobody reachable should say so now, not fail silently later.
    private var notifyHelp: String {
        guard draft.notifyMinutes != nil else { return "Reminders are sent by Pushover." }
        let chosen = app.members.filter { draft.attendees.contains($0.id) }
        let reachable = chosen.filter { !($0.pushoverUserKey ?? "").isEmpty }
        if chosen.isEmpty { return "Nobody is on this event yet, so nobody will be reminded." }
        if reachable.isEmpty { return "No Pushover key for \(chosen.map(\.name).joined(separator: ", ")) — add one in Settings or nobody will be reminded." }
        return "Will notify \(reachable.map(\.name).joined(separator: ", "))."
    }

    private var scopeHelp: String {
        let date = occurrence?.occurrenceDate ?? ""
        let moved = series?.overrides.filter { !$0.cancelled }.count ?? 0
        var text = scopePrompt == .delete
            ? "“This event” and “This and following” are measured from \(date). Deleting cannot be undone."
            : (moved > 0 ? "\(moved) occurrence(s) have their own changes. “All events” keeps them." : "")
        if scopePrompt == .delete && date.isEmpty { text = "Deleting cannot be undone." }
        return text
    }

    // MARK: Actions

    private func attemptSave() {
        if isRecurringEdit { scopePrompt = .save } else { Task { await save(.all) } }
    }

    private func setUp() {
        guard !didSetUp else { return }
        didSetUp = true
        switch target {
        case .add(let day):
            var d = EventDraft(day: day)
            // One calendar is not a choice, so it is preselected.
            if model.nativeCalendars.count == 1 { d.choose(calendar: model.nativeCalendars.first) }
            draft = d; titleFocused = true
        case .edit(let o, let s):
            series = s; occurrence = o
            draft = EventDraft(series: s, occurrence: o)
            storedRuleText = o.recurrenceText
        }
    }

    private func refreshReadBack() async {
        if draft.repeatChoice == .none { readBack = ""; return }
        if draft.repeatChoice == .other { readBack = storedRuleText; return }
        guard let rule = draft.compiledRule else { readBack = ""; return }
        try? await Task.sleep(for: .milliseconds(250))
        guard !Task.isCancelled else { return }
        readBack = (try? await app.client?.describeRecurrence(rule)) ?? ""
    }

    private func run(_ action: ScopeAction?, _ scope: EditScope) async {
        switch action {
        case .save: await save(scope)
        case .delete:
            guard let series else { return }
            if await model.delete(series: series, scope: scope, occurrenceDate: occurrence?.occurrenceDate) { dismiss() }
        case nil: break
        }
    }

    private func save(_ scope: EditScope) async {
        saving = true
        defer { saving = false }
        let ok: Bool
        if let series { ok = await model.update(draft, series: series, scope: scope, occurrenceDate: occurrence?.occurrenceDate) }
        else { ok = await model.create(draft) }
        if ok { dismiss() }
    }
}

/// The Custom… rule: an interval over days, weeks, months or years, several weekdays
/// at once, and a monthly rule by date or by position.
struct CustomRepeatFields: View {
    @Binding var rule: CustomRepeat
    private static let ordinals = [("1", "First"), ("2", "Second"), ("3", "Third"), ("4", "Fourth"), ("-1", "Last")]
    private static let names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

    var body: some View {
        Picker("Every", selection: $rule.freq) {
            Text("Days").tag(CustomRepeat.Freq.daily); Text("Weeks").tag(CustomRepeat.Freq.weekly)
            Text("Months").tag(CustomRepeat.Freq.monthly); Text("Years").tag(CustomRepeat.Freq.yearly)
        }
        .pickerStyle(.segmented)

        if !(rule.freq == .daily && rule.weekdaysOnly) {
            let unit = switch rule.freq { case .daily: "day"; case .weekly: "week"; case .monthly: "month"; case .yearly: "year" }
            Stepper(rule.interval == 1 ? "Every \(unit)" : "Every \(rule.interval) \(unit)s", value: $rule.interval, in: 1...99)
        }
        switch rule.freq {
        case .daily:
            // "Every weekday" pins the interval to 1: RRULE has no way to say "every third weekday".
            Toggle("Weekdays only", isOn: $rule.weekdaysOnly).onChange(of: rule.weekdaysOnly) { _, on in if on { rule.interval = 1 } }
        case .weekly:
            HStack(spacing: RallyDesign.space[1]) {
                ForEach(Array(Recurrence.weekdayCodes.enumerated()), id: \.offset) { index, code in
                    let on = rule.weekdays.contains(code)
                    Button { if on { rule.weekdays.remove(code) } else { rule.weekdays.insert(code) } } label: {
                        Text(String(Self.names[index].prefix(1)))
                            .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin)
                            .background(on ? RallyDesign.color("ink") : RallyDesign.color("surfaceSunken"), in: Circle())
                            .foregroundStyle(on ? RallyDesign.color("surface") : RallyDesign.color("ink"))
                    }
                    .buttonStyle(.plain).accessibilityLabel(Self.names[index]).accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            if rule.weekdays.isEmpty { Text("Pick at least one day.").font(.footnote).foregroundStyle(RallyDesign.color("stateOverdue")) }
        case .monthly:
            Picker("By", selection: $rule.monthlyMode) {
                Text("Date").tag(CustomRepeat.MonthlyMode.day); Text("Weekday").tag(CustomRepeat.MonthlyMode.weekday)
            }
            .pickerStyle(.segmented)
            if rule.monthlyMode == .day {
                Picker("On", selection: $rule.monthDay) {
                    ForEach(1...31, id: \.self) { Text("\($0)").tag($0) }
                    Text("Last day").tag(-1)
                }
                if (29...31).contains(rule.monthDay) {
                    Text("Months without a \(rule.monthDay)th are skipped. Choose “Last day” to land on the end of every month.")
                        .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                }
            } else {
                Picker("Position", selection: $rule.ordinal) { ForEach(Self.ordinals, id: \.0) { Text($0.1).tag($0.0) } }
                Picker("Weekday", selection: $rule.weekday) {
                    ForEach(Array(Recurrence.weekdayCodes.enumerated()), id: \.offset) { Text(Self.names[$0.offset]).tag($0.element) }
                }
            }
        case .yearly: EmptyView()
        }
    }
}

/// `Ends` bounds any repeating event: Never, on a date, or after N occurrences.
struct EndsFields: View {
    @Binding var ends: Ends
    let allDay: Bool
    private enum Kind: String { case never, until, count }

    private var kind: Binding<Kind> {
        Binding(
            get: { switch ends { case .never: .never; case .until: .until; case .count: .count } },
            set: { k in
                switch k {
                case .never: ends = .never
                case .until: if case .until = ends {} else { ends = .until(DayString.string(.now.addingTimeInterval(86400 * 90), calendar: EventDraft.utc)) }
                case .count: if case .count = ends {} else { ends = .count(10) }
                }
            })
    }

    var body: some View {
        Picker("Ends", selection: kind) {
            Text("Never").tag(Kind.never); Text("On date").tag(Kind.until); Text("After").tag(Kind.count)
        }
        if case .until(let day) = ends {
            DatePicker("Last day", selection: Binding(
                get: { DayString.date(day, calendar: EventDraft.utc) ?? .now },
                set: { ends = .until(DayString.string($0, calendar: EventDraft.utc)) }), displayedComponents: .date)
        }
        if case .count(let n) = ends {
            Stepper("\(n) time\(n == 1 ? "" : "s")", value: Binding(get: { n }, set: { ends = .count($0) }), in: 1...999)
        }
    }
}
