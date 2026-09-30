import XCTest

/// Meal Planner against a `demo` server on localhost:8100 (throwaway data).
final class MealsFlowTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    private func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a)
    }

    private func scrollTo(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<8 where !element.exists || !element.isHittable { app.swipeUp() }
    }

    private func openMeals() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["-rally-reset"]
        app.launch()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap(); field.typeText("localhost:8100")
        app.buttons["server-connect"].tap()
        XCTAssertTrue(app.tabBars.buttons["More"].waitForExistence(timeout: 10))
        app.tabBars.buttons["More"].tap()
        app.buttons["more-meals"].tap()
        XCTAssertTrue(app.buttons["meals-add"].waitForExistence(timeout: 5))
        return app
    }

    func testPlanAMealAndRateAnEarlierOne() {
        let app = openMeals()
        shot(app, "meals-1-planner")

        app.buttons["meals-add"].tap()
        let plan = app.textViews["meal-plan"].exists ? app.textViews["meal-plan"] : app.textFields["meal-plan"]
        XCTAssertTrue(plan.waitForExistence(timeout: 5))
        plan.tap()
        plan.typeText("Lemon pasta")
        shot(app, "meals-2-add")
        app.buttons["meal-save"].tap()
        let row = app.buttons["meal-row-Lemon pasta"]
        scrollTo(row, in: app)
        XCTAssertTrue(row.exists, "new meal should be on the planner")

        let previous = app.buttons["meals-previous"]
        scrollTo(previous, in: app)
        previous.tap()
        XCTAssertTrue(app.navigationBars["Previous Meals"].waitForExistence(timeout: 5))
        let first = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH 'prev-meal-'")).firstMatch
        XCTAssertTrue(first.waitForExistence(timeout: 10))
        shot(app, "meals-3-previous")
        first.tap()
        let star = app.buttons["star-4"]
        XCTAssertTrue(star.waitForExistence(timeout: 5))
        star.tap()
        shot(app, "meals-4-review")
        app.buttons["review-save"].tap()
        XCTAssertTrue(app.navigationBars["Previous Meals"].waitForExistence(timeout: 10))
    }
}
