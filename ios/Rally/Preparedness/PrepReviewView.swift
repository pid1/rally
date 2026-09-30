import RallyKit
import SwiftUI

/// Asks the configured LLM what the kit is missing. Reading the last review is
/// free; running one is a real call, so it only ever happens from the button.
struct PrepReviewView: View {
    let client: APIClient?
    @State private var review: PrepReview?
    @State private var loaded = false
    @State private var running = false
    @State private var error: String?

    var body: some View {
        List {
            if let review {
                if review.stale {
                    Section {
                        Label("You had \(review.itemCount) items when this ran and hold \(review.currentItemCount) now. Run it again for a fresh read.",
                              systemImage: "clock.arrow.circlepath").font(.footnote)
                    }
                }
                Section("Assessment") { Text(review.review.assessment) }
                if !review.review.gaps.isEmpty {
                    Section("What's missing") {
                        ForEach(Array(review.review.gaps.enumerated()), id: \.offset) { _, gap in
                            VStack(alignment: .leading, spacing: 2) {
                                HStack { Text(gap.item).font(.headline); Spacer(); Text(gap.priority.capitalized).font(.caption)
                                    .foregroundStyle(gap.priority == "high" ? RallyDesign.color("stateOverdue") : RallyDesign.color("inkMuted")) }
                                if !gap.category.isEmpty { Text(gap.category).font(.caption).foregroundStyle(RallyDesign.color("inkMuted")) }
                                if !gap.why.isEmpty { Text(gap.why).font(.footnote) }
                            }
                        }
                    }
                }
                if !review.review.strengths.isEmpty { Section("Strengths") { ForEach(review.review.strengths, id: \.self) { Text($0) } } }
                if !review.review.assumptions.isEmpty {
                    Section { ForEach(review.review.assumptions, id: \.self) { Text($0) } } header: { Text("Assumptions") }
                        footer: { Text("Things the review didn't know and had to guess at.") }
                }
                if !review.review.notes.isEmpty { Section("Notes") { Text(review.review.notes) } }
                Section { Text("Reviewed \(review.createdAt.formatted(date: .abbreviated, time: .shortened))" + (review.model.map { " · \($0)" } ?? ""))
                    .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
            } else if loaded {
                ContentUnavailableView("Not reviewed yet", systemImage: "sparkles",
                                       description: Text("A review reads your whole inventory and suggests what's missing."))
                    .listRowBackground(Color.clear)
            }
            if let error { Section { Text(error).foregroundStyle(RallyDesign.color("stateOverdue")) } }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("AI Review")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(running ? "Reviewing…" : (review == nil ? "Review" : "Run again")) { Task { await run() } }
                    .disabled(running).accessibilityIdentifier("prep-run-review")
            }
        }
        .task { review = try? await client?.latestPrepReview(); loaded = true }
    }

    private func run() async {
        running = true; error = nil
        defer { running = false }
        do { review = try await client?.runPrepReview() } catch { self.error = error.localizedDescription }
    }
}
