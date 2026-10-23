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
