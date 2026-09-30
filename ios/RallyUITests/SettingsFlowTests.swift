import XCTest

/// Settings against a `demo` server on localhost:8100 (throwaway data).
final class SettingsFlowTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a)
    }

    private func openSettings() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap(); field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()
        XCTAssertTrue(app.tabBars.buttons["More"].waitForExistence(timeout: 10))
        app.tabBars.buttons["More"].tap()
        app.buttons["settings-link"].tap()
        XCTAssertTrue(app.buttons["settings-family"].waitForExistence(timeout: 5))
        return app
    }

    /// Choosing where the calendar opens for a person on this device, then opening the calendar.
    func testPersonalDefaultsDecideTheCalendarLandingView() {
        let app = openSettings()
        shot(app, "settings-1-hub")

        // This phone is Mom's.
        app.buttons["device-owner"].tap()
        app.buttons["Mom"].tap()

        app.buttons["settings-personal"].tap()
        XCTAssertTrue(app.navigationBars["Personal Defaults"].waitForExistence(timeout: 5))
        shot(app, "settings-2-personal")
        // The first picker row is Dad (alphabetical); find Mom's row by its label.
        let momRow = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Mom'")).firstMatch
        XCTAssertTrue(momRow.waitForExistence(timeout: 5))
        momRow.tap()
        let choice = app.buttons["Agenda · Week"]
        XCTAssertTrue(choice.waitForExistence(timeout: 5))
        choice.tap()
        sleep(1)

        app.terminate()
        let again = XCUIApplication()   // a fresh launch keeps the server and the binding
        again.launch()
        XCTAssertTrue(again.tabBars.buttons["Calendar"].waitForExistence(timeout: 10))
        again.tabBars.buttons["Calendar"].tap()
        let agenda = again.segmentedControls["calendar-mode"].buttons["Agenda"]
        XCTAssertTrue(agenda.waitForExistence(timeout: 10))
        XCTAssertTrue(agenda.isSelected, "Mom's phone opens the calendar on Agenda")
        XCTAssertFalse(again.staticTexts["calendar-title"].label.contains("Today"), "Week names a span")
        shot(again, "settings-3-landing")
    }

    func testAddAFamilyMemberAndFlipAHouseholdSetting() {
        let app = openSettings()
        app.buttons["settings-family"].tap()
        app.navigationBars.buttons["Add member"].tap()
        let name = app.textFields["member-name"]
        XCTAssertTrue(name.waitForExistence(timeout: 5))
        name.tap()
        let unique = "Kid\(Int(Date().timeIntervalSince1970) % 10000)"
        name.typeText(unique)
        shot(app, "settings-4-member")
        app.buttons["member-save"].tap()
        XCTAssertTrue(app.buttons["member-row-\(unique)"].waitForExistence(timeout: 10), "the new member is listed")

        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.buttons["settings-household"].tap()
        XCTAssertTrue(app.navigationBars["Household"].waitForExistence(timeout: 5))
        let save = app.buttons["household-save"]
        XCTAssertFalse(save.isEnabled, "nothing changed yet")
        let stem = app.switches["STEM concept of the day"]
        for _ in 0..<6 where !stem.exists || !stem.isHittable { app.swipeUp() }
        // The switch sits at the row's trailing edge; the row's centre is its label.
        stem.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
        sleep(1)
        XCTAssertTrue(save.isEnabled, "a change enables Save")
        save.tap()
        sleep(1)
        XCTAssertFalse(save.isEnabled, "saved changes are no longer pending")
        shot(app, "settings-5-household")
        stem.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap(); sleep(1); save.tap() // put it back
    }
}
