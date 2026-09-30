import RallyKit
import SwiftUI

/// Settings, as a hub. The top is this phone — its server, name and owner; everything
/// below is the household's, and lives on the server, so it is the same on every device.
struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var deviceName = ""
    @State private var showingServerSheet = false
    @State private var saveError: String?

    var body: some View {
        Form {
            serverSection
            deviceSection
            Section("Household") {
                if let client = model.client {
                    NavigationLink("Family Members") { FamilyMembersView(client: client) }.accessibilityIdentifier("settings-family")
                    NavigationLink("Personal Defaults") { PersonalDefaultsView(client: client, deviceID: model.deviceID) }.accessibilityIdentifier("settings-personal")
                    NavigationLink("Calendars") { CalendarsSettingsView(client: client) }.accessibilityIdentifier("settings-calendars")
                    NavigationLink("Household Settings") { HouseholdSettingsView(client: client) }.accessibilityIdentifier("settings-household")
                    NavigationLink("AI") { AISettingsView(client: client) }.accessibilityIdentifier("settings-ai")
                    NavigationLink("Sports Teams") { TeamsView(client: client) }.accessibilityIdentifier("settings-teams")
                }
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
                    ServerForm(initial: model.serverURL?.absoluteString ?? "", buttonTitle: "Test and save") { showingServerSheet = false }
                        .padding(RallyDesign.space[4])
                }
                .navigationTitle("Server")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { showingServerSheet = false } } }
            }
        }
    }

    private var serverSection: some View {
        Section("Server") {
            LabeledContent("Address", value: model.serverURL?.absoluteString ?? "—").accessibilityIdentifier("settings-server")
            LabeledContent("Status", value: statusText)
            Button("Change server…") { showingServerSheet = true }
            Button("Disconnect", role: .destructive) { model.disconnect() }
        }
    }

    private var deviceSection: some View {
        Section {
            TextField("This device", text: $deviceName).submitLabel(.done).onSubmit { Task { await saveName() } }
            Picker("Belongs to", selection: Binding(get: { model.memberID }, set: { model.bind(to: $0) })) {
                Text("Nobody yet").tag(Int?.none)
                ForEach(model.members) { member in Text(member.name).tag(Int?.some(member.id)) }
            }
            .accessibilityIdentifier("device-owner")
            if let saveError { Text(saveError).font(.footnote).foregroundStyle(RallyDesign.color("stateOverdue")) }
        } header: {
            Text("This device")
        } footer: {
            Text("Who's holding this phone. Personal settings are remembered per person on each device. The name only helps you recognize it in Rally's device list.")
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
        do { try await model.rename(device: name); saveError = nil } catch { saveError = error.localizedDescription }
    }
}
