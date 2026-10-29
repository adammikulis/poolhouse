//go:build darwin

package main

/*
#include <stdint.h>
#include <stddef.h>
typedef struct { const void *data; uint64_t size; } ph_bytes;
typedef struct { int64_t version; uint8_t digest[32]; } ph_version;
typedef struct {
 ph_bytes bundle, oidc_token, prior_root, roots[32], timestamp, snapshot, targets, target;
 uint32_t root_count;
 ph_bytes jwks;
 ph_bytes san, repository_uri, repository_id, source_digest, source_ref;
 ph_bytes signer_uri, signer_digest, config_uri, config_digest, trigger, run_uri;
 ph_bytes repository, job_workflow_ref, job_name;
 uint8_t installation[32], head[32], policy_digest[32], nonce[32];
 uint8_t source[20], workflow[20];
 ph_version root_version, timestamp_version, snapshot_version, targets_version;
 uint64_t generation, repository_number, run_number, attempt, check_run, artifact_id;
 int64_t previous_time, trusted_time;
 int stage_fd, archive_fd;
} ph_provenance_input;
typedef struct {
 uint8_t artifact_sha256[32], archive_sha256[32], installation[32], head[32], policy_digest[32], nonce[32], target_sha256[32], jwks_sha256[32], oidc_key_sha256[32];
 ph_version root_version, timestamp_version, snapshot_version, targets_version;
 uint64_t generation, device, inode, size, archive_device, archive_inode, archive_size, artifact_id;
 int64_t trusted_time, producer_observed_time;
} ph_provenance_result;
*/
import "C"

import (
	"context"
	"crypto/sha256"
	"golang.org/x/sys/unix"
	"io"
	"os"
	"time"
	"unsafe"
)

func main() {}
func copyInput(value C.ph_bytes, limit uint64) ([]byte, bool) {
	if value.data == nil || value.size == 0 || uint64(value.size) > limit {
		return nil, false
	}
	return C.GoBytes(unsafe.Pointer(value.data), C.int(value.size)), true
}
func nonzero(b []byte) bool {
	for _, x := range b {
		if x != 0 {
			return true
		}
	}
	return false
}
func copy32(b *C.uint8_t) (out [32]byte) { copy(out[:], C.GoBytes(unsafe.Pointer(b), 32)); return }
func copy20(b *C.uint8_t) (out [20]byte) { copy(out[:], C.GoBytes(unsafe.Pointer(b), 20)); return }
func version(v C.ph_version) trustVersion {
	return trustVersion{version: int64(v.version), digest: copy32(&v.digest[0])}
}
func setVersion(dst *C.ph_version, v trustVersion) {
	dst.version = C.int64_t(v.version)
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&dst.digest[0])), 32), v.digest[:])
}

// PhProvenanceVerify prepares a candidate, not an authorization or a committed
// witness. Only an authenticated service may call it with immutable private
// buffers and an FD whose ownership is serialized. The service must create and journal the protected stage before this call, retain and
// revalidate that FD and atomically consume the nonce/advance the exact prior
// checkpoint before treating the candidate as an effect.
//
//export PhProvenanceVerify
func PhProvenanceVerify(input *C.ph_provenance_input, output *C.ph_provenance_result) (status C.int) {
	if output == nil {
		return 0
	}
	*output = C.ph_provenance_result{}
	defer func() {
		if recover() != nil {
			*output = C.ph_provenance_result{}
			status = 0
		}
	}()
	if input == nil {
		return 0
	}
	snap := *input
	input = &snap
	if input.generation == 0 || input.previous_time <= 0 || input.trusted_time < input.previous_time || input.root_count > 32 {
		return 0
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	installation, head, policyDigest, nonce := copy32(&input.installation[0]), copy32(&input.head[0]), copy32(&input.policy_digest[0]), copy32(&input.nonce[0])
	source, workflow := copy20(&input.source[0]), copy20(&input.workflow[0])
	if !nonzero(installation[:]) || !nonzero(head[:]) || !nonzero(policyDigest[:]) || !nonzero(nonce[:]) || !nonzero(source[:]) || !nonzero(workflow[:]) {
		return 0
	}
	fields := []C.ph_bytes{input.bundle, input.oidc_token, input.prior_root, input.timestamp, input.snapshot, input.targets, input.target, input.san, input.repository_uri, input.repository_id, input.source_digest, input.source_ref, input.signer_uri, input.signer_digest, input.config_uri, input.config_digest, input.trigger, input.run_uri, input.repository, input.job_workflow_ref, input.job_name, input.jwks}
	var aggregate uint64
	for i, field := range fields {
		limit := uint64(2048)
		if i <= 6 {
			limit = maxBundle
		} else if i == 21 {
			limit = 65536
		}
		if field.size == 0 || uint64(field.size) > limit || aggregate > 24*1024*1024-uint64(field.size) {
			return 0
		}
		aggregate += uint64(field.size)
	}
	for i := 0; i < int(input.root_count); i++ {
		size := uint64(input.roots[i].size)
		if size == 0 || size > maxBundle || aggregate > 24*1024*1024-size {
			return 0
		}
		aggregate += size
	}
	vals := make([][]byte, len(fields))
	for i, f := range fields {
		limit := uint64(2048)
		if i <= 6 {
			limit = maxBundle
		} else if i == 21 {
			limit = 65536
		}
		var ok bool
		vals[i], ok = copyInput(f, limit)
		if !ok {
			return 0
		}
	}
	if len(vals[1]) > 16384 {
		return 0
	}
	roots := make([][]byte, int(input.root_count))
	for i := range roots {
		var ok bool
		roots[i], ok = copyInput(input.roots[i], maxBundle)
		if !ok {
			return 0
		}
	}
	cp := trustCheckpoint{installation: installation, head: head, generation: uint64(input.generation), root: version(input.root_version), timestamp: version(input.timestamp_version), snapshot: version(input.snapshot_version), targets: version(input.targets_version), time: time.Unix(int64(input.previous_time), 0).UTC()}
	update := trustUpdate{priorRoot: vals[2], roots: roots, timestamp: vals[3], snapshot: vals[4], targets: vals[5], target: vals[6]}
	now := time.Unix(int64(input.trusted_time), 0).UTC()
	trust, err := prepareTrust(cp, update, now)
	if err != nil {
		return 0
	}
	p := policy{san: string(vals[7]), repoURI: string(vals[8]), repoID: string(vals[9]), sourceDigest: string(vals[10]), sourceRef: string(vals[11]), signerURI: string(vals[12]), signerDigest: string(vals[13]), configURI: string(vals[14]), configDigest: string(vals[15]), trigger: string(vals[16]), runURI: string(vals[17])}
	producer := producerPolicy{installation: installation, nonce: nonce, source: source, workflow: workflow, repositoryID: uint64(input.repository_number), runID: uint64(input.run_number), attempt: uint64(input.attempt), checkRunID: uint64(input.check_run), repository: string(vals[18]), ref: p.sourceRef, event: p.trigger, jobWorkflowRef: string(vals[19])}
	file, before, ok := openStage(int(input.stage_fd))
	if !ok {
		return 0
	}
	defer file.Close()
	archive, archiveBefore, ok := openProtected(int(input.archive_fd))
	if !ok {
		return 0
	}
	defer archive.Close()
	archiveDigest, extractedDigest, extractedSize, err := extractArchive(ctx, archive, archiveBefore.Size, file)
	if err != nil {
		return 0
	}
	var stageAfter unix.Stat_t
	if unix.Fstat(int(file.Fd()), &stageAfter) != nil || stageAfter.Dev != before.Dev || stageAfter.Ino != before.Ino || stageAfter.Size != extractedSize || stageAfter.Mode != before.Mode || stageAfter.Uid != before.Uid || stageAfter.Gid != before.Gid || stageAfter.Nlink != before.Nlink || stageAfter.Flags != before.Flags || stageAfter.Gen != before.Gen {
		return 0
	}
	verified, err := verifyBundle(vals[0], io.NewSectionReader(file, 0, stageAfter.Size), vals[1], vals[21], trust.material, p)
	if err != nil || ctx.Err() != nil || verified.artifactSHA256 != extractedDigest {
		return 0
	}
	keys, err := parseJWKS(vals[21])
	if err != nil {
		return 0
	}
	candidate, err := verifyProducer(string(vals[1]), keys, producer, verified, now)
	if err != nil || ctx.Err() != nil {
		return 0
	}
	if err = newProducerAPI().verify(ctx, candidate, uint64(input.artifact_id), archiveDigest, uint64(archiveBefore.Size), string(vals[20])); err != nil {
		return 0
	}
	if ctx.Err() != nil || !sameProtected(file, stageAfter) || !sameProtected(archive, archiveBefore) {
		return 0
	}
	targetDigest := sha256.Sum256(vals[6])
	jwksDigest := sha256.Sum256(vals[21])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.artifact_sha256[0])), 32), verified.artifactSHA256[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.archive_sha256[0])), 32), archiveDigest[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.installation[0])), 32), installation[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.head[0])), 32), head[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.policy_digest[0])), 32), policyDigest[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.nonce[0])), 32), nonce[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.target_sha256[0])), 32), targetDigest[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.jwks_sha256[0])), 32), jwksDigest[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.oidc_key_sha256[0])), 32), candidate.issuerKey[:])
	setVersion(&output.root_version, trust.next.root)
	setVersion(&output.timestamp_version, trust.next.timestamp)
	setVersion(&output.snapshot_version, trust.next.snapshot)
	setVersion(&output.targets_version, trust.next.targets)
	output.generation = input.generation
	output.device = C.uint64_t(stageAfter.Dev)
	output.inode = C.uint64_t(stageAfter.Ino)
	output.size = C.uint64_t(stageAfter.Size)
	output.archive_device = C.uint64_t(archiveBefore.Dev)
	output.archive_inode = C.uint64_t(archiveBefore.Ino)
	output.archive_size = C.uint64_t(archiveBefore.Size)
	output.artifact_id = input.artifact_id
	output.trusted_time = input.trusted_time
	output.producer_observed_time = C.int64_t(candidate.observed.Unix())
	return 1
}

func openProtected(fd int) (*os.File, unix.Stat_t, bool) {
	var st unix.Stat_t
	dup, err := unix.FcntlInt(uintptr(fd), unix.F_DUPFD_CLOEXEC, 0)
	if err != nil {
		return nil, st, false
	}
	file := os.NewFile(uintptr(dup), "protected artifact")
	if file == nil {
		unix.Close(dup)
		return nil, st, false
	}
	flags, err := unix.FcntlInt(file.Fd(), unix.F_GETFL, 0)
	if err != nil || flags&unix.O_ACCMODE != unix.O_RDONLY || unix.Fstat(dup, &st) != nil || st.Mode&unix.S_IFMT != unix.S_IFREG || st.Uid != 0 || st.Nlink != 1 || st.Mode&022 != 0 || st.Size < 0 || st.Size > maxArtifact {
		file.Close()
		return nil, st, false
	}
	return file, st, true
}
func sameProtected(file *os.File, before unix.Stat_t) bool {
	var after unix.Stat_t
	if unix.Fstat(int(file.Fd()), &after) != nil {
		return false
	}
	return before.Dev == after.Dev && before.Ino == after.Ino && before.Size == after.Size && before.Mode == after.Mode && before.Uid == after.Uid && before.Gid == after.Gid && before.Nlink == after.Nlink && before.Flags == after.Flags && before.Gen == after.Gen && before.Mtim == after.Mtim && before.Ctim == after.Ctim
}

func openStage(fd int) (*os.File, unix.Stat_t, bool) {
	var st unix.Stat_t
	dup, err := unix.FcntlInt(uintptr(fd), unix.F_DUPFD_CLOEXEC, 0)
	if err != nil {
		return nil, st, false
	}
	file := os.NewFile(uintptr(dup), "service-owned stage")
	if file == nil {
		unix.Close(dup)
		return nil, st, false
	}
	flags, err := unix.FcntlInt(file.Fd(), unix.F_GETFL, 0)
	if err != nil || flags&unix.O_ACCMODE != unix.O_RDWR || flags&unix.O_APPEND != 0 || unix.Fstat(dup, &st) != nil || st.Mode&unix.S_IFMT != unix.S_IFREG || st.Uid != 0 || st.Nlink != 1 || st.Mode&07777 != 0600 || st.Size != 0 {
		file.Close()
		return nil, st, false
	}
	return file, st, true
}
