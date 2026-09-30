// swift-tools-version: 6.0
import PackageDescription

// Everything in the app that is not a screen lives here, so it can be tested
// with `swift test` on the Mac — no simulator, no Xcode project.
let package = Package(
    name: "RallyKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "RallyKit", targets: ["RallyKit"])],
    targets: [
        .target(name: "RallyKit"),
        .testTarget(
            name: "RallyKitTests",
            dependencies: ["RallyKit"]
        ),
    ]
)
