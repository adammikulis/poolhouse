package main

import (
	"crypto/rand"
	"crypto/rsa"
	"encoding/hex"
	"math/big"
	"testing"
	"time"

	jwt "github.com/golang-jwt/jwt/v5"
)

func producerFixture(t *testing.T) (*rsa.PrivateKey, producerPolicy, *verified, jwt.MapClaims, time.Time) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	p := producerPolicy{repositoryID: 123, runID: 456, attempt: 1, checkRunID: 789, repository: "example/project", ref: "refs/heads/development", event: "push", jobWorkflowRef: "example/project/.github/workflows/build.yml@refs/heads/development"}
	p.installation[0] = 1
	p.nonce[0] = 2
	p.source[0] = 3
	p.workflow[0] = 4
	v := &verified{}
	v.artifactSHA256[0] = 5
	v.runURI = "https://github.com/example/project/actions/runs/456/attempts/1"
	v.policy = policy{repoURI: "https://github.com/example/project", repoID: "123", sourceDigest: hex.EncodeToString(p.source[:]), sourceRef: p.ref, trigger: p.event, signerURI: "https://github.com/example/project/.github/workflows/attest.yml@refs/heads/development", signerDigest: hex.EncodeToString(p.workflow[:]), runURI: v.runURI}
	issued := time.Now().UTC().Truncate(time.Second)
	v.logTimes = []time.Time{issued.Add(time.Minute)}
	now := issued.Add(6 * time.Minute)
	c := jwt.MapClaims{"iss": "https://token.actions.githubusercontent.com", "aud": producerAudience(p, v.artifactSHA256), "iat": issued.Unix(), "nbf": issued.Unix(), "exp": issued.Add(5 * time.Minute).Unix(), "repository": p.repository, "repository_id": "123", "ref": p.ref, "event_name": p.event, "sha": hex.EncodeToString(p.source[:]), "job_workflow_ref": p.jobWorkflowRef, "job_workflow_sha": hex.EncodeToString(p.workflow[:]), "run_id": "456", "run_attempt": "1", "check_run_id": "789", "runner_environment": "github-hosted"}
	return key, p, v, c, now
}
func tokenFor(t *testing.T, key *rsa.PrivateKey, c jwt.MapClaims) string {
	t.Helper()
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, c)
	token.Header["kid"] = "test-key"
	s, err := token.SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return s
}
func TestProducerBindsSignedJobToVerifiedSubject(t *testing.T) {
	key, p, v, claims, now := producerFixture(t)
	keys := map[string]*rsa.PublicKey{"test-key": &key.PublicKey}
	raw := tokenFor(t, key, claims)
	got, err := verifyProducer(raw, keys, p, v, now)
	if err != nil || got == nil {
		t.Fatalf("valid token rejected: %v", err)
	}
	want, ok := oidcKeyDigest(&key.PublicKey)
	if !ok || got.issuerKey != want {
		t.Fatal("verified key not bound")
	}
	for _, name := range []string{"iss", "aud", "repository", "repository_id", "ref", "event_name", "sha", "job_workflow_ref", "job_workflow_sha", "run_id", "run_attempt", "check_run_id", "runner_environment"} {
		t.Run(name, func(t *testing.T) {
			c := jwt.MapClaims{}
			for k, x := range claims {
				c[k] = x
			}
			c[name] = "wrong"
			if got, err := verifyProducer(tokenFor(t, key, c), keys, p, v, now); err == nil || got != nil {
				t.Fatal("mismatched claim accepted")
			}
		})
	}
	if got, err = verifyProducer(raw, keys, p, v, now.Add(-2*time.Minute)); err == nil || got != nil {
		t.Fatal("live bearer accepted before expiration")
	}
	p.nonce[0] ^= 1
	if got, err = verifyProducer(raw, keys, p, v, now); err != nil || got == nil {
		t.Fatal("reusable provenance incorrectly tied to local nonce")
	}
	v.logTimes = []time.Time{now}
	if got, err = verifyProducer(raw, keys, p, v, now); err == nil || got != nil {
		t.Fatal("timestamp outside token validity accepted")
	}
	v.logTimes = []time.Time{now.Add(-5 * time.Minute)}
	v.logTimes = nil
	if got, err = verifyProducer(raw, keys, p, v, now); err == nil || got != nil {
		t.Fatal("missing transparency timestamp accepted")
	}
	v.logTimes = []time.Time{now.Add(time.Second)}
	if got, err = verifyProducer(raw, keys, p, v, now); err == nil || got != nil {
		t.Fatal("future transparency timestamp accepted")
	}
	v.logTimes = []time.Time{now.Add(-5 * time.Minute)}
	v.runURI += "x"
	if got, err = verifyProducer(raw, keys, p, v, now); err == nil || got != nil {
		t.Fatal("cross-run provenance accepted")
	}
}

func TestProducerAllowsGitHubThumbprintAndRejectsUnknownHeaders(t *testing.T) {
	key, p, v, claims, now := producerFixture(t)
	keys := map[string]*rsa.PublicKey{"test-key": &key.PublicKey}
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	token.Header["kid"] = "test-key"
	token.Header["x5t"] = "test-thumbprint"
	raw, err := token.SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := verifyProducer(raw, keys, p, v, now); err != nil || got == nil {
		t.Fatalf("documented thumbprint header rejected: %v", err)
	}
	token.Header["jku"] = "https://example.invalid/keys"
	raw, err = token.SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := verifyProducer(raw, keys, p, v, now); err == nil || got != nil {
		t.Fatal("unrecognized key source accepted")
	}
}

func TestProducerCannotBeItsOwnJWKSAttester(t *testing.T) {
	key, p, v, claims, now := producerFixture(t)
	keys := map[string]*rsa.PublicKey{"test-key": &key.PublicKey}
	raw := tokenFor(t, key, claims)
	v.policy.signerURI = "https://github.com/" + p.jobWorkflowRef
	if got, err := verifyProducer(raw, keys, p, v, now); err == nil || got != nil {
		t.Fatal("producer self-attestation accepted")
	}
}

func TestOIDCKeyFingerprintIsKeyBound(t *testing.T) {
	a, _, _, _, _ := producerFixture(t)
	b, _, _, _, _ := producerFixture(t)
	x, ok := oidcKeyDigest(&a.PublicKey)
	if !ok {
		t.Fatal("valid key rejected")
	}
	y, ok := oidcKeyDigest(&b.PublicKey)
	if !ok || x == y {
		t.Fatal("different key not distinguished")
	}
	if _, ok := oidcKeyDigest(nil); ok {
		t.Fatal("nil key accepted")
	}
}

func TestOIDCKeyFingerprintCanonicalVector(t *testing.T) {
	n := new(big.Int).Lsh(big.NewInt(1), 2047)
	n.Add(n, big.NewInt(1))
	key := &rsa.PublicKey{N: n, E: 65537}
	got, ok := oidcKeyDigest(key)
	if !ok || hex.EncodeToString(got[:]) != "73743724c7e8b08560e25b6f7fe59bf3b3fc1a7d86439c686f84def0d67171e4" {
		t.Fatal("wrong canonical issuer key digest")
	}
}
