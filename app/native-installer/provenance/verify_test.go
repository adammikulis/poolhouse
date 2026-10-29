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
			v, err := verifyBundle(tc.b, bytes.NewReader(tc.body), tr, tc.policy)
			if err == nil || v != nil {
				t.Fatal("untrusted evidence accepted")
			}
		})
	}
	if v, err := verifyBundle(signed, bytes.NewReader(nil), nil, candidatePolicy()); err == nil || v != nil {
		t.Fatal("caller-selected missing root accepted")
	}
}

func TestStatementIsExactlyV1AndOneMatchingSubject(t *testing.T) {
	var digest [32]byte
	digest[0] = 1
	s := &intoto.Statement{Type: "https://in-toto.io/Statement/v1", PredicateType: "https://slsa.dev/provenance/v1", Subject: []*intoto.ResourceDescriptor{{Digest: map[string]string{"sha256": hex.EncodeToString(digest[:])}}}}
	if !matchesStatement(s, digest) {
		t.Fatal("valid statement rejected")
	}
	for _, kind := range []string{"", "https://in-toto.io/Statement/v0.1", "https://in-toto.io/Statement/v1 "} {
		s.Type = kind
		if matchesStatement(s, digest) {
			t.Fatal("wrong statement type accepted")
		}
	}
	s.Type = "https://in-toto.io/Statement/v1"
	s.Subject = append(s.Subject, s.Subject[0])
	if matchesStatement(s, digest) {
		t.Fatal("ambiguous subjects accepted")
	}
	s.Subject = []*intoto.ResourceDescriptor{nil}
	if matchesStatement(s, digest) {
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
