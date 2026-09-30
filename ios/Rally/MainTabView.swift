import RallyKit
import SwiftUI

/// Rally's sections. Five tabs is what a phone holds, and there are eight
/// sections, so the three least-reached ones live under More — the same
/// order the web sidebar lists them in, with Settings last.
struct MainTabView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        TabView {
            Tab("Dashboard", systemImage: "sun.max") {
                NavigationStack {
                    if let client = app.client { DashboardView(client: client) }
                }
            }
            Tab("Tasks", systemImage: "checklist") {
                NavigationStack {
                    if let client = app.client { TasksView(client: client) }
                }
            }
            Tab("Shopping", systemImage: "cart") {
                NavigationStack {
                    if let client = app.client { ShoppingView(client: client) }
                }
            }
            Tab("Calendar", systemImage: "calendar") {
                NavigationStack { PlaceholderScreen(title: "Calendar") }
            }
            Tab("More", systemImage: "ellipsis") {
                MoreView()
            }
        }
    }
}

struct MoreView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        NavigationStack {
            List {
                NavigationLink("Notes") {
                    if let client = app.client { NotesView(client: client) }
                }
                .accessibilityIdentifier("more-notes")
                NavigationLink("Meal Planner") {
                    if let client = app.client { MealPlannerView(client: client) }
                }
                .accessibilityIdentifier("more-meals")
                NavigationLink("Preparedness") {
                    if let client = app.client { PreparednessView(client: client) }
                }
                .accessibilityIdentifier("more-prep")
                Section {
                    NavigationLink("Settings") { SettingsView() }
                        .accessibilityIdentifier("settings-link")
                }
            }
            .navigationTitle("More")
        }
    }
}

/// Stands in for a screen that has not been built yet, so the navigation and
/// the connection handling can be exercised end to end first.
struct PlaceholderScreen: View {
    let title: String

    var body: some View {
        ContentUnavailableView(title, systemImage: "hammer", description: Text("Coming soon."))
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.large)
    }
}
