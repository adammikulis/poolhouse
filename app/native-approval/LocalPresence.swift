import Foundation
import LocalAuthentication

struct PresenceBinding: Equatable {
    let snapshot: WindowSnapshot
    let caller: CallerBinding
}

@MainActor
final class LocalPresenceResult {
    private let binding: PresenceBinding
    private var consumed = false
    fileprivate init(binding: PresenceBinding) { self.binding = binding }

    func consume(challenge: AdmittedChallenge, snapshot: WindowSnapshot) throws {
        try consume(binding: PresenceBinding(snapshot: snapshot, caller: challenge.caller))
    }

    fileprivate func consume(binding current: PresenceBinding) throws {
        guard !consumed else { throw ApprovalFailure.cancelled }
        consumed = true
        guard current == binding else { throw ApprovalFailure.cancelled }
    }
}

private enum PresencePolicy: Equatable { case deviceOwnerAuthentication }
private protocol PresenceContext: AnyObject {
    func evaluate(policy: PresencePolicy, reuse: TimeInterval, reply: @escaping @MainActor (Bool) -> Void)
    func invalidate()
}

private final class NativePresenceContext: PresenceContext {
    private let context = LAContext()
    func evaluate(policy: PresencePolicy, reuse: TimeInterval, reply: @escaping @MainActor (Bool) -> Void) {
        context.touchIDAuthenticationAllowableReuseDuration = reuse
        switch policy {
        case .deviceOwnerAuthentication:
            context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "approve the changes shown in Poolhouse") { success, error in
                let accepted = success && error == nil
                Task { @MainActor in reply(accepted) }
            }
        }
    }
    func invalidate() { context.invalidate() }
}

@MainActor
private final class PresenceAttempt {
    private let context: PresenceContext
    private let binding: PresenceBinding
    private var validate: (() throws -> Void)?
    private var completion: ((Result<LocalPresenceResult, Error>) -> Void)?
    private var started = false
    private var terminal = false

    init(context: PresenceContext, binding: PresenceBinding, validate: @escaping () throws -> Void,
         completion: @escaping (Result<LocalPresenceResult, Error>) -> Void) {
        self.context = context; self.binding = binding; self.validate = validate; self.completion = completion
    }
    func begin() throws {
        guard !started, !terminal else { throw ApprovalFailure.cancelled }
        started = true
        context.evaluate(policy: .deviceOwnerAuthentication, reuse: 0) { [weak self] success in self?.received(success) }
    }
    private func received(_ success: Bool) {
        guard started, !terminal else { return }
        terminal = true
        context.invalidate()
        let reply = completion, validation = validate
        completion = nil; validate = nil
        do {
            guard success else { throw ApprovalFailure.cancelled }
            guard let validation else { throw ApprovalFailure.cancelled }
            try validation()
            reply?(.success(LocalPresenceResult(binding: binding)))
        } catch { reply?(.failure(error)) }
    }
    func cancel() { terminal = true; completion = nil; validate = nil; context.invalidate() }
    deinit { context.invalidate() }
}

@MainActor
final class NativeLocalPresence {
    private let attempt: PresenceAttempt
    private init(attempt: PresenceAttempt) { self.attempt = attempt }

    static func begin(helper: AuthenticatedHelper, challenge: AdmittedChallenge, snapshot: WindowSnapshot,
                      validateWindow: @escaping () throws -> Void,
                      completion: @escaping (Result<LocalPresenceResult, Error>) -> Void) throws -> NativeLocalPresence {
        guard snapshot.card == challenge.card else { throw ApprovalFailure.invalid }
        try helper.validate(challenge)
        let binding = PresenceBinding(snapshot: snapshot, caller: challenge.caller)
        let attempt = PresenceAttempt(context: NativePresenceContext(), binding: binding, validate: {
            try helper.validate(challenge)
            try validateWindow()
        }, completion: completion)
        let operation = NativeLocalPresence(attempt: attempt)
        try attempt.begin()
        return operation
    }
    func cancel() { attempt.cancel() }
}

#if TESTING
private final class MockPresenceContext: PresenceContext {
    var reply: (@MainActor (Bool) -> Void)?
    var invalidations = 0
    var evaluations = 0
    var policy: PresencePolicy?
    var reuse: TimeInterval?
    func evaluate(policy: PresencePolicy, reuse: TimeInterval, reply: @escaping @MainActor (Bool) -> Void) {
        self.policy = policy; self.reuse = reuse; self.reply = reply; evaluations += 1
    }
    func invalidate() { invalidations += 1 }
}

private final class MockCapture {}

@MainActor
struct PresenceTests {
    static func run() throws {
        let hash = String(repeating: "1", count: 64)
        let fields: NSDictionary = ["version": 1, "request_id": hash, "action": "governance_act",
            "pool": ["id": String(hash.prefix(32)), "anchor": hash, "name": "New pool"],
            "signer": ["fingerprint": hash, "algorithm": "p256", "role": "admin", "assurance": "trusted-native"],
            "expires_at": 200, "text": "Change the recovery delay.", "nonce": hash, "digest": hash,
            "head": String(repeating: "8", count: 64), "generation": 7]
        let card = try ApprovalCard.decode(fields, requestedID: hash, now: 100)
        let caller = CallerBinding(authority: hash, guiCDHash: String(hash.prefix(40)), session: OwnerSession(owner: 501, asid: 7))
        let binding = PresenceBinding(snapshot: WindowSnapshot(windowID: UUID(), card: card), caller: caller)
        func refused(_ body: () throws -> Void) { do { try body(); preconditionFailure("Accepted stale presence") } catch {} }
        let mock = MockPresenceContext()
        var proof: LocalPresenceResult?, count = 0
        let attempt = PresenceAttempt(context: mock, binding: binding, validate: {}, completion: { result in
            precondition(mock.invalidations > 0)
            proof = try! result.get(); count += 1
        })
        try attempt.begin()
        precondition(mock.evaluations == 1 && mock.reuse == 0 && mock.policy == .deviceOwnerAuthentication)
        mock.reply?(true); mock.reply?(true)
        precondition(count == 1)
        try proof!.consume(binding: binding)
        refused { try proof!.consume(binding: binding) }
        refused { try attempt.begin() }
        for kind in 0..<3 {
            let context = MockPresenceContext()
            var successes = 0, failures = 0
            let operation = PresenceAttempt(context: context, binding: binding, validate: {
                if kind == 2 { throw ApprovalFailure.cancelled }
            }, completion: { result in
                precondition(context.invalidations > 0)
                switch result { case .success: successes += 1; case .failure: failures += 1 }
            })
            try operation.begin()
            if kind == 0 { operation.cancel() }
            context.reply?(kind != 1)
            context.reply?(true)
            precondition(successes == 0 && context.invalidations > 0 && failures == (kind == 0 ? 0 : 1))
        }
        for field in ["head", "generation", "nonce", "digest", "text"] {
            let changed = fields.mutableCopy() as! NSMutableDictionary
            changed[field] = field == "generation" ? 8 : (field == "text" ? "Different recovery delay." : String(repeating: "9", count: 64))
            let alteredCard = try ApprovalCard.decode(changed, requestedID: hash, now: 100)
            let altered = PresenceBinding(snapshot: WindowSnapshot(windowID: binding.snapshot.windowID, card: alteredCard), caller: caller)
            let result = LocalPresenceResult(binding: binding)
            refused { try result.consume(binding: altered) }
            refused { try result.consume(binding: binding) }
        }
        for changedCaller in [CallerBinding(authority: String(repeating: "9", count: 64), guiCDHash: caller.guiCDHash, session: caller.session),
                              CallerBinding(authority: caller.authority, guiCDHash: String(repeating: "9", count: 40), session: caller.session),
                              CallerBinding(authority: caller.authority, guiCDHash: caller.guiCDHash, session: OwnerSession(owner: 502, asid: 7)),
                              CallerBinding(authority: caller.authority, guiCDHash: caller.guiCDHash, session: OwnerSession(owner: 501, asid: 8))] {
            let altered = PresenceBinding(snapshot: binding.snapshot, caller: changedCaller)
            let result = LocalPresenceResult(binding: binding)
            refused { try result.consume(binding: altered) }
        }
        var capture: MockCapture? = MockCapture()
        weak var retained = capture
        let retainedAttempt = PresenceAttempt(context: MockPresenceContext(), binding: binding,
            validate: { [token = capture!] in _ = token }, completion: { _ in })
        capture = nil
        retainedAttempt.cancel()
        precondition(retained == nil)
        let result = LocalPresenceResult(binding: binding)
        let other = PresenceBinding(snapshot: WindowSnapshot(windowID: UUID(), card: card), caller: caller)
        refused { try result.consume(binding: other) }
        refused { try result.consume(binding: binding) }
        print("Fresh mock local presence, invalidation and one-shot binding checks passed")
    }
}
#endif
