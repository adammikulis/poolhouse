import AppKit

#if !TESTING
@MainActor
func run() -> Int32 {
    guard CommandLine.arguments.count == 2, ApprovalCard.hex(CommandLine.arguments[1]) else { return 1 }
    do {
        let helper = try AuthenticatedHelper.connect()
        let card = try helper.card(requestID: CommandLine.arguments[1])
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let window = ApprovalWindow(helper: helper, card: card)
        app.delegate = window
        window.show()
        app.run()
        return 0
    } catch { return 1 }
}
@main
struct ApprovalMain {
    @MainActor static func main() { exit(run()) }
}
#endif
