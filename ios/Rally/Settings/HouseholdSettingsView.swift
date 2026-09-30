import RallyKit
import SwiftUI

/// The install-wide settings. Shared across every device, and saved together: only
/// what changed is sent, so flipping one toggle never rewrites the rest.
struct HouseholdSettingsView: View {
    @Environment(AppModel.self) private var app
    @State private var model: HouseholdModel
    @State private var weatherResult: String?
    @State private var pushResult: String?
    @State private var working = false

    init(client: APIClient) { _model = State(initialValue: HouseholdModel(client: client)) }

    var body: some View {
        Form {
            do {
                general(model)
                home(model)
                weather(model)
                meals(model)
                notifications(model)
                toggles(model)
                shopping(model)
                preparedness(model)
                sports(model)
            }
        }
        .navigationTitle("Household")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") { Task { if await model.save() { await app.refresh() } } }
                    .disabled(!model.hasChanges).accessibilityIdentifier("household-save")
            }
        }
        .task { await model.load() }
        .alert("Couldn't do that", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("OK") {}
        } message: { Text(model.errorMessage ?? "") }
    }

    // MARK: Sections

    private func general(_ m: HouseholdModel) -> some View {
        Section {
            Picker("Timezone", selection: text(m, "local_timezone")) {
                Text("Choose…").tag("")
                ForEach(TimeZone.knownTimeZoneIdentifiers.sorted(), id: \.self) { Text($0.replacingOccurrences(of: "_", with: " ")).tag($0) }
            }
            .pickerStyle(.navigationLink)
        } header: { Text("General") } footer: {
            Text("Every time written without a zone is read in this one, and \"today\" means today here — not wherever a phone happens to be.")
        }
    }

    private func home(_ m: HouseholdModel) -> some View {
        Section {
            TextField("Highland Village, TX", text: text(m, "home_location"))
        } header: { Text("Home") } footer: { Text("Sent to the AI as where the family lives, so advice can be local.") }
    }

    private func weather(_ m: HouseholdModel) -> some View {
        Section {
            TextField("NWS forecast URL", text: text(m, "weather_nws_url"), axis: .vertical)
                .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
            Button(working ? "Checking…" : "Save and test") { Task { working = true; weatherResult = await m.saveThenTest { try await app.client!.testWeather() }; working = false } }
                .disabled(working)
            if let weatherResult { Text(weatherResult).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
        } header: { Text("Weather") } footer: {
            Text("Search your location at forecast.weather.gov, copy the page URL and append &FcstType=dwml.")
        }
    }

    private func meals(_ m: HouseholdModel) -> some View {
        Section("Meal Planner") {
            Picker("New meals default to", selection: text(m, "meal_default_type")) {
                ForEach(MealLogic.mealTypes, id: \.self) { Text($0).tag($0) }
            }
        }
    }

    private func notifications(_ m: HouseholdModel) -> some View {
        Section {
            SecureField("Pushover application token", text: text(m, "pushover_app_token"))
                .textInputAutocapitalization(.never).autocorrectionDisabled()
            Button(working ? "Sending…" : "Save and send a test") {
                Task { working = true; pushResult = await m.saveThenTest { try await app.client!.testPushover() }; working = false }
            }
            .disabled(working)
            if let pushResult { Text(pushResult).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
            NavigationLink("What Rally sends") { NotificationsOverviewView() }
        } header: { Text("Notifications") } footer: {
            Text("The token identifies this install; each person's own key (under Family Members) identifies them.")
        }
    }

    private func toggles(_ m: HouseholdModel) -> some View {
        Group {
            Section {
                Toggle("Notify on assignment", isOn: flag(m, "todo_notify_enabled"))
            } header: { Text("Tasks") } footer: { Text("Pushes a task to its assignee when it is created or handed to somebody new.") }
            Section {
                Toggle("STEM concept of the day", isOn: flag(m, "stem_concept_enabled"))
            } header: { Text("Learning") } footer: { Text("Adds an age-appropriate idea to each morning's summary.") }
        }
    }

    private func shopping(_ m: HouseholdModel) -> some View {
        Section("Shopping List") {
            Toggle("Include the list in the daily summary", isOn: flag(m, "shopping_list_in_summary_enabled"))
            Toggle("Notify when items are added", isOn: flag(m, "shopping_notify_enabled"))
            if m.flag("shopping_notify_enabled") {
                TextField("Wait for adding to stop (minutes)", text: text(m, "shopping_notify_settle_minutes")).keyboardType(.numberPad)
            }
        }
    }

    private func preparedness(_ m: HouseholdModel) -> some View {
        Section("Preparedness") {
            Toggle("Daily refresh digest", isOn: flag(m, "prep_notify_enabled"))
            if m.flag("prep_notify_enabled") {
                DatePicker("Send at", selection: time(m, "prep_notify_time"), displayedComponents: .hourAndMinute)
            }
            TextField("Default reminder lead (days)", text: text(m, "prep_default_remind_days")).keyboardType(.numberPad)
            Toggle("Mention overdue stock in the summary", isOn: flag(m, "prep_overdue_in_summary_enabled"))
            Toggle("Offer the AI review", isOn: flag(m, "prep_review_enabled"))
        }
    }

    private func sports(_ m: HouseholdModel) -> some View {
        Section {
            Toggle("Sports watchlist in the summary", isOn: flag(m, "sports_watchlist_enabled"))
            if let client = app.client { NavigationLink("Followed teams") { TeamsView(client: client) } }
        } header: { Text("Sports") } footer: { Text("Tonight's games and notable upcoming events for the teams you follow.") }
    }

    // MARK: Bindings

    private func text(_ m: HouseholdModel, _ key: String) -> Binding<String> {
        Binding(get: { m.value(key) }, set: { m.set(key, $0) })
    }

    private func flag(_ m: HouseholdModel, _ key: String) -> Binding<Bool> {
        Binding(get: { m.flag(key) }, set: { m.setFlag(key, $0) })
    }

    /// `HH:mm` as a time-of-day the picker can drive.
    private func time(_ m: HouseholdModel, _ key: String) -> Binding<Date> {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; f.locale = Locale(identifier: "en_US_POSIX")
        return Binding(get: { f.date(from: m.value(key)) ?? f.date(from: "08:00")! }, set: { m.set(key, f.string(from: $0)) })
    }
}

/// What Rally sends, and who currently hears it — read-only: the editor for a preference
/// is the person's own record. The first gate is the application token; with none, nothing sends.
struct NotificationsOverviewView: View {
    @Environment(AppModel.self) private var app
    @State private var overview: NotificationOverview?

    var body: some View {
        List {
            if let overview {
                if !overview.tokenConfigured {
                    Section { Label("No Pushover application token is set, so nothing sends at all.", systemImage: "exclamationmark.triangle").font(.footnote) }
                }
                ForEach(overview.kinds) { kind in
                    Section {
                        Text(kind.audience).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                        if !kind.enabled { Text("Switched off for the whole household").font(.footnote).foregroundStyle(RallyDesign.color("stateDue")) }
                        names("Hearing it", kind.receiving)
                        names("Turned it off", kind.muted)
                        names("No Pushover key", kind.noKey)
                    } header: { Text(kind.label) }
                }
            } else { ProgressView() }
        }
        .navigationTitle("What Rally sends")
        .navigationBarTitleDisplayMode(.inline)
        .task { overview = try? await app.client?.notificationsOverview() }
    }

    @ViewBuilder private func names(_ label: String, _ list: [String]) -> some View {
        if !list.isEmpty { LabeledContent(label, value: list.joined(separator: ", ")) }
    }
}
