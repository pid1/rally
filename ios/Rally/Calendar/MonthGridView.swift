import RallyKit
import SwiftUI

/// Calendar + Month. Event text is stacked in the cell, not just a date and dots:
/// a heat map says Tuesday is busy without saying with what, so reading any day
/// cost a tap and a round trip. Cell height comes from content, so an empty week
/// stays one row tall, and the rows are real targets at the hit-area floor.
struct MonthGridView: View {
    let anchor: Date
    let occurrences: [Occurrence]
    let math: CalendarMath
    let select: (Occurrence) -> Void
    let openDay: (String) -> Void

    var body: some View {
        let weeks = math.monthCells(anchor: anchor, occurrences: occurrences)
        ScrollView {
            VStack(spacing: 0) {
                HStack(spacing: 0) {
                    ForEach(Array(math.calendar.shortWeekdaySymbols.enumerated()), id: \.offset) { _, symbol in
                        Text(symbol).font(.caption2).textCase(.uppercase).foregroundStyle(RallyDesign.color("inkMuted"))
                            .frame(maxWidth: .infinity).padding(.vertical, RallyDesign.space[1])
                    }
                }
                ForEach(Array(weeks.enumerated()), id: \.offset) { _, week in
                    HStack(alignment: .top, spacing: 0) {
                        ForEach(week) { cell in MonthCellView(cell: cell, select: select, openDay: openDay) }
                    }
                    // Equal-height cells, so each cell's divider and tint run the whole row.
                    .fixedSize(horizontal: false, vertical: true)
                    .overlay(alignment: .top) { Rectangle().fill(RallyDesign.color("ruleSubtle")).frame(height: 1) }
                }
            }
        }
    }
}

private struct MonthCellView: View {
    let cell: MonthCell
    let select: (Occurrence) -> Void
    let openDay: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            // The day number opens that day — the same move "+N more" makes,
            // offered on every day rather than only the crowded ones.
            Button { openDay(cell.iso) } label: {
                Text("\(cell.dayNumber)")
                    .font(.subheadline.monospacedDigit().weight(cell.isToday ? .bold : .regular))
                    .frame(width: 30, height: 30)
                    .background(cell.isToday ? RallyDesign.color("ink") : .clear, in: Circle())
                    .foregroundStyle(cell.isToday ? RallyDesign.color("surface") : (cell.isOutside ? RallyDesign.color("inkSubtle") : RallyDesign.color("ink")))
                    .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin - 8)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Open \(cell.iso)")
            .accessibilityIdentifier("month-day-\(cell.iso)")

            ForEach(cell.shown) { o in
                Button { select(o) } label: {
                    VStack(alignment: .leading, spacing: 0) {
                        if !o.allDay { Text(CalendarMath.compactTime(o.timeLabel)).font(.system(size: 9)).foregroundStyle(RallyDesign.color("inkMuted")) }
                        Text(o.title).font(.system(size: 10, weight: .medium)).lineLimit(2)
                    }
                    .padding(.horizontal, 2)
                    .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin - 4, alignment: .topLeading)
                    .background(RallyDesign.color("surfaceSunken"))
                    .overlay(alignment: .leading) { Rectangle().fill(o.memberColor.map(RallyDesign.memberColor(hex:)) ?? RallyDesign.color("inkMuted")).frame(width: 2) }
                    .clipShape(RoundedRectangle(cornerRadius: 2))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(o.allDay ? "\(o.title), all day" : "\(o.title), \(o.timeLabel)")
            }
            if cell.hidden > 0 {
                Button { openDay(cell.iso) } label: {
                    Text("+\(cell.hidden) more").font(.system(size: 10)).foregroundStyle(RallyDesign.color("inkMuted"))
                        .frame(maxWidth: .infinity, minHeight: 24)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(1)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(cell.isOutside ? RallyDesign.color("surfaceSunken").opacity(0.4) : .clear)
        .overlay(alignment: .leading) { Rectangle().fill(RallyDesign.color("ruleSubtle")).frame(width: 1) }
    }
}
