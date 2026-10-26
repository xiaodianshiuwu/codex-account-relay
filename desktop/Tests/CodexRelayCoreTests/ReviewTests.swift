import XCTest
@testable import CodexRelayCore

final class ReviewTests: XCTestCase {
    func temporaryDirectory() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: dir) }
        return dir
    }
    func testSharedSettingsKeepIntervalStrategyAndUnknownFields() throws {
        let file = try temporaryDirectory().appendingPathComponent("auto.json")
        try AutoSettingsStore.update(["enabled": true, "interval": 1234.0, "strategy":"next", "future":"keep"], at: file)
        try AutoSettingsStore.update(["prompt": String(repeating:"x", count:5000)], at: file)
        let settings = AutoSettingsStore.load(at:file)
        XCTAssertEqual(settings["interval"] as? Double,1234)
        XCTAssertEqual(settings["strategy"] as? String,"next")
        XCTAssertEqual(settings["future"] as? String,"keep")
        XCTAssertEqual((settings["prompt"] as? String)?.count,5000)
        XCTAssertNil(settings["allow_chatgpt_restart"])
        XCTAssertThrowsError(try AutoSettingsStore.update(["interval": -1], at:file))
    }
    func testRunnerDoesNotWaitForInheritedPipesInDetachedChild() throws {
        let start = Date()
        let result = try ProcessRunner.run(executable:"/bin/sh", arguments:["-c", "sleep 2 & echo complete"], timeout:0.5)
        XCTAssertTrue(result.stdout.contains("complete"))
        XCTAssertLessThan(Date().timeIntervalSince(start),1.5)
    }
    func testTimeoutDoesNotWaitForDetachedDescendant() throws {
        let start = Date()
        XCTAssertThrowsError(try ProcessRunner.run(executable:"/bin/sh", arguments:["-c", "sleep 3 & wait"], timeout:0.1))
        XCTAssertLessThan(Date().timeIntervalSince(start),1.5)
    }
    func testOnlyProgressErrorNeverFallsBackToRawJSON() throws {
        let script = try temporaryDirectory().appendingPathComponent("backend")
        let source = """
        #!/bin/sh
        echo '{"event":"progress","message":"test"}' >&2
        exit 1
        """
        try source.write(to:script, atomically:true, encoding:.utf8)
        try FileManager.default.setAttributes([.posixPermissions:0o755],ofItemAtPath:script.path)
        XCTAssertThrowsError(try RelayBackend(binaryPath:script.path).run(["switch"])) {
            XCTAssertFalse($0.localizedDescription.contains("event"))
        }
    }
    func testGuardBridgeSettingsAndExactHostArePassedToSameBackend() throws {
        let script=try temporaryDirectory().appendingPathComponent("backend")
        let source = """
        #!/bin/sh
        test "$1" = guard || exit 1
        shift
        if [ "$1" = gui-snapshot ]; then
          echo '{"connected":true,"hosts":[],"threads":[],"monitors":[],"warnings":[],"settings":{"threshold":5,"resumeRemaining":10,"interval":30,"continuation":"keep working"}}'
        elif [ "$1" = settings ]; then
          test "$2" = --file && test -f "$3" || exit 1
          cat "$3"
        else
          test "$1" = --host && test "$2" = remote-ssh-test && test "$3" = stop || exit 1
          echo '{"ok":true}'
        fi
        """
        try source.write(to:script,atomically:true,encoding:.utf8)
        try FileManager.default.setAttributes([.posixPermissions:0o755],ofItemAtPath:script.path)
        let backend=RelayBackend(binaryPath:script.path)
        let snapshot=try backend.guardSnapshot()
        XCTAssertEqual(snapshot.settings.interval,30)
        try backend.saveGuardSettings(snapshot.settings)
        try backend.guardAction("stop",threadID:"11111111-1111-4111-8111-111111111111",hostID:"remote-ssh-test")
        XCTAssertThrowsError(try backend.guardAction("stop",threadID:"last",hostID:"local"))
    }
}
