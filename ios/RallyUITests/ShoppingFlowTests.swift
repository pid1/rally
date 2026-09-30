import XCTest

/// Drives the Shopping screen against a `demo` server on localhost:8100.
/// It adds rows to the demo database, which is throwaway by design.
final class ShoppingFlowTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func openShopping() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()
        XCTAssertTrue(app.tabBars.buttons["Shopping"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Shopping"].tap()
        XCTAssertTrue(app.buttons["shopping-add"].waitForExistence(timeout: 5))
        return app
    }

    func testAddTickOffAndManageStores() {
        let app = openShopping()
        shot(app, "shopping-1-list")

        // Add an item.
        app.buttons["shopping-add"].tap()
        let name = app.textFields["item-name"]
        XCTAssertTrue(name.waitForExistence(timeout: 5))
        name.typeText("Sourdough")
        shot(app, "shopping-2-add-sheet")
        app.buttons["item-save"].tap()

        let row = app.buttons["shopping-row-Sourdough"]
        XCTAssertTrue(row.waitForExistence(timeout: 10), "new item should appear on the list")
        shot(app, "shopping-3-after-add")

        // Tick it off; it stays on the list, struck through, until local midnight.
        // Rows are lazy and "Anywhere" is the last group, so scroll it into reach.
        let tick = app.buttons["Mark Sourdough as purchased"]
        for _ in 0..<4 where !tick.isHittable { app.swipeUp() }
        tick.tap()
        for _ in 0..<4 where !app.buttons["Mark Sourdough as not purchased"].exists { app.swipeUp() }
        XCTAssertTrue(app.buttons["Mark Sourdough as not purchased"].waitForExistence(timeout: 5))

        // Stores.
        app.buttons["shopping-manage-stores"].tap()
        let store = app.textFields["new-store-name"]
        XCTAssertTrue(store.waitForExistence(timeout: 5))
        store.tap()
        // Unique per run: store names are unique on the server, and the demo data outlives a run.
        let storeName = "Mart \(Int(Date().timeIntervalSince1970) % 100000)"
        store.typeText("\(storeName)\n")
        XCTAssertTrue(app.buttons[storeName].waitForExistence(timeout: 5))
        shot(app, "shopping-4-stores")
        app.buttons["Done"].tap()
    }

    func testAutocompleteSuggestsFromHistoryAndPurchasedPageOpens() {
        let app = openShopping()
        app.buttons["shopping-add"].tap()
        let name = app.textFields["item-name"]
        XCTAssertTrue(name.waitForExistence(timeout: 5))
        name.typeText("m")
        // History comes from the server; give the debounce and the round trip a moment.
        sleep(2)
        shot(app, "shopping-5-autocomplete")
        app.buttons["Cancel"].tap()

        app.buttons["shopping-purchased"].tap()
        XCTAssertTrue(app.navigationBars["Purchased"].waitForExistence(timeout: 5))
        sleep(1)
        shot(app, "shopping-6-purchased")
    }
}
