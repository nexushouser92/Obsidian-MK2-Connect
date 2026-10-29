// Obsidian MK2 Connect on macOS: a native window over mk2_patcher_mac.py, like the Windows one.
//
// Finds compatible application, tells whether it is original or patched, and enables the MK2 or restores the original.
// Checking needs no password; Enable and Restore ask for it with the standard macOS dialog.
// Build it with ./build_mac_app.sh (needs the Xcode Command Line Tools).
import Cocoa

let jamWarning = "!!! WARNING: JAM CONTROLLER WILL STOP WORKING IN THE MUSIC SOFTWARE WHILE THE MK2 IS ENABLED !!!"
let defaultApp = "/Applications/Native Instruments/Maschine 3/Maschine 3.app"
let python = "/usr/bin/python3"
let toolPath = (Bundle.main.resourcePath ?? ".") + "/mk2_patcher_mac.py"

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

final class Controller: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var stack: NSStackView!
    let statusLabel = NSTextField(wrappingLabelWithString: "")
    let actionButton = NSButton(title: "—", target: nil, action: nil)
    let chooseButton = NSButton(title: "Choose another app…", target: nil, action: nil)
    let spinner = NSProgressIndicator()
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
        menu.addItem(withTitle: "Quit MK2 Activator", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = menu
        NSApp.mainMenu = main
    }

    func buildWindow() {
        let title = NSTextField(labelWithString: "Obsidian MK2 Connect")
        title.font = .boldSystemFont(ofSize: 18)
        let subtitle = NSTextField(labelWithString: "Close the music software before continuing.")
        subtitle.textColor = .secondaryLabelColor
        let warning = NSTextField(wrappingLabelWithString: jamWarning)
        warning.font = .boldSystemFont(ofSize: 13)
        warning.textColor = .systemRed
        statusLabel.font = .systemFont(ofSize: 14)

        actionButton.target = self
        actionButton.action = #selector(actionPressed)
        actionButton.bezelStyle = .rounded
        actionButton.keyEquivalent = "\r"
        actionButton.widthAnchor.constraint(greaterThanOrEqualToConstant: 150).isActive = true
        chooseButton.target = self
        chooseButton.action = #selector(choosePressed)
        chooseButton.bezelStyle = .rounded
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let row = NSStackView(views: [actionButton, spinner, spacer, chooseButton])
        row.orientation = .horizontal
        row.spacing = 10

        stack = NSStackView(views: [title, subtitle, warning, statusLabel, row])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.setCustomSpacing(16, after: warning)
        stack.setCustomSpacing(16, after: statusLabel)
        stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
        for view in [warning, statusLabel, row] {
            view.widthAnchor.constraint(equalToConstant: 440).isActive = true
        }

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 260),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Obsidian MK2 Connect"
        window.contentView = stack
        window.isReleasedWhenClosed = false
        fit()
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    /// Resizes the window to its content, keeping the top edge where it is.
    func fit() {
        stack.layoutSubtreeIfNeeded()
        let height = window.frameRect(forContentRect: NSRect(origin: .zero, size: stack.fittingSize)).height
        var frame = window.frame
        frame.origin.y += frame.height - height
        frame.size.height = height
        window.setFrame(frame, display: true, animate: false)
    }

    func busy(_ text: String) {
        statusLabel.stringValue = text
        statusLabel.textColor = .secondaryLabelColor
        actionButton.title = "—"
        actionButton.isEnabled = false
        chooseButton.isEnabled = false
        spinner.startAnimation(nil)
        fit()
    }

    func show(_ text: String, _ color: NSColor, action: String?) {
        statusLabel.stringValue = text
        statusLabel.textColor = color
        actionButton.title = action ?? "—"
        actionButton.isEnabled = action != nil
        chooseButton.isEnabled = true
        spinner.stopAnimation(nil)
        fit()
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
        case "original": show(text, .labelColor, action: "Enable MK2")
        case "patched": show(text, NSColor(calibratedRed: 0, green: 0.45, blue: 0.1, alpha: 1), action: "Restore original")
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
        guard panel.runModal() == .OK, let url = panel.url else { return }
        if !FileManager.default.fileExists(atPath: url.path + "/Contents/MacOS/Maschine 3") {
            alert("Unsupported app", "Please select a compatible application.", style: .warning)
            return
        }
        appPath = url.path
        refresh()
    }

    @objc func actionPressed() {
        if softwareIsRunning() {
            alert("Music software is open", "Close the music software and press the button again.", style: .warning)
            return
        }
        let revert = state == "patched"
        if !revert {
            let a = NSAlert()
            a.alertStyle = .warning
            a.messageText = "Enable MK2"
            a.informativeText = "\(jamWarning)\n\nDo you want to enable the MK2?"
            a.addButton(withTitle: "Enable MK2")
            a.addButton(withTitle: "Cancel")
            if a.runModal() != .alertFirstButtonReturn { return }
        }
        busy("Working… macOS will ask for your password. This takes a minute.")
        let app = appPath
        DispatchQueue.global().async {
            let result = toolAsAdmin(revert ? "restore" : "enable", app: app)
            DispatchQueue.main.async { self.finished(revert, result) }
        }
    }

    func finished(_ revert: Bool, _ result: Output) {
        if result.code == 0 {
            if revert {
                alert("Done", "The music software is back to its factory state. The Jam controller works again.")
            } else {
                alert("Done", "MK2 enabled. Connect the MK2 and open the music software.\n\n"
                    + "If your software provider’s installer updates music software, you will need to enable it again.")
            }
        } else if !result.err.contains("(-128)") {      // -128: the user cancelled the password dialog
            let message = cleanError(result.err)
            alert("Error", "\(message.isEmpty ? "The command failed." : message)\n\nCheck the application before retrying.", style: .critical)
        }
        refresh()
    }
}

let delegate = Controller()
let application = NSApplication.shared
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
