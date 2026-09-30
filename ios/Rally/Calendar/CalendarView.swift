import RallyKit
import SwiftUI

struct CalendarView: View {
    @Environment(AppModel.self) private var app
    @State private var model: CalendarModel
    @State private var detail: Occurrence?
    @State private var editor: EventEditorTarget?
    private let client: APIClient

    init(client: APIClient, timeZone: TimeZone) {
        self.client = client
        // Rally's own rule for `auto`: Day on a phone, Month on anything wider. It lives
        // here and nowhere else, because only the page holding the rule can resolve `auto`.
        let narrow = UIDevice.current.userInterfaceIdiom == .phone
        _model = State(initialValue: CalendarModel(client: client, math: CalendarMath(timeZone: timeZone), narrow: narrow))
    }

    var body: some View {
        VStack(spacing: 0) {
            controls
            if !model.failures.isEmpty {
                Label("Couldn't update \(model.failures.joined(separator: ", ")) — showing what Rally last saw.", systemImage: "exclamationmark.triangle")
                    .font(.footnote).padding(RallyDesign.space[2]).frame(maxWidth: .infinity, alignment: .leading)
                    .background(RallyDesign.color("surfaceSunken"))
            }
            Text(model.rangeLabel + (model.marksToday ? " · Today" : ""))
                .font(.system(.headline, design: .serif)).textCase(.uppercase).tracking(0.8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, RallyDesign.space[4]).padding(.vertical, RallyDesign.space[2])
                .accessibilityIdentifier("calendar-title")
            content
        }
        .background(RallyDesign.color("surface"))
        .navigationTitle("Calendar")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { openAdd() } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Add event").accessibilityIdentifier("calendar-add")
            }
        }
        .task {
            await model.loadNativeCalendars()
            await applyLandingView()
        }
        .task(id: loadKey) { await model.load() }
        .task {
            // A wall tablet is never reloaded: keep the date honest and the list fresh.
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(300))
                model.tick()
                await model.load()
            }
        }
        .onChange(of: app.install.timeZone) { _, zone in model.update(math: CalendarMath(timeZone: zone)) }
        .sheet(item: $detail) { occurrence in
            EventDetailSheet(occurrence: occurrence, client: client) {
                detail = nil
                Task { if let series = await model.series(for: occurrence) { editor = .edit(occurrence, series) } }
            }
        }
        .sheet(item: $editor) { EventEditor(model: model, target: $0) }
        .alert("Couldn't do that", isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.errorMessage ?? "") }
    }

    private var loadKey: String {
        "\(model.mode.rawValue)|\(model.range.rawValue)|\(model.math.iso(model.anchor))|\(model.selectedMembers.sorted())|\(model.math.calendar.timeZone.identifier)"
    }

    // MARK: Controls

    private var controls: some View {
        VStack(spacing: 0) {
            HStack(spacing: RallyDesign.space[2]) {
                Picker("View", selection: Binding(get: { model.mode }, set: { model.set(mode: $0) })) {
                    ForEach(CalendarMode.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented).frame(maxWidth: 220)
                .accessibilityIdentifier("calendar-mode")
                Spacer(minLength: 0)
                Picker("Range", selection: Binding(get: { model.range }, set: { model.set(range: $0) })) {
                    ForEach(CalendarRange.offered(in: model.mode)) { Text($0.title).tag($0) }
                }
                .pickerStyle(.menu).accessibilityIdentifier("calendar-range")
            }
            .padding(.horizontal, RallyDesign.space[4]).padding(.vertical, RallyDesign.space[1])

            HStack(spacing: RallyDesign.space[2]) {
                navButton("chevron.left", "Previous") { model.shift(-1) }.accessibilityIdentifier("calendar-prev")
                navButton("chevron.right", "Next") { model.shift(1) }.accessibilityIdentifier("calendar-next")
                Button("Today") { model.goToToday() }.frame(minHeight: RallyDesign.targetMin).accessibilityIdentifier("calendar-today")
                Spacer()
            }
            .padding(.horizontal, RallyDesign.space[4])

            ChipBar(chips: app.members.map { FilterChip(value: $0.name, title: $0.name, colorHex: $0.color) }, selected: model.selectedMembers,
                    toggle: model.toggleMember, clear: { model.selectedMembers = [] })
        }
    }

    private func navButton(_ symbol: String, _ label: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).frame(width: RallyDesign.targetMin, height: RallyDesign.targetMin)
        }
        .accessibilityLabel(label)
    }

    // MARK: Body

    @ViewBuilder private var content: some View {
        if model.loadFailed && !model.hasLoaded {
            ContentUnavailableView("Couldn't load the calendar", systemImage: "wifi.exclamationmark")
        } else if model.mode == .agenda {
            AgendaView(days: model.math.days(mode: model.mode, range: model.range, anchor: model.anchor),
                       occurrences: model.occurrences, math: model.math, range: model.range, select: { detail = $0 })
        } else if model.range == .month {
            MonthGridView(anchor: model.anchor, occurrences: model.occurrences, math: model.math, select: { detail = $0 }, openDay: { model.drill(into: $0) })
        } else {
            TimeGridView(days: model.math.days(mode: model.mode, range: model.range, anchor: model.anchor),
                         occurrences: model.occurrences, math: model.math, select: { detail = $0 })
        }
    }

    // MARK: Actions

    private func openAdd() {
        editor = .add(day: model.math.defaultEventDate(mode: model.mode, range: model.range, anchor: model.anchor))
    }

    /// Where this device is set to start for the person using it (Settings → Personal Defaults).
    /// A device nobody has claimed, a person who has never chosen, and `auto` all land on the width rule.
    private func applyLandingView() async {
        guard let member = app.memberID,
              let prefs = try? await client.devicePreferences(deviceID: app.deviceID),
              let value = prefs[String(member)]?["calendar_default_view"] else { return }
        model.applyLandingView(value)
    }
}

enum EventEditorTarget: Identifiable {
    case add(day: String)
    case edit(Occurrence, EventSeries)
    var id: String { switch self { case .add(let d): "add-\(d)"; case .edit(let o, _): "edit-\(o.id)" } }
}
