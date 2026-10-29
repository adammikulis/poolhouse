package main

import (
	"bytes"
	"encoding/hex"
	intoto "github.com/in-toto/attestation/go/v1"
	protobundle "github.com/sigstore/protobuf-specs/gen/pb-go/bundle/v1"
	"google.golang.org/protobuf/encoding/protojson"
	"testing"

	"github.com/sigstore/sigstore-go/pkg/testing/data"
)

func candidatePolicy() policy {
	return policy{san: "https://github.com/example/project/.github/workflows/build.yml@refs/heads/development", repoURI: "https://github.com/example/project", repoID: "123", sourceDigest: "abc", sourceRef: "refs/heads/development", signerURI: "https://github.com/example/project/.github/workflows/build.yml@refs/heads/development", signerDigest: "abc", configURI: "https://github.com/example/project/.github/workflows/build.yml@refs/heads/development", configDigest: "abc", trigger: "push", runURI: "https://github.com/example/project/actions/runs/1/attempts/1"}
}

func TestRejectUntrustedAndMismatchedEvidence(t *testing.T) {
	tr := data.TrustedRoot(t, "public-good.json")
	entity := data.Bundle(t, "dsse.sigstore.json")
	signed, err := entity.MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name   string
		b      []byte
		body   []byte
		policy policy
	}{
		{"unrelated legitimate bundle", signed, []byte("different bytes"), candidatePolicy()},
		{"corrupt bundle", append([]byte(nil), signed[:len(signed)/2]...), []byte("data"), candidatePolicy()},
		{"oversized bundle", make([]byte, maxBundle+1), []byte("data"), candidatePolicy()},
		{"missing policy", signed, []byte("data"), policy{}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			v, err := verifyBundle(tc.b, bytes.NewReader(tc.body), []byte("token"), []byte("jwks"), tr, tc.policy)
			if err == nil || v != nil {
				t.Fatal("untrusted evidence accepted")
			}
		})
	}
	if v, err := verifyBundle(signed, bytes.NewReader(nil), []byte("token"), []byte("jwks"), nil, candidatePolicy()); err == nil || v != nil {
		t.Fatal("caller-selected missing root accepted")
	}
}

func TestStatementBindsExactlyArtifactAndToken(t *testing.T) {
	var artifact, token, jwks [32]byte
	artifact[0] = 1
	token[0] = 2
	jwks[0] = 3
	s := &intoto.Statement{Type: "https://in-toto.io/Statement/v1", PredicateType: "https://slsa.dev/provenance/v1", Subject: []*intoto.ResourceDescriptor{{Name: artifactEntry, Digest: map[string]string{"sha256": hex.EncodeToString(artifact[:])}}, {Name: "poolhouse-producer.jwt", Digest: map[string]string{"sha256": hex.EncodeToString(token[:])}}, {Name: "poolhouse-oidc-jwks.json", Digest: map[string]string{"sha256": hex.EncodeToString(jwks[:])}}}}
	if !matchesStatement(s, artifact, token, jwks) {
		t.Fatal("valid statement rejected")
	}
	for _, kind := range []string{"", "https://in-toto.io/Statement/v0.1"} {
		s.Type = kind
		if matchesStatement(s, artifact, token, jwks) {
			t.Fatal("wrong statement accepted")
		}
	}
	s.Type = "https://in-toto.io/Statement/v1"
	s.Subject[1].Name = artifactEntry
	if matchesStatement(s, artifact, token, jwks) {
		t.Fatal("duplicate subject accepted")
	}
	s.Subject[1] = nil
	if matchesStatement(s, artifact, token, jwks) {
		t.Fatal("nil subject accepted")
	}
}

func TestBundleDecoderRejectsDuplicateAndUnknownFields(t *testing.T) {
	for _, b := range []string{`{"mediaType":"one","mediaType":"two"}`, `{"unknown":true}`, `{"mediaType":"one","media_type":"two"}`} {
		var parsed protobundle.Bundle
		if err := (protojson.UnmarshalOptions{DiscardUnknown: false}).Unmarshal([]byte(b), &parsed); err == nil {
			t.Fatalf("ambiguous bundle accepted: %s", b)
		}
	}
}
