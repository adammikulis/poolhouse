//go:build darwin

package main

/*
#include <stdint.h>
#include <stddef.h>

typedef struct { const void *data; uint64_t size; } ph_bytes;
typedef struct {
    ph_bytes bundle, trusted_root;
    ph_bytes san, repository_uri, repository_id, source_digest, source_ref;
    ph_bytes signer_uri, signer_digest, config_uri, config_digest, trigger, run_uri;
    uint8_t installation[32];
    uint8_t policy_digest[32];
    uint64_t generation;
    int artifact_fd;
} ph_provenance_input;
typedef struct {
    uint8_t artifact_sha256[32];
    uint8_t installation[32];
    uint8_t policy_digest[32];
    uint64_t generation;
    uint64_t device, inode, size;
} ph_provenance_result;
*/
import "C"

import (
	"io"
	"os"
	"unsafe"

	"github.com/sigstore/sigstore-go/pkg/root"
	"golang.org/x/sys/unix"
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

// PhProvenanceVerify is a bounded cryptographic predicate, not an authorization.
// Its input and output are private, immutable service memory, never an IPC request or capability.
// The service serializes FD ownership, and holds and revalidates that FD through activation.
// The service must authenticate root and policy, protect the artifact and revalidate
// the held generation through its activation transaction before using this result.
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
	snapshot := *input
	input = &snapshot
	if input.generation == 0 || !nonzero(C.GoBytes(unsafe.Pointer(&input.installation[0]), 32)) || !nonzero(C.GoBytes(unsafe.Pointer(&input.policy_digest[0]), 32)) {
		return 0
	}
	bundle, ok := copyInput(input.bundle, maxBundle)
	if !ok {
		return 0
	}
	rootBytes, ok := copyInput(input.trusted_root, maxBundle)
	if !ok {
		return 0
	}
	fields := []C.ph_bytes{input.san, input.repository_uri, input.repository_id, input.source_digest, input.source_ref, input.signer_uri, input.signer_digest, input.config_uri, input.config_digest, input.trigger, input.run_uri}
	values := make([]string, len(fields))
	for i, f := range fields {
		b, valid := copyInput(f, 2048)
		if !valid {
			return 0
		}
		values[i] = string(b)
	}
	p := policy{san: values[0], repoURI: values[1], repoID: values[2], sourceDigest: values[3], sourceRef: values[4], signerURI: values[5], signerDigest: values[6], configURI: values[7], configDigest: values[8], trigger: values[9], runURI: values[10]}
	tr, err := root.NewTrustedRootFromJSON(rootBytes)
	if err != nil {
		return 0
	}
	fd, err := unix.FcntlInt(uintptr(input.artifact_fd), unix.F_DUPFD_CLOEXEC, 0)
	if err != nil {
		return 0
	}
	file := os.NewFile(uintptr(fd), "protected artifact")
	if file == nil {
		unix.Close(fd)
		return 0
	}
	defer file.Close()
	var before, after unix.Stat_t
	flags, err := unix.FcntlInt(file.Fd(), unix.F_GETFL, 0)
	if err != nil || flags&unix.O_ACCMODE != unix.O_RDONLY || unix.Fstat(fd, &before) != nil ||
		before.Mode&unix.S_IFMT != unix.S_IFREG || before.Uid != 0 || before.Nlink != 1 || before.Mode&022 != 0 || before.Size < 0 || before.Size > maxArtifact {
		return 0
	}
	verified, err := verifyBundle(bundle, io.NewSectionReader(file, 0, before.Size), tr, p)
	if err != nil || unix.Fstat(fd, &after) != nil || before.Dev != after.Dev || before.Ino != after.Ino || before.Size != after.Size || before.Mode != after.Mode || before.Uid != after.Uid || before.Gid != after.Gid || before.Nlink != after.Nlink || before.Flags != after.Flags || before.Gen != after.Gen || before.Mtim != after.Mtim || before.Ctim != after.Ctim {
		return 0
	}
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.artifact_sha256[0])), 32), verified.artifactSHA256[:])
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.installation[0])), 32), C.GoBytes(unsafe.Pointer(&input.installation[0]), 32))
	copy(unsafe.Slice((*byte)(unsafe.Pointer(&output.policy_digest[0])), 32), C.GoBytes(unsafe.Pointer(&input.policy_digest[0]), 32))
	output.generation = input.generation
	output.device = C.uint64_t(before.Dev)
	output.inode = C.uint64_t(before.Ino)
	output.size = C.uint64_t(before.Size)
	return 1
}
