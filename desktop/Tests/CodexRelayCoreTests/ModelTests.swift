import XCTest
@testable import CodexRelayCore

final class ModelTests: XCTestCase {
    private func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    func testAccountListDecoding() throws {
        let json = """
        {
          "active_account_id": "acct-a",
          "profiles": [
            {
              "id": "acct-a",
              "alias": "main",
              "email": "a@example.com",
              "name": "A",
              "plan": "plus",
              "account_id": "acct-a",
              "status": "ok",
              "last_refresh": "2026-10-23T00:00:00Z",
              "last_used_at": null,
              "last_verified_at": "2026-10-23T00:10:00Z",
              "active": true,
              "usage_text": "5h 10%",
              "usage": {
                "checked_at": "2026-10-23T00:10:00Z",
                "source": "http",
                "plan": "plus",
                "account_id": "acct-a",
                "primary": {"used_percent": 10, "window_minutes": 300, "resets_at": 1792746100},
                "secondary": {"used_percent": 33, "window_minutes": 10080, "resets_at": 1793293764},
                "rate_limit_reached_type": null
              }
            }
          ]
        }
        """
        let list = try decoder().decode(RelayAccountList.self, from: Data(json.utf8))
        XCTAssertEqual(list.activeAccountId, "acct-a")
        XCTAssertEqual(list.profiles.count, 1)
        XCTAssertEqual(list.profiles[0].alias, "main")
        XCTAssertTrue(list.profiles[0].isActive)
        XCTAssertEqual(list.profiles[0].usage?.primary?.usedPercent, 10)
        XCTAssertFalse(list.profiles[0].isExhausted)
    }

    func testExhaustedAccount() throws {
        let json = """
        {
          "id": "acct-b", "alias": "work", "active": false, "status": "ok",
          "usage": {
            "primary": {"used_percent": 100, "window_minutes": 300, "resets_at": 1},
            "secondary": null,
            "rate_limit_reached_type": {"type": "rate_limit_reached", "details": "default"}
          }
        }
        """
        let account = try decoder().decode(RelayAccount.self, from: Data(json.utf8))
        XCTAssertTrue(account.isExhausted)
        XCTAssertEqual(account.maxUsedPercent, 100)
    }

    func testAccountDisplayNamePreference() throws {
        let json = """
        {"id":"acct-a","alias":"main","display_name":"工作账号","name":"服务端名称","account_id":"acct-a"}
        """
        let account = try decoder().decode(RelayAccount.self, from: Data(json.utf8))
        XCTAssertEqual(account.preferredName, "工作账号")

        let fallback = try decoder().decode(
            RelayAccount.self,
            from: Data(#"{"id":"acct-b","alias":"personal","name":"真实名称"}"#.utf8)
        )
        XCTAssertEqual(fallback.preferredName, "真实名称")

        let aliasOnly = try decoder().decode(
            RelayAccount.self,
            from: Data(#"{"id":"acct-c","alias":"备用"}"#.utf8)
        )
        XCTAssertEqual(aliasOnly.preferredName, "备用")
    }

    func testStatusDecoding() throws {
        let json = """
        {
          "checked_at": "2026-10-23T00:00:00Z",
          "codex_home": "/Users/me/.codex",
          "relay_home": "/Users/me/.codex-relay",
          "auth_path": "/Users/me/.codex/auth.json",
          "auth_present": true,
          "auth": {
            "auth_mode": "chatgpt",
            "account_id": "acct-a",
            "user_id": "user-a",
            "email": "a@example.com",
            "name": "A",
            "plan": "plus",
            "last_refresh": "2026-10-23T00:00:00Z",
            "access_expires_at": "2026-11-02T00:00:00Z",
            "access_expired": false,
            "has_refresh_token": true,
            "has_api_key": false
          },
          "current_profile": {"id": "acct-a", "alias": "main", "account_id": "acct-a", "email": "a@example.com"},
          "desktop_running": true,
          "desktop_processes": [{"pid": 42, "ppid": 1, "role": "desktop-app-server", "started": "now", "command": "codex"}],
          "daemon_running": false,
          "last_thread_id": "00000000-0000-0000-0000-000000000001"
        }
        """
        let status = try decoder().decode(RelayStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.auth?.email, "a@example.com")
        XCTAssertEqual(status.currentProfile?.alias, "main")
        XCTAssertTrue(status.desktopRunning == true)
        XCTAssertEqual(status.desktopProcesses?.first?.pid, 42)
    }

    func testInspectDecoding() throws {
        let json = """
        {
          "thread": {"id": "t1", "title": "Thread", "cwd": "/tmp"},
          "metrics": {
            "size_human": "19.6 MiB",
            "reasoning_items": 887,
            "reasoning_encrypted_items": 887,
            "reasoning_encrypted_bytes": 5073616,
            "totals": {"turns": 8, "requests": 806, "input_tokens": 192911329, "cached_input_tokens": 188978176, "output_tokens": 779488, "reasoning_output_tokens": 413162, "total_tokens": 193690817, "cache_ratio": 97.96},
            "compactions": [
              {"timestamp": "2026-10-22T23:43:00Z", "window_number": 3, "item_count": 13, "has_encrypted_reasoning": false, "summary_chars": 100}
            ],
            "turns": [
              {"turn_id": "turn-1", "requests": 191, "first_input_tokens": 319502, "first_cached_tokens": 0, "first_cache_ratio": 0.0, "last_input_tokens": 419507, "last_cached_tokens": 417920, "last_cache_ratio": 99.62, "total_cache_ratio": 97.87}
            ]
          }
        }
        """
        let report = try decoder().decode(RelayInspectReport.self, from: Data(json.utf8))
        XCTAssertEqual(report.metrics?.reasoningEncryptedItems, 887)
        XCTAssertEqual(report.metrics?.totals?.inputTokens, 192911329)
        XCTAssertEqual(report.metrics?.totals?.cacheRatio, 97.96)
        XCTAssertEqual(report.metrics?.compactions?.first?.windowNumber, 3)
        XCTAssertEqual(report.metrics?.turns?.first?.lastCacheRatio, 99.62)
    }

    func testDoctorDecoding() throws {
        let json = """
        {"checked_at":"now","ok":true,"failures":0,"warnings":1,"checks":[{"check":"auth","level":"pass","message":"ok"}]}
        """
        let report = try decoder().decode(RelayDoctorReport.self, from: Data(json.utf8))
        XCTAssertTrue(report.ok)
        XCTAssertEqual(report.checks.first?.level, "pass")
    }

    func testManualIdleOverrideIsExplicitAndDoesNotEnableBroadForce() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let script = dir.appendingPathComponent("codex-relay")
        let body = """
        #!/bin/sh
        case "$*" in *--force*) echo 'broad force must not be passed' >&2; exit 7 ;; esac
        if [ "$1" = activity ]; then
          echo '{"idle":false,"active":[{"thread_id":"live","turn_id":"t","evidence":"desktop-runtime"}],"ignored_history":[{"thread_id":"old","reason":"unowned-before-desktop-start"}]}'
        else
          case "$*" in
            *--confirm-idle*) echo '{"ok":true,"changed":true}' ;;
            *) echo 'missing explicit idle confirmation' >&2; exit 3 ;;
          esac
        fi
        """
        try body.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)
        let backend = RelayBackend(binaryPath: script.path)
        XCTAssertThrowsError(try backend.switchAccount(alias: "b", threadID: "none", allowRestart: true) { _ in })
        let result = try backend.switchAccount(alias: "b", threadID: "none", allowRestart: true, confirmedIdle: true) { _ in }
        XCTAssertTrue(result.changed == true)
        let activity = try backend.activity()
        XCTAssertEqual(activity.active.first?.threadId, "live")
        XCTAssertEqual(activity.ignoredHistory.first?.reason, "unowned-before-desktop-start")
    }

    func testSwitchErrorStripsProgressJSON() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let script = dir.appendingPathComponent("codex-relay")
        let body = """
        #!/bin/sh
        echo '{"event":"progress","stage":"resolve","message":"正在解析账号"}' >&2
        echo '{"event":"progress","stage":"preflight","message":"正在检查进程"}' >&2
        echo 'codex-relay: 检测到其他 Codex 进程仍在使用同一 CODEX_HOME：891(cli)' >&2
        exit 1
        """
        try body.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)
        let backend = RelayBackend(binaryPath: script.path)
        do {
            _ = try backend.switchAccount(alias: "main", threadID: "t1", allowRestart: true) { _ in }
            XCTFail("expected switch failure")
        } catch {
            let message = error.localizedDescription
            XCTAssertTrue(message.contains("CODEX_HOME"))
            XCTAssertFalse(message.contains("\"event\""))
            XCTAssertFalse(message.contains("preflight"))
        }
    }

    func testBackendAgainstFakeCLI() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let script = dir.appendingPathComponent("codex-relay")
        let body = """
        #!/bin/sh
        case "$1" in
          list)
            echo '{"active_account_id":"acct-a","profiles":[{"id":"acct-a","alias":"main","account_id":"acct-a","active":true}]}'
            ;;
          status)
            echo '{"auth":{"email":"a@example.com"},"desktop_running":false}'
            ;;
          sessions)
            echo '{"threads":[{"id":"t1","title":"Thread"}]}'
            ;;
          usage)
            echo '{"profiles":[{"id":"acct-a","alias":"main","account_id":"acct-a","active":true,"usage":{"primary":{"used_percent":4,"window_minutes":300}}}]}'
            ;;
          auto)
            echo '{"action":"switched","from":"main","to":"personal","thread_id":"t1","continued":true}'
            ;;
          switch)
            echo '{"event":"progress","stage":"resolve","message":"ok"}' >&2
            echo '{"ok":true,"changed":true,"to":{"alias":"main"},"duration_ms":42,"thread_id":"t1"}'
            ;;
          resume)
            case "$*" in
              *--allow-chatgpt-restart*) echo '{"ok":true}' ;;
              *) echo "missing explicit process permission: $*" >&2; exit 3 ;;
            esac
            ;;
          *)
            echo "unexpected: $@" >&2
            exit 2
            ;;
        esac
        """
        try body.write(to: script, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: script.path)

        let backend = RelayBackend(binaryPath: script.path)
        XCTAssertEqual(try backend.listAccounts().first?.alias, "main")
        XCTAssertEqual(try backend.status().auth?.email, "a@example.com")
        XCTAssertEqual(try backend.sessions().first?.id, "t1")
        XCTAssertEqual(try backend.usage(alias: "main").first?.usage?.primary?.usedPercent, 4)

        var events: [String] = []
        let result = try backend.switchAccount(alias: "main", threadID: "t1", allowRestart: true) { event in
            events.append(event.stage ?? "")
        }
        XCTAssertTrue(result.ok == true)
        XCTAssertEqual(result.durationMs, 42)
        XCTAssertEqual(events, ["resolve"])

        // Deep links must go through the CLI with the explicit permission flag.
        let auto = try backend.autoContinueOnce()
        XCTAssertEqual(auto.action, "switched")
        XCTAssertEqual(auto.to, "personal")
        XCTAssertEqual(auto.continued, true)

        // Deep links must go through the CLI with the explicit permission flag.
        XCTAssertNoThrow(try backend.resumeThread("t1", allowRestart: true))
        XCTAssertNoThrow(try backend.resumeLast(allowRestart: true))
    }
}
