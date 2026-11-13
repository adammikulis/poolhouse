package main

import (
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"math/big"
)

type jwksDocument struct {
	Keys []struct {
		Type       string   `json:"kty"`
		Algorithm  string   `json:"alg"`
		Use        string   `json:"use"`
		ID         string   `json:"kid"`
		Modulus    string   `json:"n"`
		Exponent   string   `json:"e"`
		Operations []string `json:"key_ops"`
	} `json:"keys"`
}

// parseJWKS parses a key set only after its exact bytes were included in the
// attestation. The independently pinned clean attester must have fetched them
// from the fixed GitHub issuer; this parser cannot establish that provenance.
func parseJWKS(data []byte) (map[string]*rsa.PublicKey, error) {
	if len(data) == 0 || len(data) > 65536 || !uniqueJSON(data) {
		return nil, invalid
	}
	var raw struct {
		Keys []map[string]json.RawMessage `json:"keys"`
	}
	if json.Unmarshal(data, &raw) != nil {
		return nil, invalid
	}
	for _, key := range raw.Keys {
		for _, field := range []string{"d", "p", "q", "dp", "dq", "qi", "oth", "k"} {
			if _, exists := key[field]; exists {
				return nil, invalid
			}
		}
	}
	var doc jwksDocument
	if json.Unmarshal(data, &doc) != nil || len(doc.Keys) == 0 || len(doc.Keys) > 32 {
		return nil, invalid
	}
	keys := make(map[string]*rsa.PublicKey, len(doc.Keys))
	seen := map[string]struct{}{}
	for _, key := range doc.Keys {
		if key.ID == "" || len(key.ID) > 128 {
			return nil, invalid
		}
		if _, exists := seen[key.ID]; exists {
			return nil, invalid
		}
		seen[key.ID] = struct{}{}
		if key.Type != "RSA" || key.Algorithm != "RS256" || key.Use != "sig" {
			continue
		}
		if len(key.Operations) > 0 {
			if len(key.Operations) != 1 || key.Operations[0] != "verify" {
				return nil, invalid
			}
		}
		n, err := base64.RawURLEncoding.Strict().DecodeString(key.Modulus)
		if err != nil || len(n) < 256 || len(n) > 512 || n[0] == 0 {
			return nil, invalid
		}
		e, err := base64.RawURLEncoding.Strict().DecodeString(key.Exponent)
		if err != nil || len(e) == 0 || len(e) > 4 || e[0] == 0 {
			return nil, invalid
		}
		exponent := uint64(0)
		for _, b := range e {
			exponent = exponent<<8 | uint64(b)
		}
		if exponent < 3 || exponent > 2147483647 || exponent%2 == 0 {
			return nil, invalid
		}
		modulus := new(big.Int).SetBytes(n)
		if modulus.BitLen() < 2048 || modulus.BitLen() > 4096 {
			return nil, invalid
		}
		keys[key.ID] = &rsa.PublicKey{N: modulus, E: int(exponent)}
	}
	if len(keys) == 0 {
		return nil, invalid
	}
	return keys, nil
}
