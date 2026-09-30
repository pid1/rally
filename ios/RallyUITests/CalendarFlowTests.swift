import XCTest

/// The calendar against a `demo` server on localhost:8100 (throwaway data).
final class CalendarFlowTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a)
    }

    private func openCalendar() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap(); field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()
        XCTAssertTrue(app.tabBars.buttons["Calendar"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Calendar"].tap()
        XCTAssertTrue(app.buttons["calendar-add"].waitForExistence(timeout: 10))
        return app
    }

    private func choose(range: String, in app: XCUIApplication) {
        app.buttons["calendar-range"].tap()
        app.buttons[range].tap()
    }

    func testEveryViewAndRangeDraws() {
        let app = openCalendar()
        // `auto` on a phone is Calendar + Day, and the title says it is today.
        XCTAssertTrue(app.staticTexts["calendar-title"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["calendar-title"].label.contains("Today"))
        sleep(2)
        shot(app, "calendar-1-day")

        choose(range: "Week", in: app)
        XCTAssertFalse(app.staticTexts["calendar-title"].label.contains("Today"), "a span is not today")
        sleep(2)
        shot(app, "calendar-2-week")

        choose(range: "Month", in: app)
        sleep(2)
        shot(app, "calendar-3-month")
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'month-day-'")).firstMatch.exists)

        app.segmentedControls["calendar-mode"].buttons["Agenda"].tap()
        sleep(2)
        shot(app, "calendar-4-agenda-month")
    }

    func testAddAnEventAndOpenItsDetail() {
        let app = openCalendar()
        app.segmentedControls["calendar-mode"].buttons["Agenda"].tap()

        app.buttons["calendar-add"].tap()
        let title = app.textFields["event-title"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        title.tap(); title.typeText("Dentist run")
        // The demo family has several native calendars, so the owner must be chosen.
        let save = app.buttons["event-save"]
        if app.buttons["event-calendar"].exists {
            XCTAssertFalse(save.isEnabled, "Save waits until a calendar is chosen")
            app.buttons["event-calendar"].tap()
            // Push-style picker: pick Mom's calendar (the one hittable "Mom" on the pushed screen).
            let pushed = app.navigationBars["Whose calendar"]
            XCTAssertTrue(pushed.waitForExistence(timeout: 5), "the picker pushes a selection screen")
            // Calendars are listed under their owner's name; the filter chip behind the sheet
            // is also labelled "Mom", so target the calendar row itself.
            let row = app.buttons["Mom's Calendar"]
            XCTAssertTrue(row.waitForExistence(timeout: 5), "the pushed picker lists the owner's calendar")
            row.tap()
            // Some OS versions pop the pushed picker on selection; others need Back.
            if !save.waitForExistence(timeout: 3) { app.navigationBars.buttons.element(boundBy: 0).tap() }
            XCTAssertTrue(save.waitForExistence(timeout: 5))
            XCTAssertEqual(app.textFields["event-title"].value as? String, "Dentist run",
                           "coming back from the picker must not reset what was typed")
            XCTAssertTrue(save.isEnabled, "choosing a calendar unblocks Save")
        }
        shot(app, "calendar-5-editor")
        save.tap()

        let row = app.staticTexts["Dentist run"]
        XCTAssertTrue(row.waitForExistence(timeout: 10), "the new event is on today's agenda")
        row.tap()
        XCTAssertTrue(app.descendants(matching: .any)["detail-row-when"].waitForExistence(timeout: 5))
        shot(app, "calendar-6-detail")
    }
}
