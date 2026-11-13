package main

import (
	"bytes"
	"encoding/json"
	"io"
	"unicode/utf8"
)

// Reject duplicate decoded keys before handing security metadata to libraries
// that might otherwise interpret the same bytes differently.
func uniqueJSON(data []byte) bool {
	if !bounded(data) || !validUnicode(data) {
		return false
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var value func(int) bool
	value = func(depth int) bool {
		if depth > 32 {
			return false
		}
		token, err := dec.Token()
		if err != nil {
			return false
		}
		d, compound := token.(json.Delim)
		if !compound {
			return true
		}
		switch d {
		case '{':
			keys := map[string]struct{}{}
			for dec.More() {
				key, err := dec.Token()
				if err != nil {
					return false
				}
				k, ok := key.(string)
				if !ok {
					return false
				}
				if _, exists := keys[k]; exists {
					return false
				}
				keys[k] = struct{}{}
				if !value(depth + 1) {
					return false
				}
			}
			end, err := dec.Token()
			return err == nil && end == json.Delim('}')
		case '[':
			for dec.More() {
				if !value(depth + 1) {
					return false
				}
			}
			end, err := dec.Token()
			return err == nil && end == json.Delim(']')
		default:
			return false
		}
	}
	if !value(0) {
		return false
	}
	_, err := dec.Token()
	return err == io.EOF
}

// encoding/json replaces malformed UTF-8 and lone surrogate escapes; security
// metadata must have one exact interpretation before signature verification.
func validUnicode(data []byte) bool {
	if !utf8.Valid(data) {
		return false
	}
	inString := false
	for i := 0; i < len(data); i++ {
		if !inString {
			if data[i] == '"' {
				inString = true
			}
			continue
		}
		if data[i] == '"' {
			inString = false
			continue
		}
		if data[i] != '\\' {
			continue
		}
		i++
		if i >= len(data) {
			return false
		}
		if data[i] != 'u' {
			continue
		}
		if i+4 >= len(data) {
			return false
		}
		v, ok := hex4(data[i+1 : i+5])
		if !ok {
			return false
		}
		i += 4
		if v >= 0xdc00 && v <= 0xdfff {
			return false
		}
		if v >= 0xd800 && v <= 0xdbff {
			if i+6 >= len(data) || data[i+1] != '\\' || data[i+2] != 'u' {
				return false
			}
			w, ok := hex4(data[i+3 : i+7])
			if !ok || w < 0xdc00 || w > 0xdfff {
				return false
			}
			i += 6
		}
	}
	return !inString
}
func hex4(b []byte) (uint16, bool) {
	var n uint16
	for _, c := range b {
		n <<= 4
		switch {
		case c >= '0' && c <= '9':
			n += uint16(c - '0')
		case c >= 'a' && c <= 'f':
			n += uint16(c - 'a' + 10)
		case c >= 'A' && c <= 'F':
			n += uint16(c - 'A' + 10)
		default:
			return 0, false
		}
	}
	return n, true
}
