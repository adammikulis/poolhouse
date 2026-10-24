// Generated from native_boundaries/native_message.toml. Private DATA encoder only.
const VERSION: u16 = 1;
const SOURCE_FEATURE: u16 = 1;
impl<'o, 's, 'v> Encoder<'o, 's, 'v> {
fn pre_tool_use(&mut self, value: &'v crate::PreToolUseRequest) -> Result<(), EncodeError> {
let crate::PreToolUseRequest { session_id, turn_id, subagent, cwd, transcript_path, model, permission_mode, tool_name, matcher_aliases, tool_use_id, tool_input } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.flag(subagent.is_some())?; if let Some(item) = subagent.as_ref() { self.subagent(item)?; }
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(permission_mode)?;
self.string(tool_name)?;
self.count(matcher_aliases.len())?; for item in matcher_aliases { self.string(item)?; }
self.string(tool_use_id)?;
self.value(tool_input)?;
Ok(())
}
fn post_tool_use(&mut self, value: &'v crate::PostToolUseRequest) -> Result<(), EncodeError> {
let crate::PostToolUseRequest { session_id, turn_id, subagent, cwd, transcript_path, model, permission_mode, tool_name, matcher_aliases, tool_use_id, tool_input, tool_response } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.flag(subagent.is_some())?; if let Some(item) = subagent.as_ref() { self.subagent(item)?; }
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(permission_mode)?;
self.string(tool_name)?;
self.count(matcher_aliases.len())?; for item in matcher_aliases { self.string(item)?; }
self.string(tool_use_id)?;
self.value(tool_input)?;
self.value(tool_response)?;
Ok(())
}
fn pre_compact(&mut self, value: &'v crate::PreCompactRequest) -> Result<(), EncodeError> {
let crate::PreCompactRequest { session_id, turn_id, subagent, cwd, transcript_path, model, trigger } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.flag(subagent.is_some())?; if let Some(item) = subagent.as_ref() { self.subagent(item)?; }
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(trigger)?;
Ok(())
}
fn post_compact(&mut self, value: &'v crate::PostCompactRequest) -> Result<(), EncodeError> {
let crate::PostCompactRequest { session_id, turn_id, subagent, cwd, transcript_path, model, trigger } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.flag(subagent.is_some())?; if let Some(item) = subagent.as_ref() { self.subagent(item)?; }
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(trigger)?;
Ok(())
}
fn user_prompt_submit(&mut self, value: &'v crate::UserPromptSubmitRequest) -> Result<(), EncodeError> {
let crate::UserPromptSubmitRequest { session_id, turn_id, subagent, cwd, transcript_path, model, permission_mode, prompt } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.flag(subagent.is_some())?; if let Some(item) = subagent.as_ref() { self.subagent(item)?; }
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(permission_mode)?;
self.string(prompt)?;
Ok(())
}
fn session_end(&mut self, value: &'v crate::SessionEndRequest) -> Result<(), EncodeError> {
let crate::SessionEndRequest { session_id, turn_id, cwd, transcript_path } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
Ok(())
}
fn interrupt(&mut self, value: &'v crate::InterruptRequest) -> Result<(), EncodeError> {
let crate::InterruptRequest { session_id, turn_id, cwd, transcript_path, model, permission_mode, request_metadata } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(permission_mode)?;
self.flag(request_metadata.is_some())?; if let Some(item) = request_metadata.as_ref() { self.value_map(item)?; }
Ok(())
}
fn session_start(&mut self, value: &'v crate::SessionStartRequest) -> Result<(), EncodeError> {
let crate::SessionStartRequest { turn_id, session_id, cwd, transcript_path, model, permission_mode, target } = value;
self.flag(turn_id.is_some())?; if let Some(item) = turn_id.as_ref() { self.string(item)?; }
self.uuid(session_id)?;
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(permission_mode)?;
self.session_start_target(target)?;
Ok(())
}
fn stop(&mut self, value: &'v crate::StopRequest) -> Result<(), EncodeError> {
let crate::StopRequest { session_id, turn_id, cwd, transcript_path, model, permission_mode, request_metadata, stop_hook_active, last_assistant_message, target } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(permission_mode)?;
self.flag(request_metadata.is_some())?; if let Some(item) = request_metadata.as_ref() { self.value_map(item)?; }
self.flag(*stop_hook_active)?;
self.flag(last_assistant_message.is_some())?; if let Some(item) = last_assistant_message.as_ref() { self.string(item)?; }
self.stop_target(target)?;
Ok(())
}
fn permission_request(&mut self, value: &'v crate::PermissionRequestRequest) -> Result<(), EncodeError> {
let crate::PermissionRequestRequest { session_id, turn_id, subagent, cwd, transcript_path, model, permission_mode, tool_name, matcher_aliases, run_id_suffix, tool_input } = value;
self.uuid(session_id)?;
self.string(turn_id)?;
self.flag(subagent.is_some())?; if let Some(item) = subagent.as_ref() { self.subagent(item)?; }
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.string(permission_mode)?;
self.string(tool_name)?;
self.count(matcher_aliases.len())?; for item in matcher_aliases { self.string(item)?; }
self.string(run_id_suffix)?;
self.value(tool_input)?;
Ok(())
}
fn subagent(&mut self, value: &'v crate::SubagentHookContext) -> Result<(), EncodeError> {
let crate::SubagentHookContext { agent_id, agent_type } = value;
self.string(agent_id)?;
self.string(agent_type)?;
Ok(())
}
fn model_request(&mut self, value: &'v crate::ModelRequestHookInput) -> Result<(), EncodeError> {
let crate::ModelRequestHookInput { session_id, turn_id, cwd, transcript_path, model, agent_id, agent_type, event } = value;
self.string(session_id)?;
self.string(turn_id)?;
self.path(cwd.as_path())?;
self.flag(transcript_path.is_some())?; if let Some(item) = transcript_path.as_ref() { self.path(item.as_path())?; }
self.string(model)?;
self.flag(agent_id.is_some())?; if let Some(item) = agent_id.as_ref() { self.string(item)?; }
self.flag(agent_type.is_some())?; if let Some(item) = agent_type.as_ref() { self.string(item)?; }
self.model_request_event(event)?;
Ok(())
}
fn token_usage(&mut self, value: &'v codex_protocol::protocol::TokenUsage) -> Result<(), EncodeError> {
let codex_protocol::protocol::TokenUsage { input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens, total_tokens, codex_rollout_budget_units } = value;
self.put(&input_tokens.to_be_bytes())?;
self.put(&cached_input_tokens.to_be_bytes())?;
self.put(&cache_write_input_tokens.to_be_bytes())?;
self.put(&output_tokens.to_be_bytes())?;
self.put(&reasoning_output_tokens.to_be_bytes())?;
self.put(&total_tokens.to_be_bytes())?;
self.flag(codex_rollout_budget_units.is_some())?; if let Some(item) = codex_rollout_budget_units.as_ref() { self.number(item)?; }
Ok(())
}
fn legacy(&mut self, value: &'v crate::HookPayload) -> Result<(), EncodeError> {
let crate::HookPayload { session_id, cwd, client, triggered_at, hook_event } = value;
self.uuid(session_id)?;
self.path(cwd.as_path())?;
self.flag(client.is_some())?; if let Some(item) = client.as_ref() { self.string(item)?; }
self.timestamp(triggered_at)?;
self.legacy_event(hook_event)?;
Ok(())
}
fn after_agent(&mut self, value: &'v crate::HookEventAfterAgent) -> Result<(), EncodeError> {
let crate::HookEventAfterAgent { thread_id, turn_id, input_messages, last_assistant_message } = value;
self.uuid(thread_id)?;
self.string(turn_id)?;
self.count(input_messages.len())?; for item in input_messages { self.string(item)?; }
self.flag(last_assistant_message.is_some())?; if let Some(item) = last_assistant_message.as_ref() { self.string(item)?; }
Ok(())
}
fn session_start_source(&mut self, value: &'v crate::SessionStartSource) -> Result<(), EncodeError> {
match value {
crate::SessionStartSource::Startup => { self.tag(1)?;  }
crate::SessionStartSource::Resume => { self.tag(2)?;  }
crate::SessionStartSource::Clear => { self.tag(3)?;  }
crate::SessionStartSource::Compact => { self.tag(4)?;  }
crate::SessionStartSource::Fork => { self.tag(5)?;  }
}
Ok(())
}
fn session_start_target(&mut self, value: &'v crate::StartHookTarget) -> Result<(), EncodeError> {
match value {
crate::StartHookTarget::SessionStart { source } => { self.tag(1)?; self.session_start_source(source)?; }
crate::StartHookTarget::SubagentStart { turn_id, agent_id, agent_type } => { self.tag(2)?; self.string(turn_id)?; self.string(agent_id)?; self.string(agent_type)?; }
}
Ok(())
}
fn stop_target(&mut self, value: &'v crate::StopHookTarget) -> Result<(), EncodeError> {
match value {
crate::StopHookTarget::Stop => { self.tag(1)?;  }
crate::StopHookTarget::MemoryConsolidation => { self.tag(2)?;  }
crate::StopHookTarget::SubagentStop { agent_id, agent_type, agent_transcript_path } => { self.tag(3)?; self.string(agent_id)?; self.string(agent_type)?; self.flag(agent_transcript_path.is_some())?; if let Some(item) = agent_transcript_path.as_ref() { self.path(item.as_path())?; } }
}
Ok(())
}
fn model_request_status(&mut self, value: &'v crate::ModelRequestStatus) -> Result<(), EncodeError> {
match value {
crate::ModelRequestStatus::Completed => { self.tag(1)?;  }
crate::ModelRequestStatus::Failed => { self.tag(2)?;  }
crate::ModelRequestStatus::Cancelled => { self.tag(3)?;  }
}
Ok(())
}
fn model_request_event(&mut self, value: &'v crate::ModelRequestHookEvent) -> Result<(), EncodeError> {
match value {
crate::ModelRequestHookEvent::PreModelRequest => { self.tag(1)?;  }
crate::ModelRequestHookEvent::PostModelRequest { status, error, token_usage } => { self.tag(2)?; self.model_request_status(status)?; self.flag(error.is_some())?; if let Some(item) = error.as_ref() { self.string(item)?; } self.flag(token_usage.is_some())?; if let Some(item) = token_usage.as_ref() { self.token_usage(item)?; } }
}
Ok(())
}
fn legacy_event(&mut self, value: &'v crate::HookEvent) -> Result<(), EncodeError> {
match value {
crate::HookEvent::AfterAgent { event } => { self.tag(1)?; self.after_agent(event)?; }
}
Ok(())
}
fn original_request(&mut self, value: &'v crate::native_occurrence::NativeRequest) -> Result<(), EncodeError> {
match value {
crate::native_occurrence::NativeRequest::PreToolUse(item) => { self.tag(1)?; self.pre_tool_use(item)?; }
crate::native_occurrence::NativeRequest::PermissionRequest(item) => { self.tag(2)?; self.permission_request(item)?; }
crate::native_occurrence::NativeRequest::PostToolUse(item) => { self.tag(3)?; self.post_tool_use(item)?; }
crate::native_occurrence::NativeRequest::PreCompact(item) => { self.tag(4)?; self.pre_compact(item)?; }
crate::native_occurrence::NativeRequest::PostCompact(item) => { self.tag(5)?; self.post_compact(item)?; }
crate::native_occurrence::NativeRequest::UserPromptSubmit(item) => { self.tag(6)?; self.user_prompt_submit(item)?; }
crate::native_occurrence::NativeRequest::SessionEnd(item) => { self.tag(7)?; self.session_end(item)?; }
crate::native_occurrence::NativeRequest::Interrupt(item) => { self.tag(8)?; self.interrupt(item)?; }
crate::native_occurrence::NativeRequest::SessionStart(item) => { self.tag(9)?; self.session_start(item)?; }
crate::native_occurrence::NativeRequest::Stop(item) => { self.tag(10)?; self.stop(item)?; }
crate::native_occurrence::NativeRequest::ModelRequest(item) => { self.tag(11)?; self.model_request(item)?; }
crate::native_occurrence::NativeRequest::Legacy(item) => { self.tag(12)?; self.legacy(item)?; }
#[cfg(test)] crate::native_occurrence::NativeRequest::Test(_) => return Err(EncodeError::Unsupported),
}
Ok(())
}
fn invocation(&mut self, invocation: &'v std::sync::Arc<crate::NativeInvocation>) -> Result<(), EncodeError> {
let occurrence=invocation.occurrence();
let crate::NativeOccurrenceKind::Invocation(kind)=occurrence.kind() else { return Err(EncodeError::Context); };
let context=occurrence.actor_context().ok_or(EncodeError::Context)?;
if occurrence.native_session_instance()!=context.actor() { return Err(EncodeError::Context); }
self.tag(VERSION)?;
self.tag(SOURCE_FEATURE)?;
self.put(&[if cfg!(unix) {1} else if cfg!(windows) {2} else {return Err(EncodeError::Unsupported)}])?;
self.put(&occurrence.id().canonical_launch_id())?;
self.put(&occurrence.id().canonical_event_id())?;
self.put(&occurrence.native_session_instance().canonical_event_id())?;
self.tag(captured_kind(kind))?;
self.context(context)?;
self.original_request(invocation.original())?;
Ok(())
}
}
fn captured_kind(value: crate::NativeHookKind) -> u16 {
match value {
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::PreToolUse) => 1,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::PermissionRequest) => 2,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::PostToolUse) => 3,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::PreCompact) => 4,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::PostCompact) => 5,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::UserPromptSubmit) => 6,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::SessionEnd) => 7,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::Interrupt) => 8,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::SessionStart) => 9,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::SubagentStart) => 10,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::Stop) => 11,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::SubagentStop) => 12,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::PreModelRequest) => 13,
crate::NativeHookKind::Configured(codex_protocol::protocol::HookEventName::PostModelRequest) => 14,
crate::NativeHookKind::LegacyAfterAgent => 15,
}
}
fn value_tag(value: &serde_json::Value) -> u16 {
match value {
serde_json::Value::Null => 1,
serde_json::Value::Bool(_) => 2,
serde_json::Value::String(_) => 3,
serde_json::Value::Number(_) => 4,
serde_json::Value::Array(_) => 5,
serde_json::Value::Object(_) => 6,
}
}
