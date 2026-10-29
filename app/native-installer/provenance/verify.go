// Package provenance verifies a Sigstore attestation; it does not authorize installation.
// Its root and policy must come from an independently authenticated protected installer.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"time"

	intoto "github.com/in-toto/attestation/go/v1"

	protobundle "github.com/sigstore/protobuf-specs/gen/pb-go/bundle/v1"
	"github.com/sigstore/sigstore-go/pkg/bundle"
	"github.com/sigstore/sigstore-go/pkg/root"
	"github.com/sigstore/sigstore-go/pkg/verify"
	"google.golang.org/protobuf/encoding/protojson"
)

const maxBundle = 4 << 20
const maxArtifact = 256 << 20

var invalid = errors.New("invalid provenance")

// Policy is intentionally private: neither untrusted IPC nor JSON creates it.
// A future protected installer must construct it from independently authenticated state.
type policy struct {
	san, repoURI, repoID, sourceDigest, sourceRef    string
	signerURI, signerDigest, configURI, configDigest string
	trigger, runURI                                  string
}

// verified is issued only after library verification and policy checks. It is not
// an installation approval, an API-producer proof, or a durable commit witness.
type verified struct {
	artifactSHA256 [32]byte
	runURI         string
	policy         policy
	logTimes       []time.Time
}

func verifyBundle(b []byte, artifact io.Reader, token []byte, material root.TrustedMaterial, p policy) (*verified, error) {
	if len(b) == 0 || len(b) > maxBundle || len(token) == 0 || len(token) > 16384 || artifact == nil || material == nil ||
		p.san == "" || p.repoURI == "" || p.repoID == "" || p.sourceDigest == "" || p.sourceRef == "" ||
		p.signerURI == "" || p.signerDigest == "" || p.configURI == "" || p.configDigest == "" ||
		p.trigger == "" || p.runURI == "" {
		return nil, invalid
	}
	h := sha256.New()
	n, err := io.Copy(h, io.LimitReader(artifact, maxArtifact+1))
	if err != nil || n > maxArtifact {
		return nil, invalid
	}
	var digest [32]byte
	copy(digest[:], h.Sum(nil))
	pb := new(protobundle.Bundle)
	if err := (protojson.UnmarshalOptions{DiscardUnknown: false}).Unmarshal(b, pb); err != nil {
		return nil, invalid
	}
	entity, err := bundle.NewBundle(pb)
	if err != nil {
		return nil, invalid
	}
	verifier, err := verify.NewVerifier(material, verify.WithSignedCertificateTimestamps(1), verify.WithTransparencyLog(1), verify.WithObserverTimestamps(1))
	if err != nil {
		return nil, invalid
	}
	identity, err := verify.NewShortCertificateIdentity("https://token.actions.githubusercontent.com", "", p.san, "")
	if err != nil {
		return nil, invalid
	}
	tokenDigest := sha256.Sum256(token)
	result, err := verifier.Verify(entity, verify.NewPolicy(verify.WithArtifactDigests([]verify.ArtifactDigest{{Algorithm: "sha256", Digest: digest[:]}, {Algorithm: "sha256", Digest: tokenDigest[:]}}), verify.WithCertificateIdentity(identity)))
	if err != nil || result == nil || result.Statement == nil || result.Signature == nil || result.Signature.Certificate == nil {
		return nil, invalid
	}
	statement := result.Statement
	// Exactly the artifact and producer token are bound by the signed statement.
	if !matchesStatement(statement, digest, tokenDigest) {
		return nil, invalid
	}
	c := result.Signature.Certificate
	if c.SourceRepositoryURI != p.repoURI || c.SourceRepositoryIdentifier != p.repoID || c.SourceRepositoryDigest != p.sourceDigest ||
		c.SourceRepositoryRef != p.sourceRef || c.BuildSignerURI != p.signerURI || c.BuildSignerDigest != p.signerDigest ||
		c.BuildConfigURI != p.configURI || c.BuildConfigDigest != p.configDigest || c.BuildTrigger != p.trigger ||
		c.RunnerEnvironment != "github-hosted" || c.RunInvocationURI != p.runURI {
		return nil, invalid
	}
	var logs []time.Time
	for _, observed := range result.VerifiedTimestamps {
		if observed.Type == "Tlog" && !observed.Timestamp.IsZero() {
			logs = append(logs, observed.Timestamp.UTC())
		}
	}
	if len(logs) == 0 || len(logs) > 8 {
		return nil, invalid
	}
	return &verified{artifactSHA256: digest, runURI: c.RunInvocationURI, policy: p, logTimes: logs}, nil
}

func matchesStatement(statement *intoto.Statement, artifact, token [32]byte) bool {
	if statement == nil || statement.GetType() != "https://in-toto.io/Statement/v1" || statement.PredicateType != "https://slsa.dev/provenance/v1" || len(statement.Subject) != 2 {
		return false
	}
	expected := map[string]string{artifactEntry: hex.EncodeToString(artifact[:]), "poolhouse-producer.jwt": hex.EncodeToString(token[:])}
	for _, s := range statement.Subject {
		if s == nil || len(s.Digest) != 1 || expected[s.Name] == "" || s.Digest["sha256"] != expected[s.Name] {
			return false
		}
		delete(expected, s.Name)
	}
	return len(expected) == 0
}
