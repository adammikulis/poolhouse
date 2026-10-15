import Foundation
import Darwin

@main struct NativeHostCandidateCheck {
    static func main() {
        do {
            #if TESTING
            if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "protocol" {
                try CandidateTests.run(); return
            }
            #endif
            guard CommandLine.arguments.count == 4, CommandLine.arguments[1] == "inspect" else {
                throw CandidateFailure.invalid
            }
            var bytes = [UInt8](repeating: 0, count: 16384)
            let count = ph_host_inventory(CommandLine.arguments[3], &bytes)
            guard count > 0 else { throw CandidateFailure.invalid }
            let inventory = try CandidateInventory(Data(bytes.prefix(Int(count))))
            let candidate = try CapturedCandidate(directory: CommandLine.arguments[2], inventory: inventory)
            print(candidate.publicReport(), terminator: "")
        } catch {
            fputs("Candidate unavailable: exact captured inventory checks failed.\n", stderr)
            exit(1)
        }
    }
}
