// Generated proposal. No dispatch or authority is implemented. Schema 3f7ee4fb3ed4b13bff6301271c7add44ed4d8ac53aa52929ccf528f080b09fec
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
