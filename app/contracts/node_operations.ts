// Generated proposal. No dispatch or authority is implemented. Schema e887358b9319b84157e7b99ea7129db0a86daf6c0e61d5e6c80ef4f59bfe79e8
import type { JobCancelRequest, JobError, JobGetRequest, JobView } from "./node";
export interface NodeOperations {
  readonly "jobs.get": { readonly request: JobGetRequest; readonly response: JobView; readonly error: JobError };
  readonly "jobs.cancel": { readonly request: JobCancelRequest; readonly response: JobView; readonly error: JobError };
}
