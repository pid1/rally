import XCTest

/// Drives Tasks against a `demo` server on localhost:8100 (throwaway data).
final class TasksFlowTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// List rows are lazy, so anything below the fold is not in the tree yet.
    private func scrollTo(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<6 where !element.exists || !element.isHittable { app.swipeUp() }
    }

    private func openTasks() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()
        XCTAssertTrue(app.tabBars.buttons["Tasks"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Tasks"].tap()
        XCTAssertTrue(app.buttons["tasks-add"].waitForExistence(timeout: 5))
        return app
    }

    func testAddAndCompleteATask() {
        let app = openTasks()
        shot(app, "tasks-1-list")

        app.buttons["tasks-add"].tap()
        app.buttons["tasks-add-task"].tap()
        let title = app.textFields["todo-title"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        title.tap()
        title.typeText("Water the ferns")
        shot(app, "tasks-2-add-sheet")
        app.buttons["todo-save"].tap()

        // An undated task sorts after the dated ones, so it lands below the fold.
        let tick = app.buttons["Mark Water the ferns as done"]
        scrollTo(tick, in: app)
        XCTAssertTrue(tick.exists, "new task should be on the list")
        tick.tap()
        let undo = app.buttons["Mark Water the ferns as not done"]
        scrollTo(undo, in: app)
        XCTAssertTrue(undo.waitForExistence(timeout: 5))
        shot(app, "tasks-3-after-complete")
    }

    func testRecurringEditorReadsTheRuleBackAsDates() {
        let app = openTasks()
        app.buttons["tasks-add"].tap()
        app.buttons["Add Recurring Task"].tap()
        let title = app.textFields["recurring-title"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        title.tap()
        title.typeText("Check smoke alarms")
        XCTAssertTrue(app.staticTexts["recurring-preview"].waitForExistence(timeout: 10),
                      "the server should answer with the first date")
        shot(app, "tasks-4-recurring")
        app.buttons["Cancel"].tap()

        let completed = app.buttons["tasks-completed"]
        scrollTo(completed, in: app)
        completed.tap()
        XCTAssertTrue(app.navigationBars["Completed"].waitForExistence(timeout: 5))
        shot(app, "tasks-5-completed")
    }
}
