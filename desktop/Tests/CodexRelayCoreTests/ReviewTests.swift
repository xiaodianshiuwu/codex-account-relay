import XCTest
import Darwin
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

    func testAutoSettingsStoreRetriesFlockContentionAndThrowsWhenHeldTooLong() throws {
        let dir = try temporaryDirectory()
        let file = dir.appendingPathComponent("auto.json")
        let lockPath = dir.appendingPathComponent("auto.lock").path
        try AutoSettingsStore.update(["enabled": true, "allow_chatgpt_restart": true], at: file)

        // Hold LOCK_EX for ~100ms while update() retries (20 * 25ms = 500ms budget) -> must succeed.
        let fd1 = Darwin.open(lockPath, O_CREAT | O_RDWR, 0o600)
        XCTAssertGreaterThanOrEqual(fd1, 0)
        XCTAssertEqual(flock(fd1, LOCK_EX), 0)
        let releaser = DispatchQueue(label: "test.lock.release")
        releaser.asyncAfter(deadline: .now() + 0.1) {
            flock(fd1, LOCK_UN)
            Darwin.close(fd1)
        }
        try AutoSettingsStore.update(["allow_chatgpt_restart": false], at: file)
        XCTAssertEqual(AutoSettingsStore.load(at: file)["allow_chatgpt_restart"] as? Bool, false)

        // Hold LOCK_EX past bounded retries -> must throw EBUSY and leave disk unchanged.
        let fd2 = Darwin.open(lockPath, O_CREAT | O_RDWR, 0o600)
        XCTAssertGreaterThanOrEqual(fd2, 0)
        XCTAssertEqual(flock(fd2, LOCK_EX), 0)
        defer {
            flock(fd2, LOCK_UN)
            Darwin.close(fd2)
        }
        XCTAssertThrowsError(
            try AutoSettingsStore.update(["enabled": false], at: file, maxLockRetries: 2, retryDelayMicros: 10_000)
        ) { error in
            XCTAssertEqual((error as? POSIXError)?.code, .EBUSY)
        }
        XCTAssertEqual(AutoSettingsStore.load(at: file)["enabled"] as? Bool, true)
    }

    func testAutoSettingsStoreValidatesFieldsNormalizesUUIDAndWrites0600() throws {
        let dir = try temporaryDirectory()
        let file = dir.appendingPathComponent("auto.json")
        let upperUUID = "11111111-1111-4111-8111-111111111111".uppercased()
        try AutoSettingsStore.update([
            "enabled": true,
            "allow_chatgpt_restart": false,
            "interval": 30.0,
            "remaining_threshold": 15.0,
            "strategy": "best",
            "prompt": "continue",
            "thread_id": upperUUID,
        ], at: file)
        let loaded = AutoSettingsStore.load(at: file)
        XCTAssertEqual(loaded["thread_id"] as? String, upperUUID.lowercased())

        let attrs = try FileManager.default.attributesOfItem(atPath: file.path)
        let perms = (attrs[.posixPermissions] as? NSNumber)?.intValue
        XCTAssertEqual(perms, 0o600)

        let entries = try FileManager.default.contentsOfDirectory(atPath: dir.path)
        XCTAssertFalse(entries.contains(where: { $0.hasSuffix(".tmp") }))

        // Invalid values rejected
        XCTAssertThrowsError(try AutoSettingsStore.update(["enabled": "true"], at: file))
        XCTAssertThrowsError(try AutoSettingsStore.update(["allow_chatgpt_restart": 1], at: file))
        XCTAssertThrowsError(try AutoSettingsStore.update(["interval": true], at: file))
        XCTAssertThrowsError(try AutoSettingsStore.update(["interval": Double.nan], at: file))
        XCTAssertThrowsError(try AutoSettingsStore.update(["remaining_threshold": 101.0], at: file))
        XCTAssertThrowsError(try AutoSettingsStore.update(["strategy": "random"], at: file))
        XCTAssertThrowsError(try AutoSettingsStore.update(["prompt": "bad\0text"], at: file))
        XCTAssertThrowsError(try AutoSettingsStore.update(["thread_id": "not-a-uuid"], at: file))
    }

    func testBackendHandlesLeadingDashSearchAndDisplayName() throws {
        let script = try temporaryDirectory().appendingPathComponent("backend")
        let source = """
        #!/bin/sh
        if [ "$1" = sessions ]; then
          case "$*" in
            *"--search=-urgent"*) echo '{"threads":[{"id":"t-dash","title":"-urgent"}]}' ;;
            *) echo "bad sessions args: $*" >&2; exit 2 ;;
          esac
        elif [ "$1" = label ]; then
          if [ "$2" = "--json" ] && [ "$3" = "--" ] && [ "$4" = "main" ] && [ "$5" = "-work" ]; then
            echo '{"ok":true,"profile":{"id":"acct-1","alias":"main","display_name":"-work"}}'
          else
            echo "bad label args: $*" >&2; exit 3
          fi
        else
          echo "unexpected: $*" >&2; exit 4
        fi
        """
        try source.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)
        let backend = RelayBackend(binaryPath: script.path)
        let sessions = try backend.sessions(limit: 10, search: "-urgent")
        XCTAssertEqual(sessions.first?.id, "t-dash")
        let labeled = try backend.setDisplayName(alias: "main", displayName: "-work")
        XCTAssertEqual(labeled.displayName, "-work")
    }
}
