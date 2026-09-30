import XCTest

/// Drives the real app against a Rally server on the Mac. Start one first:
/// `demo` (port 8100). The simulator shares the Mac's network, so the server
/// is `localhost`.
final class OnboardingFlowTests: XCTestCase {
    override func setUp() {
        continueAfterFailure = false
    }

    private func launch() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        return app
    }

    func testConnectsAndShowsTheTabs() {
        let app = launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()

        XCTAssertTrue(app.tabBars.buttons["Dashboard"].waitForExistence(timeout: 10))
        for tab in ["Tasks", "Shopping", "Calendar", "More"] {
            XCTAssertTrue(app.tabBars.buttons[tab].exists, "missing \(tab) tab")
        }
    }

    func testAWrongAddressSaysSoAndStaysOnOnboarding() {
        let app = launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText("localhost:9")
        app.buttons["server-connect"].tap()

        XCTAssertTrue(app.staticTexts["server-message"].waitForExistence(timeout: 15))
        XCTAssertFalse(app.tabBars.firstMatch.exists)
    }

    func testSettingsShowsTheServerAndLetsYouPickAnOwner() {
        let app = launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()

        app.tabBars.buttons["More"].tap()
        app.buttons["settings-link"].tap()
        XCTAssertTrue(app.staticTexts["settings-server"].waitForExistence(timeout: 5)
                      || app.otherElements["settings-server"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["device-owner"].waitForExistence(timeout: 5))
    }
}

extension OnboardingFlowTests {
    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// Walks every screen and keeps a screenshot of each in the result bundle.
    func testTour() {
        let app = launch()
        shot(app, "1-onboarding")

        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText("localhost:8100")
        shot(app, "2-address-typed")
        app.buttons["server-connect"].tap()

        XCTAssertTrue(app.tabBars.buttons["Dashboard"].waitForExistence(timeout: 10))
        shot(app, "3-dashboard-tab")

        app.tabBars.buttons["More"].tap()
        shot(app, "4-more")
        app.buttons["settings-link"].tap()
        XCTAssertTrue(app.buttons["device-owner"].waitForExistence(timeout: 5))
        shot(app, "5-settings")

        app.buttons["device-owner"].tap()
        shot(app, "6-pick-owner")
    }
}

extension OnboardingFlowTests {
    /// The landing tab shows today from the snapshot, and the note live.
    func testDashboardShowsTodayAndTheNote() {
        let app = launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()
        XCTAssertTrue(app.staticTexts["dashboard-greeting"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Dentist — Emma"].exists || app.staticTexts["Dentist — Emma"].waitForExistence(timeout: 5))
        shot(app, "dashboard-1")
        app.swipeUp()
        shot(app, "dashboard-2")
    }
}
