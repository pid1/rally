import RallyKit
import SwiftUI

struct MealPlannerView: View {
    @Environment(AppModel.self) private var app
    @State private var model: MealsModel
    @State private var editing: MealTarget?

    init(client: APIClient) { _model = State(initialValue: MealsModel(client: client)) }

    private var today: String { app.install.today() }

    var body: some View {
        let days = model.days(today: today)
        List {
            if model.hasLoaded && days.isEmpty {
                ContentUnavailableView(model.mealTypes.isEmpty ? "No upcoming meals" : "No meals match",
                                       systemImage: "fork.knife",
                                       description: Text(model.mealTypes.isEmpty ? "Tap + to plan one." : ""))
                    .listRowBackground(Color.clear)
            }
            ForEach(days) { day in
                Section(MealLogic.dayHeading(day.date, today: today, calendar: app.install.calendar)) {
                    ForEach(day.meals) { meal in
                        Button { editing = .edit(meal) } label: { MealRow(meal: meal, members: app.members) }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("meal-row-\(meal.plan)")
                    }
                }
            }
            Section {
                NavigationLink("View previous meals") { PreviousMealsView(client: app.client) }
                    .accessibilityIdentifier("meals-previous")
            }
        }
        .listStyle(.insetGrouped)
        .safeAreaInset(edge: .top, spacing: 0) {
            ChipBar(chips: MealLogic.mealTypes.map { FilterChip(value: $0, title: $0) }, selected: model.mealTypes,
                    toggle: model.toggleType, clear: { model.mealTypes = [] })
        }
        .navigationTitle("Meal Planner")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { editing = .add } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Add meal").accessibilityIdentifier("meals-add")
            }
        }
        .refreshable { await model.load() }
        .task(id: editing == nil) {
            guard editing == nil else { return }
            while !Task.isCancelled { await model.load(); try? await Task.sleep(for: .seconds(60)) }
        }
        .sheet(item: $editing) { target in
            MealEditor(target: target) { draft, meal in await model.save(draft, editing: meal) }
                onDelete: { meal in await model.delete(meal) }
        }
        .alert("Couldn't do that", isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.errorMessage ?? "") }
    }
}

enum MealTarget: Identifiable {
    case add
    case edit(MealPlan)
    var id: String { switch self { case .add: "add"; case .edit(let m): "edit-\(m.id)" } }
}

struct MealRow: View {
    let meal: MealPlan
    let members: [FamilyMember]

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            (Text("\(meal.mealType): ").bold() + Text(meal.plan))
            if let meta = MealLogic.meta(meal, members: members) {
                Text(meta).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
            }
            if meal.rating != nil || !(meal.review ?? "").isEmpty {
                Stars(rating: meal.rating ?? 0)
                if let review = meal.review, !review.isEmpty {
                    Text(review).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")).lineLimit(3)
                }
            }
        }
        .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin, alignment: .leading)
        .contentShape(Rectangle())
    }
}

struct Stars: View {
    let rating: Int
    var body: some View {
        HStack(spacing: 2) {
            ForEach(1...5, id: \.self) { Image(systemName: $0 <= rating ? "star.fill" : "star") }
        }
        .font(.caption)
        .foregroundStyle(RallyDesign.color("ink"))
        .accessibilityElement()
        .accessibilityLabel(rating == 0 ? "Not rated" : "\(rating) of 5 stars")
    }
}

/// Add or edit a meal. Shared by the planner and the previous-meals archive.
struct MealEditor: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var app
    let target: MealTarget
    let save: (MealDraft, MealPlan?) async -> Bool
    let onDelete: (MealPlan) async -> Bool

    @State private var draft = MealDraft(date: "", mealType: "Dinner")
    @State private var saving = false
    @State private var confirmDelete = false
    @State private var confirmLosingReview = false
    @FocusState private var focused: Bool

    init(target: MealTarget, save: @escaping (MealDraft, MealPlan?) async -> Bool,
         onDelete: @escaping (MealPlan) async -> Bool) {
        self.target = target; self.save = save; self.onDelete = onDelete
    }

    private var original: MealPlan? { if case .edit(let m) = target { m } else { nil } }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    DatePicker("Day", selection: $draft.date.nonOptional.day(), displayedComponents: .date)
                    Picker("Meal", selection: $draft.mealType) {
                        ForEach(MealLogic.mealTypes, id: \.self) { Text($0).tag($0) }
                    }
                    TextField("What's on the menu?", text: $draft.plan, axis: .vertical)
                        .focused($focused).accessibilityIdentifier("meal-plan")
                }
                Section {
                    ForEach(app.members) { member in
                        Toggle(isOn: Binding(
                            get: { draft.attendees.contains(member.id) },
                            set: { on in if on { draft.attendees.insert(member.id) } else { draft.attendees.remove(member.id) } }
                        )) { MemberLabel(member: member) }
                    }
                } header: { Text("Who's eating") } footer: { Text("Leave everyone off to mean the whole family.") }
                Section {
                    Picker("Cook", selection: $draft.cookID) {
                        Text("Nobody in particular").tag(Int?.none)
                        ForEach(app.members) { Text($0.name).tag(Int?.some($0.id)) }
                    }
                }
                if original != nil {
                    Section { Button("Delete Meal", role: .destructive) { confirmDelete = true } }
                }
            }
            .navigationTitle(original == nil ? "Add Meal" : "Edit Meal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { attemptSave() }
                        .disabled(draft.plan.trimmingCharacters(in: .whitespaces).isEmpty || saving)
                        .accessibilityIdentifier("meal-save")
                }
            }
            .onAppear {
                if let original { draft = MealDraft(original) }
                else { draft = MealDraft(date: app.install.today(), mealType: app.install.defaultMealType); focused = true }
            }
            .confirmationDialog("Delete this meal?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { Task { if let m = original, await onDelete(m) { dismiss() } } }
            }
            .confirmationDialog("Move this meal onto the planner?", isPresented: $confirmLosingReview, titleVisibility: .visible) {
                Button("Move and discard review", role: .destructive) { Task { await commit() } }
            } message: { Text("Its rating and review will be removed, because a meal that hasn't happened yet can't be reviewed.") }
        }
        .presentationDetents([.large])
    }

    private func attemptSave() {
        if let original, MealLogic.losesReview(original, movingTo: draft.date, today: app.install.today()) {
            confirmLosingReview = true
        } else { Task { await commit() } }
    }

    private func commit() async {
        saving = true
        defer { saving = false }
        if await save(draft, original) { dismiss() }
    }
}
