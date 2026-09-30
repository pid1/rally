import RallyKit
import SwiftUI

/// Rally's sections. Five tabs is what a phone holds, and there are eight
/// sections, so the three least-reached ones live under More — the same
/// order the web sidebar lists them in, with Settings last.
struct MainTabView: View {
    var body: some View {
        TabView {
            Tab("Dashboard", systemImage: "sun.max") {
                NavigationStack { PlaceholderScreen(title: "Dashboard") }
            }
            Tab("Tasks", systemImage: "checklist") {
                NavigationStack { PlaceholderScreen(title: "Tasks") }
            }
            Tab("Shopping", systemImage: "cart") {
                NavigationStack { PlaceholderScreen(title: "Shopping") }
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
    var body: some View {
        NavigationStack {
            List {
                NavigationLink("Notes") { PlaceholderScreen(title: "Notes") }
                NavigationLink("Meal Planner") { PlaceholderScreen(title: "Meal Planner") }
                NavigationLink("Preparedness") { PlaceholderScreen(title: "Preparedness") }
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
