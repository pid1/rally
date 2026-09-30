import RallyKit
import SwiftUI

/// Today, from the snapshot Rally generates each morning — plus today's Daily
/// Note, which is read live so one written at breakfast shows up at once.
struct DashboardView: View {
    let client: APIClient
    @State private var dashboard: Dashboard?
    @State private var failed = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: RallyDesign.space[4]) {
                if let dashboard {
                    Text(dashboard.greeting)
                        .font(.system(.title2, design: .serif))
                        .accessibilityIdentifier("dashboard-greeting")

                    if !dashboard.briefing.isEmpty {
                        Card(title: "The Briefing") { Text(dashboard.briefing) }
                    }
                    Card(title: "Weather & Preparedness") { Text(dashboard.weatherSummary) }
                    if let note = dashboard.note {
                        Card(title: "Daily Note") { MarkdownText(markdown: note.body) }
                    }
                    Card(title: "Today's Schedule") {
                        if dashboard.schedule.isEmpty {
                            Text("No events scheduled today.").foregroundStyle(RallyDesign.color("inkMuted"))
                        }
                        VStack(alignment: .leading, spacing: RallyDesign.space[3]) {
                            ForEach(dashboard.schedule) { item in
                                HStack(alignment: .firstTextBaseline, spacing: RallyDesign.space[3]) {
                                    Text(item.time).font(.subheadline.monospacedDigit())
                                        .foregroundStyle(RallyDesign.color("inkMuted"))
                                        .frame(width: 84, alignment: .leading)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.title)
                                        if !item.notes.isEmpty {
                                            Text(item.notes).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if let stem = dashboard.stemConcept { StemCard(stem: stem) }

                    if let when = dashboard.generatedAt {
                        Text("Last updated \(when.formatted(date: .abbreviated, time: .shortened))")
                            .font(.footnote).foregroundStyle(RallyDesign.color("inkSubtle"))
                            .frame(maxWidth: .infinity)
                    }
                } else if failed {
                    ContentUnavailableView("Couldn't load today", systemImage: "wifi.exclamationmark",
                                           description: Text("Pull down to try again."))
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, RallyDesign.space[7])
                }
            }
            .padding(RallyDesign.space[4])
            .frame(maxWidth: 720)
            .frame(maxWidth: .infinity)
        }
        .background(RallyDesign.color("surfaceSunken").opacity(0.6))
        .navigationTitle(Date.now.formatted(.dateTime.weekday(.wide).month(.wide).day()))
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task {
            // The note is live, so look again now and then rather than once.
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(for: .seconds(300))
            }
        }
    }

    private func load() async {
        do { dashboard = try await client.dashboard(); failed = false }
        catch { if dashboard == nil { failed = true } }
    }
}

private struct Card<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: RallyDesign.space[2]) {
            Text(title.uppercased())
                .font(.caption.weight(.semibold)).tracking(1.2)
                .foregroundStyle(RallyDesign.color("inkMuted"))
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(RallyDesign.space[4])
        .background(RallyDesign.color("surface"), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(RallyDesign.color("ruleSubtle")))
    }
}

private struct StemCard: View {
    let stem: StemConcept

    var body: some View {
        Card(title: "STEM Concept of the Day") {
            VStack(alignment: .leading, spacing: RallyDesign.space[2]) {
                HStack(alignment: .firstTextBaseline) {
                    Text(stem.title).font(.headline)
                    if !stem.field.isEmpty {
                        Text(stem.field).font(.caption).foregroundStyle(RallyDesign.color("inkMuted"))
                    }
                }
                Text(stem.explanation)
                ForEach(Array(stem.activities.enumerated()), id: \.offset) { _, activity in
                    VStack(alignment: .leading, spacing: 2) {
                        if !activity.audience.isEmpty {
                            Text(activity.audience).font(.caption.weight(.semibold))
                                .foregroundStyle(RallyDesign.color("inkMuted"))
                        }
                        Text(activity.idea)
                    }
                }
            }
        }
    }
}
