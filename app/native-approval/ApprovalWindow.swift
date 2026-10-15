import AppKit

@MainActor
final class ApprovalWindow: NSObject, NSWindowDelegate, NSApplicationDelegate {
    private let helper: AuthenticatedHelper
    private var session: ApprovalSession
    private let window: NSWindow
    private var authorization: FreshAuthorization?
    private var observer: NSObjectProtocol?
    private var expiryTimer: Timer?
    private let approveButton = NSButton()

    init(helper: AuthenticatedHelper, card: ApprovalCard) {
        self.helper = helper
        session = ApprovalSession(card: card, windowID: UUID())
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 720, height: 620),
                          styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        super.init()
        window.title = "Poolhouse approval"
        window.delegate = self
        window.isReleasedWhenClosed = false
        let scroll = NSScrollView(frame: NSRect(x: 20, y: 70, width: 680, height: 530))
        scroll.autoresizingMask = [.width, .height]
        scroll.hasVerticalScroller = true
        let text = NSTextView(frame: scroll.bounds)
        text.isVerticallyResizable = true
        text.isHorizontallyResizable = false
        text.minSize = NSSize(width: 0, height: scroll.contentSize.height)
        text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        text.textContainer?.containerSize = NSSize(width: scroll.contentSize.width, height: CGFloat.greatestFiniteMagnitude)
        text.textContainer?.widthTracksTextView = true
        text.isEditable = false
        text.isSelectable = true
        text.isRichText = false
        text.font = NSFont.systemFont(ofSize: 15)
        text.string = card.display
        text.autoresizingMask = [.width]
        text.sizeToFit()
        scroll.documentView = text
        window.contentView?.addSubview(scroll)
        let approve = approveButton
        approve.title = "Approve"; approve.target = self; approve.action = #selector(ApprovalWindow.approve)
        approve.frame = NSRect(x: 570, y: 20, width: 130, height: 32)
        approve.autoresizingMask = [.minXMargin, .maxYMargin]
        window.contentView?.addSubview(approve)
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancel))
        cancel.frame = NSRect(x: 430, y: 20, width: 130, height: 32)
        cancel.autoresizingMask = [.minXMargin, .maxYMargin]
        window.contentView?.addSubview(cancel)
        observer = NotificationCenter.default.addObserver(forName: NSApplication.didResignActiveNotification,
                                                          object: NSApp, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.background() }
        }
    }

    func show() {
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        expiryTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { if let self, self.now >= self.session.snapshot.card.expires { self.cancel() } }
        }
    }
    private var visible: Bool { window.isVisible && !window.isMiniaturized && window.occlusionState.contains(.visible) }
    private var now: UInt64 { UInt64(max(0, Date().timeIntervalSince1970)) }

    @objc private func approve() {
        guard session.phase == .displaying else { return }
        approveButton.isEnabled = false
        do {
            try session.begin(visible: visible, active: NSApp.isActive && window.isKeyWindow, now: now)
            authorization = try FreshAuthorization(helper: helper)
            try authorization?.authenticate(helper: helper, snapshot: session.snapshot) { [weak self] result in
                self?.authenticated(result)
            }
        } catch { cancel() }
    }

    private func authenticated(_ result: Result<Void, Error>) {
        do {
            try result.get()
            try session.authenticated(snapshot: session.snapshot, visible: visible,
                                      active: NSApp.isActive && window.isKeyWindow, now: now)
            try helper.commit(snapshot: session.snapshot)
            try session.completed()
            finish()
        } catch { cancel() }
    }

    private func background() {
        session.background()
        if session.phase == .cancelled { finish() }
    }
    @objc private func cancel() { session.cancel(); helper.cancel(); finish() }
    private func finish() { expiryTimer?.invalidate(); expiryTimer = nil; authorization?.destroy(); authorization = nil; window.orderOut(nil); NSApp.stop(nil) }
    func windowWillClose(_ notification: Notification) { cancel() }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { cancel(); return .terminateNow }
    deinit { if let observer { NotificationCenter.default.removeObserver(observer) } }
}
