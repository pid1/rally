import RallyKit
import SwiftUI

/// What a screen *does* for one person on **this device**. The key is the pair — a person
/// on a device — because width is a guess about a device, not a fact about one.
struct PersonalDefaultsView: View {
    @Environment(AppModel.self) private var app
    @State private var model: PersonalDefaultsModel
    @State private var forgetting: Device?

    init(client: APIClient, deviceID: String) { _model = State(initialValue: PersonalDefaultsModel(client: client, deviceID: deviceID)) }

    var body: some View {
        Form {
            do {
                ForEach(model.catalog) { setting in
                    Section {
                        ForEach(app.members) { member in
                            Picker(selection: Binding(get: { model.answer(member: member.id, key: setting.key) },
                                                      set: { v in Task { await model.choose(v, member: member.id, key: setting.key) } })) {
                                ForEach(setting.choices) { Text($0.label).tag($0.value) }
                            } label: { MemberLabel(member: member) }
                        }
                    } header: { Text(setting.label) } footer: { Text(setting.description) }
                }
                Section {
                    ForEach(model.devices) { device in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(device.label ?? "Unnamed device")
                                if device.id == app.deviceID { Text("· this one").foregroundStyle(RallyDesign.color("inkMuted")) }
                            }
                            Text("\(device.answerCount) saved answer\(device.answerCount == 1 ? "" : "s") · last seen \(device.lastSeenAt.formatted(date: .abbreviated, time: .shortened))")
                                .font(.caption).foregroundStyle(RallyDesign.color("inkMuted"))
                        }
                        .swipeActions { Button("Forget", role: .destructive) { forgetting = device } }
                    }
                } header: { Text("Devices Rally remembers") } footer: {
                    Text("A browser or phone that clears its data comes back as a new device and leaves its answers behind. Forget one to drop it and everything saved for it.")
                }
            }
        }
        .navigationTitle("Personal Defaults")
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.load() }
        .confirmationDialog("Forget \(forgetting?.label ?? "this device")?", isPresented: Binding(get: { forgetting != nil }, set: { if !$0 { forgetting = nil } }),
                            titleVisibility: .visible) {
            Button("Forget", role: .destructive) { if let d = forgetting { Task { await model.forget(d) } } }
        } message: { Text("Its \(forgetting?.answerCount ?? 0) saved answers go with it.") }
        .alert("Couldn't do that", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("OK") {}
        } message: { Text(model.errorMessage ?? "") }
    }
}
