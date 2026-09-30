import XCTest

/// Captures the screenshots used in the pull request and docs, against a freshly seeded `demo`
/// server (`demo` on port 8100). Not an assertion suite — `CalendarFlowTests` and friends are —
/// but it does fail if a screen cannot be reached, so the set is never silently short.
final class ScreenshotTourTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func shot(_ app: XCUIApplication, _ name: String) {
        sleep(2) // let lists settle and the grid scroll to its opening hour
        let a = XCTAttachment(screenshot: app.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a)
    }

    private func scrollTo(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<8 where !element.exists || !element.isHittable { app.swipeUp() }
    }

    func testCaptureTheTour() {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        shot(app, "00-onboarding")
        field.tap(); field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()

        XCTAssertTrue(app.staticTexts["dashboard-greeting"].waitForExistence(timeout: 10))
        shot(app, "01-dashboard")

        app.tabBars.buttons["Tasks"].tap()
        XCTAssertTrue(app.buttons["tasks-add"].waitForExistence(timeout: 5))
        shot(app, "02-tasks")

        app.tabBars.buttons["Shopping"].tap()
        XCTAssertTrue(app.buttons["shopping-add"].waitForExistence(timeout: 5))
        shot(app, "03-shopping")
        app.buttons["shopping-add"].tap()
        let item = app.textFields["item-name"]
        XCTAssertTrue(item.waitForExistence(timeout: 5))
        item.typeText("m")
        shot(app, "04-shopping-autocomplete")
        app.buttons["Cancel"].tap()

        app.tabBars.buttons["Calendar"].tap()
        XCTAssertTrue(app.buttons["calendar-add"].waitForExistence(timeout: 5))
        app.buttons["calendar-range"].tap(); app.buttons["Week"].tap()
        sleep(2)
        shot(app, "05-calendar-week")
        app.buttons["calendar-range"].tap(); app.buttons["Month"].tap()
        shot(app, "06-calendar-month")
        app.segmentedControls["calendar-mode"].buttons["Agenda"].tap()
        shot(app, "07-calendar-agenda")

        app.tabBars.buttons["More"].tap()
        app.buttons["more-meals"].tap()
        XCTAssertTrue(app.buttons["meals-add"].waitForExistence(timeout: 5))
        shot(app, "08-meal-planner")
        let previous = app.buttons["meals-previous"]
        scrollTo(previous, in: app)
        previous.tap()
        XCTAssertTrue(app.navigationBars["Previous Meals"].waitForExistence(timeout: 5))
        shot(app, "09-previous-meals")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()

        app.buttons["more-prep"].tap()
        XCTAssertTrue(app.buttons["prep-add"].waitForExistence(timeout: 10))
        shot(app, "10-preparedness")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        app.buttons["more-notes"].tap()
        XCTAssertTrue(app.buttons["notes-add"].waitForExistence(timeout: 5))
        shot(app, "11-notes")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        app.buttons["settings-link"].tap()
        XCTAssertTrue(app.buttons["settings-family"].waitForExistence(timeout: 5))
        shot(app, "12-settings")
    }
}
