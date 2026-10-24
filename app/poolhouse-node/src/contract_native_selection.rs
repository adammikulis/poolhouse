// Generated source observation DATA only; no admitted catalog or rights.
use super::{Case, Type};
pub(super) const VERSION: u16 = 1;
pub(super) const INVOCATION_VERSION: u16 = 2;
pub(super) const REGULAR: u16 = 1;
pub(super) const MEMORY_CLEANUP: u16 = 2;
pub(super) const DECLARATION: usize = 19;
pub(super) const SOURCE_PATH: usize = 15;
pub(super) const TYPES: &[Type] = &[
    Type::Unit, // Unit
    Type::String, // String
    Type::Bool, // Bool
    Type::I64, // I64
    Type::U64, // U64
    Type::Path, // Path
    Type::EventKind, // EventKind
    Type::ValueObject, // ValueObject
    Type::Record(&[5, 1, 1, 20, 21]), // DeclaredPluginTrust
    Type::Record(&[1, 1, 20, 22, 1, 1]), // ExecutorSource
    Type::Record(&[2, 23, 6, 24, 4, 20, 4, 15, 17, 3]), // ConfiguredHeader
    Type::Record(&[10, 1, 2, 18]), // UnmeasuredCommandDeclaration
    Type::Record(&[10, 1, 1, 7]), // UnmeasuredMcpDeclaration
    Type::Record(&[1, 25, 18]), // UnmeasuredNotifyDeclaration
    Type::Record(&[1]), // OpaqueCallback
    Type::Union(&[Case { tag: 1, item: 5 }, Case { tag: 2, item: 9 }]), // HandlerSourcePath
    Type::Union(&[Case { tag: 1, item: 1 }, Case { tag: 2, item: 1 }, Case { tag: 3, item: 1 }]), // Matcher
    Type::Union(&[Case { tag: 1, item: 0 }, Case { tag: 2, item: 0 }, Case { tag: 3, item: 0 }, Case { tag: 4, item: 0 }, Case { tag: 5, item: 0 }, Case { tag: 6, item: 0 }, Case { tag: 7, item: 0 }, Case { tag: 8, item: 0 }, Case { tag: 9, item: 0 }, Case { tag: 10, item: 0 }, Case { tag: 11, item: 0 }]), // HookSource
    Type::Union(&[Case { tag: 1, item: 0 }]), // EnvironmentObservation
    Type::Union(&[Case { tag: 1, item: 11 }, Case { tag: 2, item: 12 }, Case { tag: 3, item: 13 }, Case { tag: 4, item: 14 }]), // Declaration
    Type::Optional(1), // OptionalString
    Type::Optional(5), // OptionalPath
    Type::Optional(7), // OptionalValueObject
    Type::Optional(8), // OptionalPluginTrust
    Type::Optional(16), // OptionalMatcher
    Type::List(1), // Strings
];
