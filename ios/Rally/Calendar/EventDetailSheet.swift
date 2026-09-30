import RallyKit
import SwiftUI

/// A read-only look at one occurrence. External events have no Edit button — and
/// say why, on the screen where the question is asked.
struct EventDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    let occurrence: Occurrence
    let client: APIClient
    let edit: () -> Void
    @State private var notifyResult: String?
    @State private var notifying = false

    var body: some View {
        NavigationStack {
            List {
                ForEach(CalendarText.detailRows(occurrence), id: \.key) { row in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.label).font(.caption).foregroundStyle(RallyDesign.color("inkMuted"))
                        Text(row.value)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("detail-row-\(row.key)")
                }
                if occurrence.editable && !occurrence.attendees.isEmpty {
                    Section {
                        Button(notifying ? "Sending…" : "Notify attendees") { Task { await notify() } }.disabled(notifying)
                        if let notifyResult { Text(notifyResult).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
                    }
                }
            }
            .navigationTitle(occurrence.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                if occurrence.editable {
                    ToolbarItem(placement: .confirmationAction) { Button("Edit", action: edit).accessibilityIdentifier("detail-edit") }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func notify() async {
        guard let id = occurrence.eventID else { return }
        notifying = true
        defer { notifying = false }
        do { notifyResult = try await client.notifyEvent(id: id, occurrenceDate: occurrence.occurrenceDate).summary }
        catch { notifyResult = "Could not reach Rally to send that." }
    }
}
