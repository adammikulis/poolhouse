package main

import (
	"encoding/base64"
	"encoding/json"
	"testing"
)

func TestAttestedJWKSParsing(t *testing.T) {
	key, _, _, _, _ := producerFixture(t)
	n := base64.RawURLEncoding.EncodeToString(key.N.Bytes())
	e := base64.RawURLEncoding.EncodeToString([]byte{1, 0, 1})
	makeDoc := func(entries ...map[string]any) []byte {
		b, err := json.Marshal(map[string]any{"keys": entries})
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	valid := map[string]any{"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "test-key", "n": n, "e": e}
	keys, err := parseJWKS(makeDoc(valid))
	if err != nil || keys["test-key"] == nil || keys["test-key"].N.Cmp(key.N) != 0 {
		t.Fatalf("valid key rejected: %v", err)
	}
	if _, err = parseJWKS(makeDoc(valid, valid)); err == nil {
		t.Fatal("duplicate key ID accepted")
	}
	bad := map[string]any{"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "weak", "n": "AQ", "e": e}
	if _, err = parseJWKS(makeDoc(bad)); err == nil {
		t.Fatal("weak key accepted")
	}
	if _, err = parseJWKS([]byte(`{"keys":[],"keys":[]}`)); err == nil {
		t.Fatal("duplicate field accepted")
	}
}

func TestJWKSRejectsPrivateOrWrongOperation(t *testing.T) {
	key, _, _, _, _ := producerFixture(t)
	n := base64.RawURLEncoding.EncodeToString(key.N.Bytes())
	for _, extra := range []string{`"d":"AQ"`, `"key_ops":["sign"]`} {
		b := []byte(`{"keys":[{"kty":"RSA","alg":"RS256","use":"sig","kid":"k","n":"` + n + `","e":"AQAB",` + extra + `}]}`)
		if _, err := parseJWKS(b); err == nil {
			t.Fatal("private or nonverify key accepted")
		}
	}
}
