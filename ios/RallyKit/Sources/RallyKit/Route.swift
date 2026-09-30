import Foundation

public enum HTTPMethod: String, Sendable { case get = "GET", post = "POST", put = "PUT", delete = "DELETE" }

/// Every endpoint the app calls, by the path template the server declares.
///
/// Routes are an enum rather than string literals at call sites so that
/// `SpecCoverageTests` can check each one against `ios/openapi.json`: a
/// renamed endpoint then fails a test here instead of a screen in the app.
public enum Route: String, CaseIterable, Sendable {
    // Family, devices, dashboard
    case listFamily
    case listDevices
    case announceDevice
    case forgetDevice
    case devicePreferences
    case saveMemberPreferences
    case preferenceCatalog
    case dashboard
    // Tasks
    case todos
    case createTodo
    case updateTodo
    case deleteTodo
    case completedTodos
    case recurringTodos
    case createRecurringTodo
    case updateRecurringTodo
    case deleteRecurringTodo
    case previewRecurrence
    // Notes
    case notes
    case createNote
    case updateNote
    case deleteNote
    case previousNotes
    // Shopping
    case shoppingStores
    case createShoppingStore
    case updateShoppingStore
    case deleteShoppingStore
    case shoppingItems
    case createShoppingItem
    case updateShoppingItem
    case deleteShoppingItem
    case reorderShoppingItems
    case shoppingPurchased
    case shoppingSuggestions
    case forgetShoppingSuggestion

    private var spec: (HTTPMethod, String) {
        switch self {
        case .listFamily: (.get, "/api/family")
        case .listDevices: (.get, "/api/devices")
        case .announceDevice: (.put, "/api/devices/{device_id}")
        case .forgetDevice: (.delete, "/api/devices/{device_id}")
        case .devicePreferences: (.get, "/api/devices/{device_id}/preferences")
        case .saveMemberPreferences: (.put, "/api/devices/{device_id}/preferences/{member_id}")
        case .preferenceCatalog: (.get, "/api/preferences/catalog")
        case .dashboard: (.get, "/api/dashboard")
        case .todos: (.get, "/api/todos")
        case .createTodo: (.post, "/api/todos")
        case .updateTodo: (.put, "/api/todos/{todo_id}")
        case .deleteTodo: (.delete, "/api/todos/{todo_id}")
        case .completedTodos: (.get, "/api/todos/completed")
        case .recurringTodos: (.get, "/api/recurring-todos")
        case .createRecurringTodo: (.post, "/api/recurring-todos")
        case .updateRecurringTodo: (.put, "/api/recurring-todos/{rt_id}")
        case .deleteRecurringTodo: (.delete, "/api/recurring-todos/{rt_id}")
        case .previewRecurrence: (.post, "/api/recurring-todos/preview")
        case .notes: (.get, "/api/notes")
        case .createNote: (.post, "/api/notes")
        case .updateNote: (.put, "/api/notes/{note_id}")
        case .deleteNote: (.delete, "/api/notes/{note_id}")
        case .previousNotes: (.get, "/api/notes/previous")
        case .shoppingStores: (.get, "/api/shopping/stores")
        case .createShoppingStore: (.post, "/api/shopping/stores")
        case .updateShoppingStore: (.put, "/api/shopping/stores/{store_id}")
        case .deleteShoppingStore: (.delete, "/api/shopping/stores/{store_id}")
        case .shoppingItems: (.get, "/api/shopping/items")
        case .createShoppingItem: (.post, "/api/shopping/items")
        case .updateShoppingItem: (.put, "/api/shopping/items/{item_id}")
        case .deleteShoppingItem: (.delete, "/api/shopping/items/{item_id}")
        case .reorderShoppingItems: (.post, "/api/shopping/items/reorder")
        case .shoppingPurchased: (.get, "/api/shopping/purchased")
        case .shoppingSuggestions: (.get, "/api/shopping/suggestions")
        case .forgetShoppingSuggestion: (.delete, "/api/shopping/suggestions/{history_id}")
        }
    }

    public var method: HTTPMethod { spec.0 }
    public var template: String { spec.1 }
}
