// Generated proposal. No dispatch or authority is implemented. Schema 85edabd2d8785da6dd55700a1f5c58be18b75d44082505198120758d63b3f1ed
import type { AgentControlError, AgentControlRequest, AgentControlSnapshot, AgentGetRequest, AgentPauseResult, AgentStopResult, AgentTaskStartRequest, AgentTaskStartResult, AgentTaskStopRequest, AgentTaskStopResult, JobCancelRequest, JobError, JobGetRequest, JobListRequest, JobListResponse, JobOutputChunk, JobOutputRequest, JobView } from "./node";
export interface NodeOperations {
  readonly "jobs.get": { readonly request: JobGetRequest; readonly response: JobView; readonly error: JobError };
  readonly "jobs.cancel": { readonly request: JobCancelRequest; readonly response: JobView; readonly error: JobError };
  readonly "jobs.list": { readonly request: JobListRequest; readonly response: JobListResponse; readonly error: JobError };
  readonly "jobs.output.read": { readonly request: JobOutputRequest; readonly response: JobOutputChunk; readonly error: JobError };
  readonly "agents.get": { readonly request: AgentGetRequest; readonly response: AgentControlSnapshot; readonly error: AgentControlError };
  readonly "agents.pause": { readonly request: AgentControlRequest; readonly response: AgentPauseResult; readonly error: AgentControlError };
  readonly "agents.resume": { readonly request: AgentControlRequest; readonly response: AgentPauseResult; readonly error: AgentControlError };
  readonly "agents.stop": { readonly request: AgentControlRequest; readonly response: AgentStopResult; readonly error: AgentControlError };
  readonly "agents.task.start": { readonly request: AgentTaskStartRequest; readonly response: AgentTaskStartResult; readonly error: AgentControlError };
  readonly "agents.task.stop": { readonly request: AgentTaskStopRequest; readonly response: AgentTaskStopResult; readonly error: AgentControlError };
}
