package main

import (
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"strconv"
	"strings"
	"time"

	jwt "github.com/golang-jwt/jwt/v5"
)

// Private preparation only. Keys, time, policy and installation must be issued by
// protected authority state; this type is not an authenticated installation witness.
type producerPolicy struct {
	installation, nonce                      [32]byte
	source, workflow                         [20]byte
	repositoryID, runID, attempt, checkRunID uint64
	repository, ref, event, jobWorkflowRef   string
}
type producerCandidate struct {
	policy   producerPolicy
	subject  [32]byte
	observed time.Time
}
type oidcClaims struct {
	jwt.RegisteredClaims
	Repository     string `json:"repository"`
	RepositoryID   string `json:"repository_id"`
	Ref            string `json:"ref"`
	Event          string `json:"event_name"`
	SHA            string `json:"sha"`
	JobWorkflowRef string `json:"job_workflow_ref"`
	JobWorkflowSHA string `json:"job_workflow_sha"`
	RunID          string `json:"run_id"`
	Attempt        string `json:"run_attempt"`
	CheckRunID     string `json:"check_run_id"`
	Runner         string `json:"runner_environment"`
}

func producerAudience(p producerPolicy, subject [32]byte) string {
	h := sha256.New()
	h.Write([]byte("poolhouse/native-producer-audience/v2\x00"))
	h.Write(subject[:])
	h.Write(p.source[:])
	h.Write(p.workflow[:])
	var b [8]byte
	for _, n := range []uint64{p.repositoryID, p.runID, p.attempt, p.checkRunID} {
		binary.BigEndian.PutUint64(b[:], n)
		h.Write(b[:])
	}
	return "poolhouse-native-producer-v2:" + hex.EncodeToString(h.Sum(nil))
}

// A verified transparency timestamp must fall within the producer token validity
// interval. The token can then support reusable provenance after it expires.
// This does not prove an artifact API/job association, consume a nonce,
// authenticate the key snapshot, or authorize activation.
func verifyProducer(raw string, keys map[string]*rsa.PublicKey, p producerPolicy, subject *verified, now time.Time) (*producerCandidate, error) {
	if len(raw) == 0 || len(raw) > 16384 || subject == nil || now.IsZero() || !nonzero(p.installation[:]) || !nonzero(p.nonce[:]) || !nonzero(p.source[:]) || !nonzero(p.workflow[:]) || p.repositoryID == 0 || p.runID == 0 || p.attempt == 0 || p.checkRunID == 0 || p.repository == "" || p.ref == "" || p.event == "" || p.jobWorkflowRef == "" {
		return nil, invalid
	}
	bound := subject.policy
	if bound.repoURI != "https://github.com/"+p.repository || bound.repoID != uintString(p.repositoryID) || bound.sourceDigest != hex.EncodeToString(p.source[:]) || bound.sourceRef != p.ref || bound.trigger != p.event || bound.signerURI != "https://github.com/"+p.jobWorkflowRef || bound.signerDigest != hex.EncodeToString(p.workflow[:]) || bound.runURI != "https://github.com/"+p.repository+"/actions/runs/"+uintString(p.runID)+"/attempts/"+uintString(p.attempt) || subject.runURI != bound.runURI {
		return nil, invalid
	}
	parts := strings.Split(raw, ".")
	if len(parts) != 3 {
		return nil, invalid
	}
	for _, part := range parts[:2] {
		decoded, err := base64.RawURLEncoding.Strict().DecodeString(part)
		if err != nil || !uniqueJSON(decoded) {
			return nil, invalid
		}
	}
	for _, observed := range subject.logTimes {
		if observed.IsZero() || observed.After(now) {
			continue
		}
		claims := new(oidcClaims)
		parsed, err := jwt.ParseWithClaims(raw, claims, func(token *jwt.Token) (any, error) {
			if len(token.Header) < 3 || len(token.Header) > 4 || token.Header["alg"] != "RS256" || token.Header["typ"] != "JWT" {
				return nil, invalid
			}
			for key, value := range token.Header {
				if key != "alg" && key != "typ" && key != "kid" && key != "x5t" {
					return nil, invalid
				}
				if key == "x5t" {
					thumb, ok := value.(string)
					if !ok || len(thumb) == 0 || len(thumb) > 128 {
						return nil, invalid
					}
				}
			}
			kid, ok := token.Header["kid"].(string)
			if !ok || len(kid) == 0 || len(kid) > 128 {
				return nil, invalid
			}
			key := keys[kid]
			if key == nil || key.N == nil || key.N.BitLen() < 2048 || key.N.BitLen() > 4096 || key.E < 3 || key.E%2 == 0 {
				return nil, invalid
			}
			return key, nil
		}, jwt.WithValidMethods([]string{"RS256"}), jwt.WithIssuer("https://token.actions.githubusercontent.com"), jwt.WithExpirationRequired(), jwt.WithIssuedAt(), jwt.WithTimeFunc(func() time.Time { return observed }))
		if err != nil || parsed == nil || !parsed.Valid || claims.IssuedAt == nil || claims.NotBefore == nil || claims.ExpiresAt == nil || len(claims.Audience) != 1 || claims.Audience[0] != producerAudience(p, subject.artifactSHA256) ||
			claims.ExpiresAt.Time.Sub(claims.IssuedAt.Time) <= 0 || claims.ExpiresAt.Time.Sub(claims.IssuedAt.Time) > 15*time.Minute ||
			claims.Repository != p.repository || claims.RepositoryID != uintString(p.repositoryID) || claims.Ref != p.ref || claims.Event != p.event || claims.SHA != hex.EncodeToString(p.source[:]) ||
			claims.JobWorkflowRef != p.jobWorkflowRef || claims.JobWorkflowSHA != hex.EncodeToString(p.workflow[:]) || claims.RunID != uintString(p.runID) || claims.Attempt != uintString(p.attempt) || claims.CheckRunID != uintString(p.checkRunID) || claims.Runner != "github-hosted" {
			continue
		}
		if now.Before(claims.ExpiresAt.Time) {
			continue
		}
		return &producerCandidate{policy: p, subject: subject.artifactSHA256, observed: observed}, nil
	}
	return nil, invalid
}

func uintString(v uint64) string { return strconv.FormatUint(v, 10) }
