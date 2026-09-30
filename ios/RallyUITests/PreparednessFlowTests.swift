import XCTest

/// Preparedness against a `demo` server on localhost:8100 (throwaway data).
final class PreparednessFlowTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a)
    }

    private func scrollTo(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<8 where !element.exists || !element.isHittable { app.swipeUp() }
    }

    func testAddAnItemRefreshOneAndOpenTheGoList() {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap(); field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()
        XCTAssertTrue(app.tabBars.buttons["More"].waitForExistence(timeout: 10))
        app.tabBars.buttons["More"].tap()
        app.buttons["more-prep"].tap()
        XCTAssertTrue(app.buttons["prep-add"].waitForExistence(timeout: 10))
        sleep(2)
        shot(app, "prep-1-list")
        // Refresh the first scheduled item: it should survive the round trip.
        let refreshed = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Mark ' AND label ENDSWITH ' refreshed'")).firstMatch
        XCTAssertTrue(refreshed.waitForExistence(timeout: 5), "demo data has scheduled items")
        refreshed.tap()
        sleep(1)

        app.buttons["prep-add"].tap()
        let name = app.textFields["prep-name"]
        XCTAssertTrue(name.waitForExistence(timeout: 5))
        name.tap(); name.typeText("Headlamps")
        shot(app, "prep-2-add")
        app.buttons["prep-save"].tap()
        XCTAssertTrue(app.buttons["prep-add"].waitForExistence(timeout: 10))
        let row = app.buttons["prep-row-Headlamps"]
        scrollTo(row, in: app)
        XCTAssertTrue(row.exists, "new item should be on the list")

        let locations = app.buttons["prep-manage-locations"]
        scrollTo(locations, in: app)
        XCTAssertTrue(locations.exists, "Locations sits in the bottom section")
        let goList = app.buttons["prep-golist"]
        scrollTo(goList, in: app)
        goList.tap()
        XCTAssertTrue(app.navigationBars["Go List"].waitForExistence(timeout: 5))
        sleep(1)
        shot(app, "prep-3-golist")
    }
}
