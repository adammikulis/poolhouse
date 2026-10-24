import type { CertificateDigest, DeviceId, NodeIdentity, U32, U64 } from "./node";

declare const device: DeviceId;
declare const certificate: CertificateDigest;
declare const identity: NodeIdentity;
declare const sequence: U64;
const publicId: DeviceId = device;
const version: U32 = identity.contract_version;

// @ts-expect-error Certificate digests are distinct from device identities.
const substitutedDevice: DeviceId = certificate;
// @ts-expect-error Unsigned values retain nominal widths and string encoding.
const narrowedSequence: U32 = sequence;
// @ts-expect-error JSON numbers cannot represent this wire scalar.
const numericSequence: U64 = 1;

void [publicId, version, substitutedDevice, narrowedSequence, numericSequence];

import type { NodeOperations } from "./node_operations";
import type { JobId, ResourceGeneration, ProjectId, OptionalExitCode, Boolean as NodeBoolean } from "./node";
declare const jobId: JobId;
declare const generation: ResourceGeneration;
declare const projectId: ProjectId;
const cancelRequest: NodeOperations["jobs.cancel"]["request"] = {job_id: jobId, generation, request_id: "00000000-0000-4000-8000-000000000001" as import("./node").RequestId};
void cancelRequest;
// @ts-expect-error project identity cannot replace a resource generation
const wrongGeneration: ResourceGeneration = projectId;
void wrongGeneration;
const noExitCode: OptionalExitCode = null;
void noExitCode;
// @ts-expect-error JSON integer cannot replace a boolean
const wrongBoolean: NodeBoolean = 1;
void wrongBoolean;

import type { JobListCursor, JobOutputCursor, JobViewList } from "./node";
declare const listCursor: JobListCursor;
// @ts-expect-error list cursor cannot resume output
const wrongCursor: JobOutputCursor = listCursor;
void wrongCursor;
declare const jobPage: JobViewList;
// @ts-expect-error generated lists are immutable
jobPage.push({});

import type { AgentId, TaskExecutionId } from "./node";
declare const agentId: AgentId;
// @ts-expect-error stable agent identity is not an execution identity
const wrongExecution: TaskExecutionId = agentId;
void wrongExecution;
declare const taskStop: NodeOperations["agents.task.stop"]["request"];
// @ts-expect-error task stop cannot become general agent control
const generalStop: NodeOperations["agents.stop"]["request"] = { ...taskStop, task: taskStop.task };
void generalStop;

import { unixMicrosFromParts } from "./node";
const timestamp = unixMicrosFromParts(1n, 999999n);
void timestamp;
// @ts-expect-error timestamp components require exact bigint values
unixMicrosFromParts(1, 0n);


import { finiteF64FromNumber, finiteF64ToNumber } from "./node";
import type { FiniteF64 } from "./node";
const finiteBits: FiniteF64 = finiteF64FromNumber(-0);
const finiteNumber: number = finiteF64ToNumber(finiteBits);
// @ts-expect-error Wire bits are not a raw JSON number.
const rawFinite: FiniteF64 = 1;
// @ts-expect-error Conversion does not coerce strings to numbers.
finiteF64FromNumber("1");
void [finiteNumber, rawFinite];
