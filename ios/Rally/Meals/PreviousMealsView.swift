import RallyKit
import SwiftUI

/// Meals from days before today, with their ratings and reviews. The one archive
/// you can edit, so a save reloads what is loaded rather than jumping back to
/// the first page.
struct PreviousMealsView: View {
    @Environment(AppModel.self) private var app
    @State private var loader: ArchiveLoader<MealPlan, MealFilter>
    @State private var reviewing: MealPlan?
    private let client: APIClient

    init(client: APIClient?) {
        let client = client ?? APIClient(baseURL: URL(string: "http://invalid")!)
        self.client = client
        _loader = State(initialValue: ArchiveLoader<MealPlan, MealFilter>(filter: MealFilter()) { filter, search, limit, offset in
            try await client.previousMeals(filter: filter, search: search, limit: limit, offset: offset)
        })
    }

    private var ratingChips: [FilterChip] {
        [FilterChip(value: "5", title: "5 Stars"), FilterChip(value: "4", title: "4+ Stars"), FilterChip(value: "3", title: "3+ Stars")]
    }

    var body: some View {
        List {
            // Filters live in the list: pinned with safeAreaInset, the horizontal scroller slid
            // under the navigation bar on some screens and left a blank band.
            Section {
                VStack(spacing: 0) {
                    ChipBar(chips: MealLogic.mealTypes.map { FilterChip(value: $0, title: $0) }, selected: loader.filter.mealTypes,
                            toggle: { type in
                                if loader.filter.mealTypes.contains(type) { loader.filter.mealTypes.remove(type) } else { loader.filter.mealTypes.insert(type) }
                            })
                    ChipBar(chips: ratingChips, selected: loader.filter.minRating.map { [String($0)] } ?? [],
                            // Single-select: tapping the active chip clears it.
                            toggle: { value in loader.filter.minRating = loader.filter.minRating == Int(value) ? nil : Int(value) })
                }
        
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
            }
            if loader.hasLoaded && loader.items.isEmpty {
                ContentUnavailableView(loader.search.isEmpty && loader.filter.mealTypes.isEmpty && loader.filter.minRating == nil
                                       ? "No earlier meals" : "No meals match", systemImage: "fork.knife")
                    .listRowBackground(Color.clear)
            }
            ForEach(loader.items) { meal in
                Button { reviewing = meal } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(MealLogic.dayHeading(meal.date, today: app.install.today(), calendar: app.install.calendar).uppercased())
                            .font(.caption.weight(.semibold)).tracking(1.0).foregroundStyle(RallyDesign.color("inkMuted"))
                        MealRow(meal: meal, members: app.members)
                        if meal.rating == nil && (meal.review ?? "").isEmpty {
                            Text("Tap to rate").font(.footnote).foregroundStyle(RallyDesign.color("inkSubtle"))
                        }
                    }
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("prev-meal-\(meal.plan)")
            }
            if loader.hasMore {
                Button { Task { await loader.loadMore() } } label: {
                    Text(loader.isLoading ? "Loading…" : "Load more").frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Previous Meals")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: Bindable(loader).search, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search previous meals")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Sort", selection: Bindable(loader).filter.sort) {
                        ForEach(MealSort.allCases) { Text($0.title).tag($0) }
                    }
                } label: { Image(systemName: "arrow.up.arrow.down") }.accessibilityLabel("Sort")
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if loader.hasLoaded {
                Text("\(loader.total) meal\(loader.total == 1 ? "" : "s")")
                    .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                    .padding(RallyDesign.space[2]).frame(maxWidth: .infinity).background(.bar)
            }
        }
        .task(id: "\(loader.search)|\(loader.filter.sort.rawValue)|\(loader.filter.mealTypes.sorted())|\(loader.filter.minRating ?? 0)") {
            if loader.hasLoaded { try? await Task.sleep(for: .milliseconds(250)) }
            guard !Task.isCancelled else { return }
            await loader.reload()
        }
        .sheet(item: $reviewing) { meal in
            ReviewSheet(meal: meal, client: client) { await loader.refreshLoaded() }
        }
        .alert("Couldn't load that", isPresented: Binding(
            get: { loader.errorMessage != nil }, set: { if !$0 { loader.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(loader.errorMessage ?? "") }
    }
}

/// Rate a meal and say what you thought; or edit the meal itself.
struct ReviewSheet: View {
    @Environment(\.dismiss) private var dismiss
    let meal: MealPlan
    let client: APIClient
    let saved: () async -> Void

    @State private var rating: Int
    @State private var review: String
    @State private var editing: MealTarget?
    @State private var error: String?

    init(meal: MealPlan, client: APIClient, saved: @escaping () async -> Void) {
        self.meal = meal; self.client = client; self.saved = saved
        _rating = State(initialValue: meal.rating ?? 0)
        _review = State(initialValue: meal.review ?? "")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("\(meal.mealType): \(meal.plan)").font(.headline)
                }
                Section("Rating") {
                    HStack(spacing: RallyDesign.space[2]) {
                        ForEach(1...5, id: \.self) { value in
                            Button { rating = rating == value ? 0 : value } label: {
                                Image(systemName: value <= rating ? "star.fill" : "star").font(.title2)
                                    .frame(width: RallyDesign.targetMin, height: RallyDesign.targetMin)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("\(value) star\(value == 1 ? "" : "s")")
                            .accessibilityIdentifier("star-\(value)")
                        }
                        Spacer()
                        if rating > 0 { Button("Clear") { rating = 0 } }
                    }
                }
                Section("Review") { TextField("How was it?", text: $review, axis: .vertical).lineLimit(3...8) }
                Section { Button("Edit meal details…") { editing = .edit(meal) } }
                if let error { Section { Text(error).foregroundStyle(RallyDesign.color("stateOverdue")) } }
            }
            .navigationTitle("Review")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }.accessibilityIdentifier("review-save")
                }
            }
            .sheet(item: $editing) { target in
                MealEditor(target: target) { draft, m in
                    do { _ = try await client.updateMeal(id: m?.id ?? meal.id, draft); await saved(); return true }
                    catch { self.error = error.localizedDescription; return false }
                } onDelete: { m in
                    do { try await client.deleteMeal(id: m.id); await saved(); dismiss(); return true }
                    catch { self.error = error.localizedDescription; return false }
                }
            }
        }
        .presentationDetents([.large])
    }

    private func save() async {
        do {
            _ = try await client.reviewMeal(id: meal.id, rating: rating == 0 ? nil : rating, review: review)
            await saved()
            dismiss()
        } catch { self.error = error.localizedDescription }
    }
}
