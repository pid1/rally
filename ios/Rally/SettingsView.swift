import RallyKit
import SwiftUI

/// Phase 2's Settings: this phone's server, name and owner. Family members,
/// calendars, notifications and the rest come with their screens.
struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var deviceName = ""
    @State private var showingServerSheet = false
    @State private var saveError: String?

    var body: some View {
        Form {
            Section("Server") {
                LabeledContent("Address", value: model.serverURL?.absoluteString ?? "—")
                    .accessibilityIdentifier("settings-server")
                LabeledContent("Status", value: statusText)
                Button("Change server…") { showingServerSheet = true }
                Button("Disconnect", role: .destructive) { model.disconnect() }
            }

            Section {
                TextField("This device", text: $deviceName)
                    .submitLabel(.done)
                    .onSubmit { Task { await saveName() } }
                Picker("Belongs to", selection: Binding(
                    get: { model.memberID },
                    set: { model.bind(to: $0) }
                )) {
                    Text("Nobody yet").tag(Int?.none)
                    ForEach(model.members) { member in
                        Label {
                            Text(member.name)
                        } icon: {
                            Circle().fill(RallyDesign.memberColor(hex: member.color)).frame(width: 10, height: 10)
                        }
                        .tag(Int?.some(member.id))
                    }
                }
                .accessibilityIdentifier("device-owner")
                if let saveError { Text(saveError).font(.footnote).foregroundStyle(RallyDesign.color("stateOverdue")) }
            } header: {
                Text("This device")
            } footer: {
                Text("Who's holding this phone. Personal settings are remembered per person on each device. The name only helps you recognize it in Rally's device list.")
            }
        }
        .navigationTitle("Settings")
        .task {
            await model.refresh()
            deviceName = await model.currentDeviceLabel() ?? model.deviceLabel
        }
        .sheet(isPresented: $showingServerSheet) {
            NavigationStack {
                ScrollView {
                    ServerForm(initial: model.serverURL?.absoluteString ?? "", buttonTitle: "Test and save") {
                        showingServerSheet = false
                    }
                    .padding(RallyDesign.space[4])
                }
                .navigationTitle("Server")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { showingServerSheet = false } } }
            }
        }
    }

    private var statusText: String {
        switch model.connection {
        case .connected: "Connected"
        case .unreachable: "Can't reach server"
        case .notRally: "Not a Rally server"
        case .failed: "Error"
        case nil: "Checking…"
        }
    }

    private func saveName() async {
        let name = deviceName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        do { try await model.rename(device: name); saveError = nil }
        catch { saveError = error.localizedDescription }
    }
}
