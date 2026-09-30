import RallyKit
import SwiftUI

@main
struct RallyApp: App {
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .fontDesign(.serif)
                .tint(RallyDesign.color("ink"))
                .task { await model.refresh() }
                .onChange(of: scenePhase) { _, phase in
                    // A phone that slept on another network comes back to a
                    // different answer, so ask again rather than trusting the last.
                    if phase == .active { Task { await model.refresh() } }
                }
        }
    }
}
