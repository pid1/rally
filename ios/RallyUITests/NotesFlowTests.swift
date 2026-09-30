import XCTest

/// Notes against a `demo` server on localhost:8100 (today already has a note).
final class NotesFlowTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a)
    }

    func testAddingToADayThatHasANoteSwitchesToEditingIt() {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap(); field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()
        XCTAssertTrue(app.tabBars.buttons["More"].waitForExistence(timeout: 10))
        app.tabBars.buttons["More"].tap()
        app.buttons["more-notes"].tap()
        XCTAssertTrue(app.buttons["notes-add"].waitForExistence(timeout: 5))
        shot(app, "notes-1-list")

        app.buttons["notes-add"].tap()
        let body = app.textViews["note-body"]
        XCTAssertTrue(body.waitForExistence(timeout: 5))
        body.tap()
        body.typeText("Bring water")
        app.buttons["note-save"].tap()

        // Today already has a note, so the editor switches to it and says so.
        XCTAssertTrue(app.staticTexts["note-notice"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["note-notice"].label.contains("added to the end"))
        shot(app, "notes-2-switched")
        app.buttons["Cancel"].tap()

        // The link is the last row and rows are lazy, so scroll it into reach.
        let previous = app.buttons["notes-previous"]
        for _ in 0..<6 where !previous.exists || !previous.isHittable { app.swipeUp() }
        XCTAssertTrue(previous.exists, "link should be on the list")
        previous.tap()
        XCTAssertTrue(app.navigationBars["Previous Notes"].waitForExistence(timeout: 5))
        shot(app, "notes-3-previous")
    }
}
