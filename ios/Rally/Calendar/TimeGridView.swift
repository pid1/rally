import RallyKit
import SwiftUI

/// Calendar + Day/Week: hour gutter, one column per day, blocks positioned and
/// sized by start and duration. Day and Week are the same component with one
/// column or seven. Duration is height and adjacency is position, so an overlap
/// is visible rather than something you work out from two timestamps.
struct TimeGridView: View {
    let days: [Date]
    let occurrences: [Occurrence]
    let math: CalendarMath
    let select: (Occurrence) -> Void

    /// 30 minutes is exactly 44pt: the shortest body and the hit-area floor are
    /// pinned to each other, which is the whole reason for this value.
    static let hourHeight: CGFloat = 88
    private static let dayHeight = hourHeight * 24

    @Environment(\.horizontalSizeClass) private var sizeClass

    private var gutter: CGFloat { sizeClass == .compact ? 40 : 56 }

    var body: some View {
        GeometryReader { proxy in
            // No column goes under 44pt; a grid that cannot fit scrolls inside itself
            // rather than pushing the page sideways.
            let colWidth = max(RallyDesign.targetMin, (proxy.size.width - gutter) / CGFloat(max(1, days.count)))
            let contentWidth = gutter + colWidth * CGFloat(days.count)
            let grid = VStack(spacing: 0) {
                header(colWidth: colWidth)
                allDayBand(colWidth: colWidth)
                hours(colWidth: colWidth)
            }
            // Bounded to the viewport: a vertical ScrollView nested in a horizontal one is
            // otherwise as tall as a whole day, and the overflow is centered and clipped.
            .frame(width: max(contentWidth, proxy.size.width), height: proxy.size.height, alignment: .top)

            // Only scroll sideways when the columns genuinely cannot fit at 44pt each.
            if contentWidth > proxy.size.width {
                ScrollView(.horizontal, showsIndicators: false) { grid }
            } else {
                grid
            }
        }
    }

    // MARK: Header and all-day band

    private func header(colWidth: CGFloat) -> some View {
        HStack(spacing: 0) {
            Color.clear.frame(width: gutter, height: 1)
            ForEach(days, id: \.self) { day in
                let isToday = math.iso(day) == math.today()
                VStack(spacing: 2) {
                    Text(math.weekdaySymbol(day)).font(.caption2).textCase(.uppercase)
                    Text("\(math.dayNumber(day))")
                        .font(.headline.monospacedDigit())
                        .frame(minWidth: 30, minHeight: 30)
                        .background(isToday ? RallyDesign.color("ink") : .clear, in: Circle())
                        .foregroundStyle(isToday ? RallyDesign.color("surface") : RallyDesign.color("ink"))
                }
                .frame(width: colWidth)
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(isToday ? .isSelected : [])
            }
        }
        .padding(.vertical, RallyDesign.space[1])
        .background(RallyDesign.color("surface"))
        .overlay(alignment: .bottom) { Rectangle().fill(RallyDesign.color("ruleSubtle")).frame(height: 1) }
    }

    /// Pinned above the scrolling hours. A Thu–Sun trip is one bar across four
    /// columns rather than four disconnected chips — the only reason the band is
    /// worth its vertical space, and why it collapses entirely on a week with none.
    @ViewBuilder private func allDayBand(colWidth: CGFloat) -> some View {
        let bars = math.allDayBars(days: days, in: occurrences)
        if !bars.isEmpty {
            // Stack bars into rows so two trips in one week do not overlap.
            let rows = packRows(bars.map { ($0.first, $0.last) })
            ZStack(alignment: .topLeading) {
                ForEach(Array(bars.enumerated()), id: \.offset) { index, bar in
                    Button { select(bar.occurrence) } label: {
                        HStack(spacing: 4) {
                            MemberDot(hex: bar.occurrence.memberColor)
                            Text(bar.occurrence.title).font(.caption).lineLimit(1)
                        }
                        .padding(.horizontal, 6)
                        .frame(width: colWidth * CGFloat(bar.last - bar.first + 1) - 4, height: RallyDesign.targetMin - 4, alignment: .leading)
                        .background(RallyDesign.color("surfaceSunken"), in: RoundedRectangle(cornerRadius: 6))
                        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(style: StrokeStyle(lineWidth: 1, dash: bar.occurrence.editable ? [] : [3, 2]))
                            .foregroundStyle(RallyDesign.color("rule")))
                    }
                    .buttonStyle(.plain)
                    .offset(x: gutter + colWidth * CGFloat(bar.first) + 2, y: CGFloat(rows[index]) * RallyDesign.targetMin + 2)
                    .accessibilityLabel("\(bar.occurrence.title), all day")
                }
            }
            .frame(maxWidth: .infinity, minHeight: CGFloat((rows.max() ?? 0) + 1) * RallyDesign.targetMin + 4, alignment: .topLeading)
            .overlay(alignment: .leading) { Text("All day").font(.caption2).foregroundStyle(RallyDesign.color("inkMuted")).frame(width: gutter) }
            .overlay(alignment: .bottom) { Rectangle().fill(RallyDesign.color("ruleSubtle")).frame(height: 1) }
        }
    }

    private func packRows(_ spans: [(Int, Int)]) -> [Int] {
        var ends: [Int] = []
        return spans.map { span in
            if let i = ends.firstIndex(where: { $0 < span.0 }) { ends[i] = span.1; return i }
            ends.append(span.1); return ends.count - 1
        }
    }

    // MARK: Hours

    private func hours(colWidth: CGFloat) -> some View {
        ScrollViewReader { reader in
            ScrollView(.vertical, showsIndicators: true) {
                ZStack(alignment: .topLeading) {
                    HStack(alignment: .top, spacing: 0) {
                        VStack(spacing: 0) {
                            ForEach(0..<24, id: \.self) { hour in
                                // Midnight carries no label: the row is the top of the grid and a
                                // "12 AM" there reads as a value rather than a boundary.
                                Text(hour == 0 ? "" : "\(hour % 12 == 0 ? 12 : hour % 12) \(hour < 12 ? "AM" : "PM")")
                                    .font(.caption2).foregroundStyle(RallyDesign.color("inkMuted"))
                                    .frame(width: gutter, height: Self.hourHeight, alignment: .topTrailing)
                                    .padding(.trailing, 4)
                                    .frame(width: gutter, alignment: .trailing)
                                    .id("hour-\(hour)")
                            }
                        }
                        ForEach(days, id: \.self) { day in column(day, width: colWidth) }
                    }
                    nowLine(colWidth: colWidth)
                }
                .frame(height: Self.dayHeight)
            }
            // Re-open on the current hour whenever the set of days changes.
            .task(id: days.first) {
                let target = CalendarMath.openingScrollMinutes(nowMinutes: math.nowMinutes()) / 60
                reader.scrollTo("hour-\(target)", anchor: .top)
            }
        }
    }

    private func column(_ day: Date, width: CGFloat) -> some View {
        let iso = math.iso(day)
        let isToday = iso == math.today()
        return ZStack(alignment: .topLeading) {
            VStack(spacing: 0) {
                ForEach(0..<24, id: \.self) { _ in
                    Rectangle().fill(RallyDesign.color("ruleSubtle")).frame(height: 1).frame(height: Self.hourHeight, alignment: .top)
                }
            }
            ForEach(math.blocks(on: iso, in: occurrences)) { block in blockView(block, colWidth: width) }
        }
        .frame(width: width, height: Self.dayHeight, alignment: .topLeading)
        .background(isToday ? RallyDesign.color("surfaceSunken").opacity(0.5) : .clear)
        .overlay(alignment: .leading) { Rectangle().fill(RallyDesign.color("ruleSubtle")).frame(width: 1) }
        .clipped() // a late-night block must not paint past the bottom of the day
    }

    private func blockView(_ block: PlacedBlock, colWidth: CGFloat) -> some View {
        let w = colWidth / CGFloat(block.columns)
        let y = CGFloat(block.paint.start) / 60 * Self.hourHeight
        let bodyH = CGFloat(block.paint.minutes) / 60 * Self.hourHeight
        let o = block.occurrence
        let time = o.startDate == block.iso ? o.timeLabel : "Continues"
        let tab: CGFloat = 8

        return Button { select(o) } label: {
            HStack(spacing: 0) {
                if block.paint.short {
                    // Under 30 minutes: a tab down the left at the event's own rounded
                    // length carries the color; the words and the tap target live in the body.
                    VStack { Rectangle().fill(memberColor(o)).frame(width: tab, height: CGFloat(block.paint.tabMinutes) / 60 * Self.hourHeight); Spacer(minLength: 0) }
                }
                VStack(alignment: .leading, spacing: 0) {
                    Text(o.title).font(.caption.weight(.semibold)).lineLimit(block.paint.short ? 1 : 3)
                    Text(time).font(.caption2).foregroundStyle(RallyDesign.color("inkMuted")).lineLimit(1)
                }
                .padding(.horizontal, 4).padding(.vertical, 2)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .background(RallyDesign.color("surfaceSunken"))
                .overlay(alignment: .leading) { if !block.paint.short { Rectangle().fill(memberColor(o)).frame(width: 3) } }
            }
            .frame(width: w - 1, height: bodyH - 1, alignment: .topLeading)
            .clipShape(RoundedRectangle(cornerRadius: 4))
            .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(style: StrokeStyle(lineWidth: 1, dash: o.editable ? [] : [3, 2])).foregroundStyle(RallyDesign.color("rule")))
        }
        .buttonStyle(.plain)
        .offset(x: CGFloat(block.column) * w, y: y)
        .accessibilityLabel("\(o.title), \(time)")
    }

    private func memberColor(_ o: Occurrence) -> Color { o.memberColor.map(RallyDesign.memberColor(hex:)) ?? RallyDesign.color("inkMuted") }

    /// One line across the whole grid rather than one per column: the grid's value
    /// is a shared vertical axis, so the element naming your place on it has to
    /// cross every column to be read against any of them. Drawn in every displayed
    /// range. A hairline, never a fill — color on this page means a family member.
    private func nowLine(colWidth: CGFloat) -> some View {
        TimelineView(.everyMinute) { context in
            let minutes = math.nowMinutes(now: context.date)
            Rectangle().fill(RallyDesign.color("ink"))
                .frame(width: colWidth * CGFloat(days.count), height: 1.5)
                .offset(x: gutter, y: CGFloat(minutes) / 60 * Self.hourHeight)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
    }
}

struct MemberDot: View {
    let hex: String?
    var body: some View {
        Circle().fill(hex.map(RallyDesign.memberColor(hex:)) ?? RallyDesign.color("inkMuted")).frame(width: 8, height: 8)
    }
}
