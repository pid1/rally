import RallyKit
import SwiftUI

/// Day-grouped list. Empty days are omitted. A single-day range drops its day
/// heading, which would repeat the range title an inch above it.
struct AgendaView: View {
    let days: [Date]
    let occurrences: [Occurrence]
    let math: CalendarMath
    let range: CalendarRange
    let select: (Occurrence) -> Void

    var body: some View {
        let groups = days.compactMap { day -> (Date, String, [Occurrence])? in
            let iso = math.iso(day)
            let events = math.occurrences(on: iso, in: occurrences)
            return events.isEmpty ? nil : (day, iso, events)
        }
        List {
            if groups.isEmpty {
                ContentUnavailableView(CalendarText.emptyMessage(range: range), systemImage: "calendar").listRowBackground(Color.clear)
            }
            ForEach(groups, id: \.1) { day, iso, events in
                Section {
                    ForEach(events) { o in
                        Button { select(o) } label: { AgendaRow(o: o, iso: iso) }.buttonStyle(.plain)
                    }
                } header: {
                    if range != .day { Text(math.dayHeading(day) + (iso == math.today() ? " · Today" : "")) }
                }
            }
        }
        .listStyle(.insetGrouped)
    }
}

private struct AgendaRow: View {
    let o: Occurrence
    let iso: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: RallyDesign.space[3]) {
            Text(o.allDay ? "All day" : (o.startDate == iso ? o.timeLabel : "Continues"))
                .font(.subheadline.monospacedDigit()).foregroundStyle(RallyDesign.color("inkMuted"))
                .frame(width: 84, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) { MemberDot(hex: o.memberColor); Text(o.title) }
                if !o.location.isEmpty { Text(o.location).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
                let who = o.attendees.isEmpty ? (o.member ?? "") : o.attendees.joined(separator: ", ")
                if !who.isEmpty { Text(who).font(.caption).foregroundStyle(RallyDesign.color("inkSubtle")) }
            }
        }
        .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin, alignment: .leading)
        .contentShape(Rectangle())
    }
}
