// Generated proposal. No dispatch or authority is implemented. Schema e00c39c5a862274de475d5940a9f500a7d7729afe3df21d929c5c92faa7dde90
import type { JobCancelRequest, JobError, JobGetRequest, JobListRequest, JobListResponse, JobOutputChunk, JobOutputRequest, JobView } from "./node";
export interface NodeOperations {
  readonly "jobs.get": { readonly request: JobGetRequest; readonly response: JobView; readonly error: JobError };
  readonly "jobs.cancel": { readonly request: JobCancelRequest; readonly response: JobView; readonly error: JobError };
  readonly "jobs.list": { readonly request: JobListRequest; readonly response: JobListResponse; readonly error: JobError };
  readonly "jobs.output.read": { readonly request: JobOutputRequest; readonly response: JobOutputChunk; readonly error: JobError };
}
