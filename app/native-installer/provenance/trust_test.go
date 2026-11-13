package main

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"testing"
	"time"

	"github.com/sigstore/sigstore-go/pkg/testing/data"
	"github.com/sigstore/sigstore/pkg/signature"
	"github.com/theupdateframework/go-tuf/v2/metadata"
)

func trustFixture(t *testing.T) (trustCheckpoint, trustUpdate, time.Time, signature.Signer) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	signer, err := signature.LoadSigner(key, crypto.SHA256)
	if err != nil {
		t.Fatal(err)
	}
	public, err := metadata.KeyFromPublicKey(key.Public())
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Truncate(time.Second)
	expires := now.Add(time.Hour)
	r := metadata.Root(expires)
	for _, role := range []string{metadata.ROOT, metadata.TIMESTAMP, metadata.SNAPSHOT, metadata.TARGETS} {
		if err = r.Signed.AddKey(public, role); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = r.Sign(signer); err != nil {
		t.Fatal(err)
	}
	rb, err := r.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	target, err := data.TrustedRoot(t, "public-good.json").MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	tf, err := metadata.TargetFile().FromBytes("trusted_root.json", target)
	if err != nil {
		t.Fatal(err)
	}
	tg := metadata.Targets(expires)
	tg.Signed.Targets["trusted_root.json"] = tf
	if _, err = tg.Sign(signer); err != nil {
		t.Fatal(err)
	}
	tgb, err := tg.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	snap := metadata.Snapshot(expires)
	th := sha256.Sum256(tgb)
	snap.Signed.Meta["targets.json"] = &metadata.MetaFiles{Version: 1, Length: int64(len(tgb)), Hashes: metadata.Hashes{"sha256": th[:]}}
	if _, err = snap.Sign(signer); err != nil {
		t.Fatal(err)
	}
	sb, err := snap.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	stamp := metadata.Timestamp(expires)
	sh := sha256.Sum256(sb)
	stamp.Signed.Meta["snapshot.json"] = &metadata.MetaFiles{Version: 1, Length: int64(len(sb)), Hashes: metadata.Hashes{"sha256": sh[:]}}
	if _, err = stamp.Sign(signer); err != nil {
		t.Fatal(err)
	}
	tb, err := stamp.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	cp := trustCheckpoint{generation: 1, root: trustVersion{version: 1, digest: sha256.Sum256(rb)}, time: now.Add(-time.Second)}
	cp.installation[0] = 1
	cp.head[0] = 2
	return cp, trustUpdate{priorRoot: rb, timestamp: tb, snapshot: sb, targets: tgb, target: target}, now, signer
}

func TestTrustPreparationVerifiesAndRejectsTampering(t *testing.T) {
	cp, u, now, _ := trustFixture(t)
	got, err := prepareTrust(cp, u, now)
	if err != nil || got == nil || got.material == nil {
		t.Fatalf("valid metadata rejected: %v", err)
	}
	if got.next.root.version != 1 || got.next.timestamp.version != 1 || got.next.snapshot.version != 1 || got.next.targets.version != 1 {
		t.Fatal("wrong verified versions")
	}
	for name, mutate := range map[string]func(*trustUpdate){
		"root":      func(x *trustUpdate) { x.priorRoot = append([]byte(nil), x.priorRoot...); x.priorRoot[0] ^= 1 },
		"timestamp": func(x *trustUpdate) { x.timestamp = append([]byte(nil), x.timestamp...); x.timestamp[0] ^= 1 },
		"snapshot":  func(x *trustUpdate) { x.snapshot = append([]byte(nil), x.snapshot...); x.snapshot[0] ^= 1 },
		"targets":   func(x *trustUpdate) { x.targets = append([]byte(nil), x.targets...); x.targets[0] ^= 1 },
		"target":    func(x *trustUpdate) { x.target = append([]byte(nil), x.target...); x.target[0] ^= 1 },
	} {
		t.Run(name, func(t *testing.T) {
			v := u
			mutate(&v)
			if c, e := prepareTrust(cp, v, now); e == nil || c != nil {
				t.Fatal("tampered metadata accepted")
			}
		})
	}
	if c, e := prepareTrust(cp, u, cp.time.Add(-time.Second)); e == nil || c != nil {
		t.Fatal("clock rollback accepted")
	}
	if c, e := prepareTrust(cp, u, now.Add(2*time.Hour)); e == nil || c != nil {
		t.Fatal("expired metadata accepted")
	}
	stale := got.next
	stale.timestamp.version = 2
	if c, e := prepareTrust(stale, u, now); e == nil || c != nil {
		t.Fatal("version rollback accepted")
	}
	equiv := got.next
	equiv.targets.digest[0] ^= 1
	if c, e := prepareTrust(equiv, u, now); e == nil || c != nil {
		t.Fatal("same-version equivocation accepted")
	}
}

func TestTrustRootRotationMustBeSequential(t *testing.T) {
	cp, u, now, signer := trustFixture(t)
	old, err := metadata.Root().FromBytes(u.priorRoot)
	if err != nil {
		t.Fatal(err)
	}
	old.Signed.Version = 2
	old.ClearSignatures()
	if _, err = old.Sign(signer); err != nil {
		t.Fatal(err)
	}
	next, err := old.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	u.roots = [][]byte{next}
	got, err := prepareTrust(cp, u, now)
	if err != nil || got.next.root.version != 2 {
		t.Fatalf("valid root rotation rejected: %v", err)
	}
	old.Signed.Version = 3
	old.ClearSignatures()
	if _, err = old.Sign(signer); err != nil {
		t.Fatal(err)
	}
	next, err = old.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	u.roots = [][]byte{next}
	if got, err = prepareTrust(cp, u, now); err == nil || got != nil {
		t.Fatal("skipped root version accepted")
	}
}

func TestTrustJSONRejectsDuplicateAndTrailingFields(t *testing.T) {
	for _, input := range []string{`{"a":1,"a":2}`, `{"a":1,"\u0061":2}`, `{"x":{"y":1,"y":2}}`, `{} {}`, `{"a":`, `[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]`} {
		if uniqueJSON([]byte(input)) {
			t.Fatalf("accepted ambiguous JSON: %s", input)
		}
	}
	if !uniqueJSON([]byte(`{"x":[1,{"a":true}],"y":null}`)) {
		t.Fatal("valid JSON rejected")
	}
}

func TestTrustRejectsExpiredRootAndIncompleteMetadata(t *testing.T) {
	cp, u, now, signer := trustFixture(t)
	r, err := metadata.Root().FromBytes(u.priorRoot)
	if err != nil {
		t.Fatal(err)
	}
	r.Signed.Expires = now.Add(-time.Second)
	r.ClearSignatures()
	if _, err = r.Sign(signer); err != nil {
		t.Fatal(err)
	}
	u.priorRoot, err = r.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	cp.root.digest = sha256.Sum256(u.priorRoot)
	if c, e := prepareTrust(cp, u, now); e == nil || c != nil {
		t.Fatal("expired final root accepted")
	}
	cp, u, now, signer = trustFixture(t)
	stamp, err := metadata.Timestamp().FromBytes(u.timestamp)
	if err != nil {
		t.Fatal(err)
	}
	delete(stamp.Signed.Meta, "snapshot.json")
	stamp.ClearSignatures()
	if _, err = stamp.Sign(signer); err != nil {
		t.Fatal(err)
	}
	u.timestamp, err = stamp.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	if c, e := prepareTrust(cp, u, now); e == nil || c != nil {
		t.Fatal("missing snapshot metadata accepted")
	}
}

func TestTrustRotationEnforcesNewRootThreshold(t *testing.T) {
	cp, u, now, signer := trustFixture(t)
	second, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	pub, err := metadata.KeyFromPublicKey(second.Public())
	if err != nil {
		t.Fatal(err)
	}
	r, err := metadata.Root().FromBytes(u.priorRoot)
	if err != nil {
		t.Fatal(err)
	}
	r.Signed.Version = 2
	if err = r.Signed.AddKey(pub, metadata.ROOT); err != nil {
		t.Fatal(err)
	}
	r.Signed.Roles[metadata.ROOT].Threshold = 2
	r.ClearSignatures()
	if _, err = r.Sign(signer); err != nil {
		t.Fatal(err)
	}
	next, err := r.ToBytes(false)
	if err != nil {
		t.Fatal(err)
	}
	u.roots = [][]byte{next}
	if c, e := prepareTrust(cp, u, now); e == nil || c != nil {
		t.Fatal("new root threshold bypassed")
	}
}

func TestTrustJSONRejectsMalformedUnicode(t *testing.T) {
	for _, b := range [][]byte{[]byte(`{"x":"\ud800"}`), []byte(`{"x":"\udc00"}`), []byte(`{"x":"\ud800\u0041"}`), {'{', '"', 'x', '"', ':', '"', 0xff, '"', '}'}} {
		if uniqueJSON(b) {
			t.Fatal("invalid Unicode accepted")
		}
	}
	if !uniqueJSON([]byte(`{"x":"\ud83d\ude00"}`)) {
		t.Fatal("valid surrogate pair rejected")
	}
}
