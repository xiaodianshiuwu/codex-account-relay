// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CodexRelayDesktop",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "CodexRelay", targets: ["CodexRelayApp"])
    ],
    targets: [
        .target(
            name: "CodexRelayCore",
            path: "Sources/CodexRelayCore"
        ),
        .executableTarget(
            name: "CodexRelayApp",
            dependencies: ["CodexRelayCore"],
            path: "Sources/CodexRelayApp"
        ),
        .testTarget(
            name: "CodexRelayCoreTests",
            dependencies: ["CodexRelayCore"],
            path: "Tests/CodexRelayCoreTests"
        )
    ],
    swiftLanguageModes: [.v5]
)
