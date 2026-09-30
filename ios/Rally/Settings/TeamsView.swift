import RallyKit
import SwiftUI

struct TeamsView: View {
    @Environment(AppModel.self) private var app
    @State private var model: TeamsModel
    @State private var editing: TeamTarget?

    init(client: APIClient) { _model = State(initialValue: TeamsModel(client: client)) }

    var body: some View {
        List {
            do {
                if model.hasLoaded && model.teams.isEmpty {
                    ContentUnavailableView("No teams followed", systemImage: "sportscourt", description: Text("Tap + to follow a team or racing series."))
                        .listRowBackground(Color.clear)
                }
                ForEach(model.teams) { team in
                    Button { editing = .edit(team) } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack { Text(team.label); if !team.active { Text("Paused").font(.caption).foregroundStyle(RallyDesign.color("inkMuted")) } }
                            Text("\(team.provider.uppercased()) · \(team.league)").font(.caption).foregroundStyle(RallyDesign.color("inkMuted"))
                        }
                        .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin, alignment: .leading).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .navigationTitle("Sports Teams")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { editing = .add } label: { Image(systemName: "plus") }.accessibilityLabel("Follow a team") } }
        .task { await model.load() }
        .sheet(item: $editing) { TeamEditor(model: model, target: $0) }
    }
}

enum TeamTarget: Identifiable {
    case add, edit(FollowedTeam)
    var id: String { switch self { case .add: "add"; case .edit(let t): "edit-\(t.id)" } }
}

struct TeamEditor: View {
    @Environment(\.dismiss) private var dismiss
    let model: TeamsModel
    let target: TeamTarget
    @State private var draft = TeamDraft()
    @State private var testResult: String?
    @State private var confirmDelete = false
    @State private var saving = false
    @State private var didSetUp = false

    private var original: FollowedTeam? { if case .edit(let t) = target { t } else { nil } }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name (e.g. Dallas Stars)", text: $draft.label)
                    Picker("Data source", selection: $draft.provider) {
                        Text("ESPN").tag("espn"); Text("MLB (carries radio)").tag("mlb")
                    }
                    TextField("League (e.g. hockey/nhl)", text: $draft.league).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("Team key (blank for a racing series)", text: $draft.teamKey).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("Radio station (optional)", text: $draft.radioStation)
                    Toggle("Include in the summary", isOn: $draft.active)
                } footer: { Text("Baseball uses MLB's own listings, the only source that carries radio. Everything else uses ESPN.") }
                if let original {
                    Section {
                        Button("Check the next 14 days") { Task { testResult = await model.test(original) } }
                        if let testResult { Text(testResult).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
                        Button("Unfollow", role: .destructive) { confirmDelete = true }
                    }
                }
            }
            .navigationTitle(original == nil ? "Follow a Team" : "Edit Team")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { saving = true; if await model.save(draft, editing: original) { dismiss() }; saving = false } }
                        .disabled(draft.label.trimmingCharacters(in: .whitespaces).isEmpty || draft.league.trimmingCharacters(in: .whitespaces).isEmpty || saving)
                }
            }
            .onAppear { if !didSetUp { didSetUp = true; if let original { draft = TeamDraft(original) } } }
            .confirmationDialog("Unfollow \(original?.label ?? "this team")?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Unfollow", role: .destructive) { Task { if let o = original, await model.delete(o) { dismiss() } } }
            }
            .alert("Couldn't do that", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
                Button("OK") {}
            } message: { Text(model.errorMessage ?? "") }
        }
        .presentationDetents([.large])
    }
}
