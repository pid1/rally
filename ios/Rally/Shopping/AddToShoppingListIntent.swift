import AppIntents
import RallyKit

/// "Add to shopping list in Rally" — Siri asks what, then adds it. Replaces the
/// Shortcuts recipe in docs/voice-shortcuts.md with something that needs no setup.
struct AddToShoppingListIntent: AppIntent {
    static let title: LocalizedStringResource = "Add to Shopping List"
    static let description = IntentDescription("Adds an item to your family's Rally shopping list.")

    @Parameter(title: "Item", requestValueDialog: "What should I add?")
    var item: String

    @Parameter(title: "Store", description: "A store name; anything Rally doesn't know goes under Anywhere.")
    var store: String?

    static var parameterSummary: some ParameterSummary {
        Summary("Add \(\.$item) to the shopping list") { \.$store }
    }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let url = LocalSettings().serverURL else {
            return .result(dialog: "Open Rally and set your server first.")
        }
        let name = item.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return .result(dialog: "I didn't catch an item to add.") }
        do {
            let created = try await APIClient(baseURL: url).createShoppingItem(name: name, note: nil, storeName: store)
            return .result(dialog: "Added \(created.name) to your shopping list.")
        } catch {
            return .result(dialog: "Couldn't reach Rally. \(error.localizedDescription)")
        }
    }
}

struct RallyShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AddToShoppingListIntent(),
            phrases: [
                "Add to my shopping list in \(.applicationName)",
                "Add something to \(.applicationName) shopping list",
            ],
            shortTitle: "Add to Shopping List",
            systemImageName: "cart.badge.plus")
    }
}
