// Obsidian MK2 Connect on macOS: a native window over mk2_patcher_mac.py, like the Windows one.
//
// Finds compatible application, tells whether it is original or patched, and enables the MK2 or restores the original.
// Checking needs no password; Enable and Restore ask for it with the standard macOS dialog.
// Build it with ./build_mac_app.sh (needs the Xcode Command Line Tools).
import Cocoa
import CoreText

let jamWarning = "!!! WARNING: JAM CONTROLLER WILL STOP WORKING IN THE MUSIC SOFTWARE WHILE THE MK2 IS ENABLED !!!"
let defaultApp = "/Applications/Native Instruments/Maschine 3/Maschine 3.app"
let python = "/usr/bin/python3"
let toolPath = (Bundle.main.resourcePath ?? ".") + "/mk2_patcher_mac.py"

func interFont(_ size: CGFloat, semibold: Bool = false) -> NSFont {
    let file = semibold ? "Inter-SemiBold" : "Inter-Regular"
    if let url = Bundle.main.url(forResource: file, withExtension: "ttf") {
        CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        if let provider = CGDataProvider(url: url as CFURL), let cgFont = CGFont(provider),
           let name = cgFont.postScriptName as String?, let font = NSFont(name: name, size: size) {
            return font
        }
    }
    return .systemFont(ofSize: size, weight: semibold ? .semibold : .regular)
}

struct Output {
    let code: Int32
    let out: String
    let err: String
}

func run(_ executable: String, _ arguments: [String]) -> Output {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    let out = Pipe(), err = Pipe()
    process.standardOutput = out
    process.standardError = err
    do {
        try process.run()
    } catch {
        return Output(code: -1, out: "", err: error.localizedDescription)
    }
    let outData = out.fileHandleForReading.readDataToEndOfFile()
    let errData = err.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    let text = { (d: Data) in String(decoding: d, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines) }
    return Output(code: process.terminationStatus, out: text(outData), err: text(errData))
}

func shellQuote(_ s: String) -> String {
    "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

func appleScriptString(_ s: String) -> String {
    "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
}

/// Runs the command-line tool.
func tool(_ arguments: [String]) -> Output {
    run(python, ["-B", toolPath] + arguments)
}

/// Runs enable/restore as root with the macOS password dialog (-B: no .pyc files owned by root).
func toolAsAdmin(_ action: String, app: String) -> Output {
    let command = [python, "-B", toolPath, action, "--yes", "--app", app].map(shellQuote).joined(separator: " ")
    return run("/usr/bin/osascript", ["-e", "do shell script \(appleScriptString(command)) with administrator privileges"])
}

/// The message of the tool inside osascript's "execution error: ... (1)".
func cleanError(_ s: String) -> String {
    var t = s.trimmingCharacters(in: .whitespacesAndNewlines)
    if let r = t.range(of: "execution error:") { t = String(t[r.upperBound...]).trimmingCharacters(in: .whitespaces) }
    if let r = t.range(of: #"\s*\(-?\d+\)$"#, options: .regularExpression) { t.removeSubrange(r) }
    if t.hasPrefix("ERROR: ") { t.removeFirst(7) }
    return t
}

func softwareIsRunning() -> Bool {
    run("/usr/bin/pgrep", ["-x", "Maschine 3"]).code == 0
}

final class Controller: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var layoutStack: NSStackView!
    let statusLabel = NSTextField(wrappingLabelWithString: "")
    let actionButton = NSButton(title: "—", target: nil, action: nil)
    let chooseButton = NSButton(title: "Choose another version…", target: nil, action: nil)
    let spinner = NSProgressIndicator()
    var infoSheet: NSWindow?
    var appPath = defaultApp
    var state = "unsupported"

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        refresh()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func buildMenu() {
        let main = NSMenu()
        let item = NSMenuItem()
        main.addItem(item)
        let menu = NSMenu()
        menu.addItem(withTitle: "Quit Obsidian MK2 Connect", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = menu
        NSApp.mainMenu = main
    }

    func buildWindow() {
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 440, height: 320))
        let logo = NSImageView()
        logo.image = NSImage(contentsOfFile: (Bundle.main.resourcePath ?? "") + "/obsidian-logo.png")
        logo.imageScaling = .scaleProportionallyUpOrDown
        logo.widthAnchor.constraint(equalToConstant: 60).isActive = true
        logo.heightAnchor.constraint(equalToConstant: 60).isActive = true
        statusLabel.font = interFont(15, semibold: true)
        statusLabel.alignment = .center
        statusLabel.isSelectable = false
        statusLabel.maximumNumberOfLines = 2
        let desc = NSTextField(wrappingLabelWithString: "For reliable connection, please close all open audio production software (DAWs, mixers, etc.).")
        desc.font = interFont(12)
        desc.textColor = .secondaryLabelColor
        desc.alignment = .center
        desc.isSelectable = false
        desc.preferredMaxLayoutWidth = 380
        actionButton.bezelStyle = .rounded
        actionButton.font = interFont(12)
        actionButton.keyEquivalent = "\r"
        actionButton.target = self
        actionButton.action = #selector(actionPressed)
        chooseButton.title = "Select other version…"
        chooseButton.bezelStyle = .rounded
        chooseButton.font = interFont(12)
        chooseButton.target = self
        chooseButton.action = #selector(choosePressed)
        let buttons = NSStackView(views: [actionButton, chooseButton])
        buttons.orientation = .horizontal
        buttons.distribution = .fillEqually
        buttons.spacing = 12
        actionButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 32).isActive = true
        chooseButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 32).isActive = true
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        spinner.frame = NSRect(x: 4, y: 6, width: 14, height: 14)
        let infoButton = NSButton(title: "More info…", target: self, action: #selector(moreInfoPressed))
        infoButton.isBordered = false
        infoButton.contentTintColor = .linkColor
        infoButton.font = interFont(11)
        let stack = NSStackView(views: [logo, statusLabel, desc, buttons, infoButton])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)
        layoutStack = stack
        NSLayoutConstraint.activate([
            stack.centerYAnchor.constraint(equalTo: content.centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 30),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -30),
            statusLabel.widthAnchor.constraint(equalTo: stack.widthAnchor),
            desc.widthAnchor.constraint(equalTo: stack.widthAnchor),
            buttons.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
        window = NSWindow(contentRect: content.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Obsidian MK2 Connect"
        window.appearance = NSAppearance(named: .darkAqua)
        window.contentView = content
        let loadingAccessory = NSTitlebarAccessoryViewController()
        loadingAccessory.layoutAttribute = .right
        let loadingView = NSView(frame: NSRect(x: 0, y: 0, width: 31, height: 22))
        loadingView.addSubview(spinner)
        loadingAccessory.view = loadingView
        window.addTitlebarAccessoryViewController(loadingAccessory)
        window.isReleasedWhenClosed = false
        fitWindow()
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    func fitWindow() {
        guard let window = window, let stack = layoutStack else { return }
        window.contentView?.layoutSubtreeIfNeeded()
        stack.layoutSubtreeIfNeeded()
        let height = max(160, ceil(stack.fittingSize.height) + 16)
        let oldFrame = window.frame
        let contentRect = NSRect(x: 0, y: 0, width: 440, height: height)
        var frame = window.frameRect(forContentRect: contentRect)
        frame.origin = NSPoint(x: oldFrame.origin.x, y: oldFrame.maxY - frame.height)
        window.setFrame(frame, display: true, animate: false)
    }

    @objc func moreInfoPressed() {
        guard infoSheet == nil else { return }
        let sheet = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 380, height: 230),
                             styleMask: [.titled, .closable], backing: .buffered, defer: false)
        sheet.title = "More Info"
        sheet.appearance = NSAppearance(named: .darkAqua)
        sheet.isMovable = false
        sheet.isReleasedWhenClosed = false
        sheet.delegate = self
        let label = NSTextField(wrappingLabelWithString: "Other versions may not work.\n\nUse at your own risk.\n\nEnsure all production software is closed before continuing.")
        label.font = interFont(12)
        label.textColor = .secondaryLabelColor
        label.alignment = .center
        label.isSelectable = false
        let ok = NSButton()
        ok.title = "OK"
        ok.bezelStyle = .rounded
        ok.font = interFont(12)
        ok.keyEquivalent = "\r"
        ok.target = self
        ok.action = #selector(closeInfoSheet)
        let stack = NSStackView(views: [label, ok])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false
        if let content = sheet.contentView {
            content.addSubview(stack)
            NSLayoutConstraint.activate([
                stack.centerYAnchor.constraint(equalTo: content.centerYAnchor),
                stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
                stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
                label.widthAnchor.constraint(equalTo: stack.widthAnchor),
                ok.widthAnchor.constraint(equalToConstant: 140)
            ])
        }
        infoSheet = sheet
        window.beginSheet(sheet) { _ in self.infoSheet = nil }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if sender === infoSheet { window.endSheet(sender); return false }
        return true
    }

    @objc func closeInfoSheet() {
        guard let sheet = infoSheet else { return }
        window.endSheet(sheet)
    }

    func busy(_ text: String) {
        statusLabel.stringValue = text
        statusLabel.textColor = .secondaryLabelColor
        actionButton.title = "—"
        actionButton.isEnabled = false
        chooseButton.isEnabled = false
        spinner.startAnimation(nil)
        fitWindow()
    }

    func show(_ text: String, _ color: NSColor, action: String?) {
        statusLabel.stringValue = text
        statusLabel.textColor = color
        actionButton.title = action ?? "—"
        actionButton.isEnabled = action != nil
        chooseButton.isEnabled = true
        spinner.stopAnimation(nil)
        fitWindow()
    }

    func refresh() {
        state = "unsupported"
        busy("Checking music software… (takes a few seconds)")
        let app = appPath
        DispatchQueue.global().async {
            let result = tool(["status", "--porcelain", "--app", app])
            DispatchQueue.main.async { self.checked(result) }
        }
    }

    func checked(_ result: Output) {
        if result.code != 0 {
            show(cleanError(result.err.isEmpty ? result.out : result.err), .systemRed, action: nil)
            return
        }
        let parts = result.out.components(separatedBy: "\n")
        state = parts.first ?? "unsupported"
        let text = parts.dropFirst().joined(separator: "\n")
        switch state {
        case "original": show("MK2 is Not Active", .labelColor, action: "Activate MK2")
        case "patched": show("Connection Successful", .systemGreen, action: "Restore original")
        default: show(text, .systemRed, action: nil)
        }
    }

    func alert(_ title: String, _ text: String, style: NSAlert.Style = .informational) {
        let a = NSAlert()
        a.alertStyle = style
        a.messageText = title
        a.informativeText = text
        a.runModal()
    }

    @objc func choosePressed() {
        let panel = NSOpenPanel()
        panel.title = "Locate compatible application"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowedFileTypes = ["app"]
        panel.directoryURL = URL(fileURLWithPath: "/Applications")
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            guard FileManager.default.fileExists(atPath: url.path + "/Contents/MacOS/Maschine 3") else {
                self.show("Unsupported app. Select a compatible version.", .systemRed, action: nil)
                return
            }
            self.appPath = url.path
            self.refresh()
        }
    }

    @objc func actionPressed() {
        if softwareIsRunning() {
            let action = state == "patched" ? "Restore original" : "Activate MK2"
            show("Close your production software first.", .systemRed, action: action)
            return
        }
        let revert = state == "patched"
        busy("Working… macOS will ask for your password.")
        let app = appPath
        DispatchQueue.global().async {
            let result = toolAsAdmin(revert ? "restore" : "enable", app: app)
            DispatchQueue.main.async { self.finished(revert, result) }
        }
    }

    func finished(_ revert: Bool, _ result: Output) {
        if result.code == 0 || result.err.contains("(-128)") {
            refresh()
            return
        }
        let message = cleanError(result.err.isEmpty ? result.out : result.err)
        statusLabel.toolTip = message
        let action = revert ? "Restore original" : "Activate MK2"
        show(message.isEmpty ? "Action failed. Please try again." : message, .systemRed, action: action)
    }

}

let delegate = Controller()
let application = NSApplication.shared
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
