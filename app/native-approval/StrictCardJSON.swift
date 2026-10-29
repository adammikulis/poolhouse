import Foundation

/// The approval protocol contains only objects, strings and unsigned integers.
/// Parsing each key before insertion rejects duplicate and escaped duplicate keys.
struct StrictCardJSON {
    private let bytes: [UInt8]
    private var index = 0

    init(_ data: Data) throws {
        guard !data.isEmpty, data.count <= 98304 else { throw ApprovalFailure.invalid }
        bytes = Array(data)
    }

    private mutating func whitespace() {
        while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 }
    }

    private mutating func take(_ byte: UInt8) throws {
        whitespace()
        guard index < bytes.count, bytes[index] == byte else { throw ApprovalFailure.invalid }
        index += 1
    }

    private mutating func string() throws -> String {
        whitespace()
        guard index < bytes.count, bytes[index] == 34 else { throw ApprovalFailure.invalid }
        let start = index
        index += 1
        while index < bytes.count {
            let byte = bytes[index]
            if byte == 34 {
                index += 1
                let fragment = Data(bytes[start..<index])
                guard let value = try JSONSerialization.jsonObject(with: fragment, options: [.fragmentsAllowed]) as? String else { throw ApprovalFailure.invalid }
                return value
            }
            guard byte >= 32 else { throw ApprovalFailure.invalid }
            if byte == 92 {
                index += 1
                guard index < bytes.count else { throw ApprovalFailure.invalid }
            }
            index += 1
        }
        throw ApprovalFailure.invalid
    }

    private mutating func value(_ depth: Int) throws -> Any {
        whitespace()
        guard index < bytes.count, depth <= 3 else { throw ApprovalFailure.invalid }
        if bytes[index] == 34 { return try string() }
        if bytes[index] == 123 {
            try take(123)
            var result: [String: Any] = [:]
            whitespace()
            if index < bytes.count && bytes[index] == 125 { index += 1; return result }
            while true {
                let key = try string()
                guard result[key] == nil, result.count < 16 else { throw ApprovalFailure.invalid }
                try take(58)
                result[key] = try value(depth + 1)
                whitespace()
                guard index < bytes.count else { throw ApprovalFailure.invalid }
                if bytes[index] == 125 { index += 1; return result }
                try take(44)
            }
        }
        let start = index
        while index < bytes.count && (48...57).contains(bytes[index]) { index += 1 }
        guard index > start, !(index - start > 1 && bytes[start] == 48),
              let number = UInt64(String(decoding: bytes[start..<index], as: UTF8.self)),
              number <= UInt64(Int64.max) else { throw ApprovalFailure.invalid }
        return NSNumber(value: number)
    }

    mutating func document() throws -> NSDictionary {
        guard let object = try value(0) as? [String: Any] else { throw ApprovalFailure.invalid }
        whitespace()
        guard index == bytes.count else { throw ApprovalFailure.invalid }
        return object as NSDictionary
    }
}
