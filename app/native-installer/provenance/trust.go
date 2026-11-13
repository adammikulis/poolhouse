package main

import (
	"crypto/sha256"
	"time"

	"github.com/sigstore/sigstore-go/pkg/root"
	"github.com/theupdateframework/go-tuf/v2/metadata/trustedmetadata"
)

// These are private preparation values, not authenticated store witnesses. The
// authority must supply checkpoint bytes from its protected transaction and CAS
// the exact prior head before making any resulting material active.
type trustVersion struct {
	version int64
	digest  [32]byte
}
type trustCheckpoint struct {
	installation, head           [32]byte
	generation                   uint64
	root                         trustVersion
	timestamp, snapshot, targets trustVersion
	time                         time.Time
}
type trustUpdate struct {
	priorRoot                            []byte
	roots                                [][]byte
	timestamp, snapshot, targets, target []byte
}
type trustCandidate struct {
	prior    trustCheckpoint
	next     trustCheckpoint
	material *root.TrustedRoot
}

func bounded(data []byte) bool { return len(data) > 0 && len(data) <= maxBundle }
func accepted(previous trustVersion, version int64, data []byte) (trustVersion, bool) {
	if version <= 0 || !bounded(data) {
		return trustVersion{}, false
	}
	digest := sha256.Sum256(data)
	if version < previous.version || (version == previous.version && digest != previous.digest) {
		return trustVersion{}, false
	}
	return trustVersion{version: version, digest: digest}, true
}

// prepareTrust verifies a fixed Sigstore TUF target without mutating storage or
// conferring authority. Its checkpoint and reference time must come from an
// independently authenticated installation and protected monotonic time source.
func prepareTrust(cp trustCheckpoint, update trustUpdate, now time.Time) (result *trustCandidate, err error) {
	defer func() {
		if recover() != nil {
			result = nil
			err = invalid
		}
	}()
	for _, previous := range []trustVersion{cp.timestamp, cp.snapshot, cp.targets} {
		if previous.version < 0 || (previous.version == 0) != (previous.digest == [32]byte{}) {
			return nil, invalid
		}
	}
	total := len(update.priorRoot) + len(update.timestamp) + len(update.snapshot) + len(update.targets) + len(update.target)
	for _, data := range update.roots {
		if len(data) > maxBundle || total > 16*1024*1024-len(data) {
			return nil, invalid
		}
		total += len(data)
	}
	if total > 16*1024*1024 {
		return nil, invalid
	}
	if cp.generation == 0 || !nonzero(cp.installation[:]) || !nonzero(cp.head[:]) || cp.root.version <= 0 ||
		cp.time.IsZero() || now.IsZero() || now.Before(cp.time) || !bounded(update.priorRoot) ||
		sha256.Sum256(update.priorRoot) != cp.root.digest || len(update.roots) > 32 ||
		!uniqueJSON(update.priorRoot) || !uniqueJSON(update.timestamp) || !uniqueJSON(update.snapshot) || !uniqueJSON(update.targets) || !uniqueJSON(update.target) {
		return nil, invalid
	}
	tm, err := trustedmetadata.New(update.priorRoot)
	if err != nil || tm.Root.Signed.Version != cp.root.version {
		return nil, invalid
	}
	tm.RefTime = now.UTC()
	var finalRoot = update.priorRoot
	for _, next := range update.roots {
		if !uniqueJSON(next) {
			return nil, invalid
		}
		if _, err = tm.UpdateRoot(next); err != nil {
			return nil, invalid
		}
		finalRoot = next
	}
	if !tm.Root.Signed.ConsistentSnapshot {
		return nil, invalid
	}
	nr, ok := accepted(cp.root, tm.Root.Signed.Version, finalRoot)
	if !ok {
		return nil, invalid
	}
	timestamp, err := tm.UpdateTimestamp(update.timestamp)
	if err != nil {
		return nil, invalid
	}
	nt, ok := accepted(cp.timestamp, timestamp.Signed.Version, update.timestamp)
	if !ok {
		return nil, invalid
	}
	snapshot, err := tm.UpdateSnapshot(update.snapshot, false)
	if err != nil {
		return nil, invalid
	}
	ns, ok := accepted(cp.snapshot, snapshot.Signed.Version, update.snapshot)
	if !ok {
		return nil, invalid
	}
	targets, err := tm.UpdateTargets(update.targets)
	if err != nil {
		return nil, invalid
	}
	ng, ok := accepted(cp.targets, targets.Signed.Version, update.targets)
	if !ok {
		return nil, invalid
	}
	target, ok := targets.Signed.Targets["trusted_root.json"]
	if !ok || target == nil || len(target.Hashes) == 0 || target.VerifyLengthHashes(update.target) != nil {
		return nil, invalid
	}
	material, err := root.NewTrustedRootFromJSON(update.target)
	if err != nil {
		return nil, invalid
	}
	next := cp
	next.root = nr
	next.timestamp = nt
	next.snapshot = ns
	next.targets = ng
	next.time = now.UTC()
	return &trustCandidate{prior: cp, next: next, material: material}, nil
}
