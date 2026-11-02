//! Test-data generation through the reviewed upstream codec, not producer authentication.
use codex_hooks::{NativeSessionContext, NativeSessionSource};
use codex_protocol::ThreadId;

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|value| format!("{value:02x}")).collect()
}
fn label(value: &str) -> ThreadId {
    ThreadId::from_string(value).expect("diagnostic UUID")
}
fn emit(name: &str, source: &NativeSessionSource) {
    let context = source.actor_context().expect("actual source context");
    println!(
        "{name}|{}|{}|{}|{}",
        hex(&context.actor().canonical_launch_id()),
        hex(&context.canonical_bytes()),
        hex(&context.actor().source_uuid_bytes()),
        context.actor().serial()
    );
}
fn main() {
    // Diagnostic labels intentionally contradict actual placement and use varied UUID versions.
    let history = NativeSessionContext::new(
        label("11111111-1111-1111-8111-111111111111"),
        label("00000000-0000-0000-0000-000000000000"),
        Some(label("77777777-7777-7777-8777-777777777777")),
    );
    let (_root_owner, root) = NativeSessionSource::birth_root(history).expect("source root");
    emit("root_with_diagnostic_parent", &root);
    let child_labels = NativeSessionContext::new(history.actor(), history.root(), None);
    let (_child_owner, child) = NativeSessionSource::birth_child(
        child_labels,
        &root.parent_reference().expect("source parent"),
    )
    .expect("source child");
    emit("child_without_diagnostic_parent", &child);
    let nested_labels =
        NativeSessionContext::new(history.actor(), history.root(), history.direct_parent());
    let (_nested_owner, nested) = NativeSessionSource::birth_child(
        nested_labels,
        &child.parent_reference().expect("source parent"),
    )
    .expect("source grandchild");
    emit("grandchild", &nested);
    let (_reopened_owner, reopened) =
        NativeSessionSource::birth_root(history).expect("fresh history root");
    emit("fresh_root_same_history", &reopened);
    let plain = NativeSessionContext::new(history.actor(), history.root(), None);
    let (_plain_owner, plain_root) = NativeSessionSource::birth_root(plain).expect("plain root");
    emit("root_without_diagnostic_parent", &plain_root);
}
