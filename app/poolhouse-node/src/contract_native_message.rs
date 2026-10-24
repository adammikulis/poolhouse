// Generated from native_boundaries/native_message.toml; UNTRUSTED DATA ONLY.
use super::{Case, EnvelopeField, Type};
pub(super) const VERSION: u16 = 1;
pub(super) const SOURCE_FEATURE: u16 = 1;
pub(super) const ORIGINAL_REQUEST: usize = 34;
pub(super) const ENVELOPE: &[EnvelopeField] = &[
    EnvelopeField::Version,
    EnvelopeField::SourceFeature,
    EnvelopeField::Platform,
    EnvelopeField::Launch,
    EnvelopeField::Occurrence,
    EnvelopeField::Instance,
    EnvelopeField::CapturedKind,
    EnvelopeField::ActorContext,
    EnvelopeField::OriginalRequest,
];
pub(super) const TYPES: &[Type] = &[
    Type::Unit, // Unit
    Type::String, // String
    Type::Uuid, // Uuid
    Type::Bool, // Bool
    Type::I64, // I64
    Type::Path, // Path
    Type::Timestamp, // Timestamp
    Type::Number, // Number
    Type::Record(&[1, 1]), // Subagent
    Type::Record(&[2, 1, 37, 5, 36, 1, 1, 1, 41, 1, 33]), // PreToolUse
    Type::Record(&[2, 1, 37, 5, 36, 1, 1, 1, 41, 1, 33]), // PermissionRequest
    Type::Record(&[2, 1, 37, 5, 36, 1, 1, 1, 41, 1, 33, 33]), // PostToolUse
    Type::Record(&[2, 1, 37, 5, 36, 1, 1]), // PreCompact
    Type::Record(&[2, 1, 37, 5, 36, 1, 1]), // PostCompact
    Type::Record(&[2, 1, 37, 5, 36, 1, 1, 1]), // UserPromptSubmit
    Type::Record(&[2, 1, 5, 36]), // SessionEnd
    Type::Record(&[2, 1, 5, 36, 1, 1, 38]), // Interrupt
    Type::Record(&[27]), // SessionStartTargetFields
    Type::Record(&[1, 1, 1]), // SubagentStartFields
    Type::Record(&[35, 2, 5, 36, 1, 1, 28]), // SessionStart
    Type::Record(&[1, 1, 36]), // SubagentStopFields
    Type::Record(&[2, 1, 5, 36, 1, 1, 38, 3, 35, 29]), // Stop
    Type::Record(&[4, 4, 4, 4, 4, 4, 39]), // TokenUsage
    Type::Record(&[30, 35, 40]), // PostModelRequestFields
    Type::Record(&[1, 1, 5, 36, 1, 35, 35, 31]), // ModelRequest
    Type::Record(&[2, 1, 41, 35]), // AfterAgent
    Type::Record(&[2, 5, 35, 6, 32]), // Legacy
    Type::Union(&[Case { tag: 1, item: 0, kind: 0 }, Case { tag: 2, item: 0, kind: 0 }, Case { tag: 3, item: 0, kind: 0 }, Case { tag: 4, item: 0, kind: 0 }, Case { tag: 5, item: 0, kind: 0 }]), // SessionStartSource
    Type::Union(&[Case { tag: 1, item: 17, kind: 9 }, Case { tag: 2, item: 18, kind: 10 }]), // SessionStartTarget
    Type::Union(&[Case { tag: 1, item: 0, kind: 11 }, Case { tag: 2, item: 0, kind: 11 }, Case { tag: 3, item: 20, kind: 12 }]), // StopTarget
    Type::Union(&[Case { tag: 1, item: 0, kind: 0 }, Case { tag: 2, item: 0, kind: 0 }, Case { tag: 3, item: 0, kind: 0 }]), // ModelRequestStatus
    Type::Union(&[Case { tag: 1, item: 0, kind: 13 }, Case { tag: 2, item: 23, kind: 14 }]), // ModelRequestEvent
    Type::Union(&[Case { tag: 1, item: 25, kind: 0 }]), // LegacyEvent
    Type::Union(&[Case { tag: 1, item: 0, kind: 0 }, Case { tag: 2, item: 3, kind: 0 }, Case { tag: 3, item: 1, kind: 0 }, Case { tag: 4, item: 7, kind: 0 }, Case { tag: 5, item: 42, kind: 0 }, Case { tag: 6, item: 43, kind: 0 }]), // Value
    Type::Union(&[Case { tag: 1, item: 9, kind: 1 }, Case { tag: 2, item: 10, kind: 2 }, Case { tag: 3, item: 11, kind: 3 }, Case { tag: 4, item: 12, kind: 4 }, Case { tag: 5, item: 13, kind: 5 }, Case { tag: 6, item: 14, kind: 6 }, Case { tag: 7, item: 15, kind: 7 }, Case { tag: 8, item: 16, kind: 8 }, Case { tag: 9, item: 19, kind: 0 }, Case { tag: 10, item: 21, kind: 0 }, Case { tag: 11, item: 24, kind: 0 }, Case { tag: 12, item: 26, kind: 15 }]), // OriginalRequest
    Type::Optional(1), // OptionalString
    Type::Optional(5), // OptionalPath
    Type::Optional(8), // OptionalSubagent
    Type::Optional(43), // OptionalMap
    Type::Optional(7), // OptionalNumber
    Type::Optional(22), // OptionalTokenUsage
    Type::List(1), // Strings
    Type::List(33), // Values
    Type::Map(33), // ValueMap
];
pub(super) const MINIMUM: &[usize] = &[0, 8, 16, 1, 8, 9, 12, 8, 16, 77, 77, 79, 51, 51, 59, 34, 51, 2, 24, 47, 17, 55, 49, 4, 38, 33, 73, 2, 4, 2, 2, 2, 35, 2, 36, 1, 1, 1, 1, 1, 1, 8, 8, 8];
