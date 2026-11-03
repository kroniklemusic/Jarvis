// JARVIS.app: a native macOS window that runs the JARVIS engine and shows its HUD.
//
// Built on your Mac by app/build-app.sh with Apple's free command line tools (swiftc).
// The engine is the Python program in the jarvis folder; this app starts it, shows the
// interface it serves on this Mac, and stops it when you quit.

import Cocoa
import WebKit

/// A transparent strip over the top of the window so it can be dragged like a title bar.
final class DragStrip: NSView {
    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 {
            window?.performZoom(nil)
        } else {
            window?.performDrag(with: event)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var engine: Process?
    private var outputBuffer = ""
    private var quitting = false
    private var fatalMessage: String?
    private var logHandle: FileHandle?
    private var activity: NSObjectProtocol?
    private var hudURL: URL?

    private lazy var jarvisFolder: URL = {
        if let path = Bundle.main.object(forInfoDictionaryKey: "JARVISRepoPath") as? String,
           !path.isEmpty, !path.hasPrefix("__") {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("jarvis", isDirectory: true)
    }()

    // MARK: - App lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Keep macOS from putting JARVIS to sleep in the background ("App Nap").
        activity = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiatedAllowingIdleSystemSleep],
            reason: "JARVIS is listening for its wake word")
        buildMenu()
        buildWindow()
        startEngine()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        stopEngine()
        if let activity = activity {
            ProcessInfo.processInfo.endActivity(activity)
        }
    }

    // MARK: - Window

    private func buildWindow() {
        let visible = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let size = NSSize(width: min(1440, visible.width * 0.92), height: min(900, visible.height * 0.92))
        window = NSWindow(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "J.A.R.V.I.S."
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = NSColor(calibratedRed: 0.004, green: 0.02, blue: 0.035, alpha: 1)
        window.minSize = NSSize(width: 760, height: 560)
        window.appearance = NSAppearance(named: .darkAqua)

        let container = NSView(frame: NSRect(origin: .zero, size: size))
        container.autoresizingMask = [.width, .height]

        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: container.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.underPageBackgroundColor = window.backgroundColor
        container.addSubview(webView)

        // Drag area across the top (leaves the window buttons on the left untouched).
        let stripHeight: CGFloat = 40
        let strip = DragStrip(frame: NSRect(x: 80, y: size.height - stripHeight,
                                            width: size.width - 80 - 260, height: stripHeight))
        strip.autoresizingMask = [.width, .minYMargin]
        container.addSubview(strip)

        window.contentView = container
        window.setFrameAutosaveName("JARVISMainWindow")
        if !window.setFrameUsingName("JARVISMainWindow") {
            window.center()
        }
        webView.loadHTMLString(AppDelegate.bootPage, baseURL: nil)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildMenu() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About JARVIS",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide JARVIS", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit JARVIS", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        let viewItem = NSMenuItem()
        mainMenu.addItem(viewItem)
        let viewMenu = NSMenu(title: "View")
        let fullScreen = NSMenuItem(title: "Enter Full Screen",
                                    action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullScreen.keyEquivalentModifierMask = [.command, .control]
        viewMenu.addItem(fullScreen)
        viewMenu.addItem(withTitle: "Reload Interface", action: #selector(reloadInterface), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "Open Logs Folder", action: #selector(openLogs), keyEquivalent: "l")
        viewItem.submenu = viewMenu

        let windowItem = NSMenuItem()
        mainMenu.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = mainMenu
    }

    @objc private func reloadInterface() {
        if let url = hudURL {
            webView.load(URLRequest(url: url))
        }
    }

    @objc private func openLogs() {
        NSWorkspace.shared.open(jarvisFolder.appendingPathComponent("logs", isDirectory: true))
    }

    // MARK: - The JARVIS engine (Python)

    private func startEngine() {
        let python = jarvisFolder.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            showFatal("JARVIS isn't set up yet.\n\nOpen Terminal and run:\n\ncd \"\(jarvisFolder.path)\" && ./setup.sh")
            return
        }

        let logsFolder = jarvisFolder.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsFolder, withIntermediateDirectories: true)
        let logURL = logsFolder.appendingPathComponent("app.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        logHandle = try? FileHandle(forWritingTo: logURL)
        _ = logHandle?.seekToEndOfFile()

        let process = Process()
        // A login shell picks up the same settings as Terminal (Homebrew, ~/.zprofile).
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = ["-lc", "cd \"$1\" && exec \"$0\" -m jarvis --app", python.path, jarvisFolder.path]
        process.currentDirectoryURL = jarvisFolder

        var env = ProcessInfo.processInfo.environment.filter { !$0.key.uppercased().hasPrefix("ANTHROPIC_") }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extraPaths = ["/opt/homebrew/bin", "/usr/local/bin", "\(home)/.local/bin"]
        env["PATH"] = (extraPaths + [env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"]).joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        process.environment = env

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            DispatchQueue.main.async { self?.handleOutput(data) }
        }
        process.terminationHandler = { [weak self] finished in
            let status = finished.terminationStatus
            DispatchQueue.main.async { self?.engineExited(status: status) }
        }

        do {
            try process.run()
            engine = process
        } catch {
            showFatal("JARVIS couldn't start: \(error.localizedDescription)")
        }
    }

    private func handleOutput(_ data: Data) {
        logHandle?.write(data)
        outputBuffer += String(decoding: data, as: UTF8.self)
        while let newline = outputBuffer.range(of: "\n") {
            let line = String(outputBuffer[..<newline.lowerBound]).trimmingCharacters(in: .whitespaces)
            outputBuffer.removeSubrange(..<newline.upperBound)
            if line.hasPrefix("JARVIS_HUD_URL="),
               let url = URL(string: String(line.dropFirst("JARVIS_HUD_URL=".count))) {
                hudURL = url
                webView.load(URLRequest(url: url))
            } else if line.hasPrefix("JARVIS_FATAL=") {
                fatalMessage = String(line.dropFirst("JARVIS_FATAL=".count))
            }
        }
    }

    private func engineExited(status: Int32) {
        engine = nil
        if quitting {
            return
        }
        let message = fatalMessage
            ?? "JARVIS stopped unexpectedly (code \(status)).\n\nDetails are in the logs folder: \(jarvisFolder.path)/logs"
        showFatal(message)
    }

    private func stopEngine() {
        guard let process = engine, process.isRunning else {
            return
        }
        process.terminate()   // asks JARVIS to say goodbye and shut Claude down cleanly
        let deadline = Date().addingTimeInterval(4)
        while process.isRunning && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        if process.isRunning {
            kill(process.processIdentifier, SIGKILL)
        }
    }

    private func showFatal(_ message: String) {
        quitting = true
        let alert = NSAlert()
        alert.messageText = "JARVIS can't continue"
        alert.informativeText = message
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Open Logs Folder")
        if alert.runModal() == .alertSecondButtonReturn {
            openLogs()
        }
        NSApp.terminate(nil)
    }

    // MARK: - Web view

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        window.makeFirstResponder(webView)   // so Space and Esc reach the interface
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        // Only JARVIS's own interface opens inside the window; anything else goes to your browser.
        if url.scheme == "about" || url.host == "127.0.0.1" {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    static let bootPage = """
    <!doctype html><html><head><meta charset="utf-8"><style>
    html,body{margin:0;height:100%;background:#010509;color:#45e3ff;font:12px ui-monospace,Menlo,monospace;
    display:grid;place-items:center;letter-spacing:.4em;text-transform:uppercase;overflow:hidden}
    .r{width:180px;height:180px;border-radius:50%;border:2px solid rgba(69,227,255,.25);border-top-color:#45e3ff;
    animation:s 1.1s linear infinite;box-shadow:0 0 40px rgba(69,227,255,.25),inset 0 0 40px rgba(69,227,255,.15)}
    .w{display:grid;place-items:center;gap:28px}.t{font-size:22px;color:#b9f6ff;text-shadow:0 0 14px #45e3ff}
    @keyframes s{to{transform:rotate(360deg)}}</style></head>
    <body><div class="w"><div class="r"></div><div class="t">J.A.R.V.I.S.</div><div>Starting engine</div></div></body></html>
    """
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
