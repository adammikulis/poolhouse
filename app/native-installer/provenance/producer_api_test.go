package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
)

type apiTransport func(*http.Request) (*http.Response, error)

func (f apiTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func archiveFor(t *testing.T, name string, content []byte) []byte {
	t.Helper()
	var b bytes.Buffer
	w := zip.NewWriter(&b)
	f, e := w.Create(name)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = f.Write(content); e != nil {
		t.Fatal(e)
	}
	if e = w.Close(); e != nil {
		t.Fatal(e)
	}
	return b.Bytes()
}
func TestArchiveBindsExactSubject(t *testing.T) {
	body := []byte("native fixture")
	subject := sha256.Sum256(body)
	a := archiveFor(t, artifactEntry, body)
	got, err := verifyArchive(bytes.NewReader(a), int64(len(a)), subject)
	if err != nil || got != sha256.Sum256(a) {
		t.Fatalf("valid archive rejected: %v", err)
	}
	wrong := subject
	wrong[0] ^= 1
	if _, err = verifyArchive(bytes.NewReader(a), int64(len(a)), wrong); err == nil {
		t.Fatal("wrong extracted subject accepted")
	}
	a = archiveFor(t, "../poolhouse-native-artifact", body)
	if _, err = verifyArchive(bytes.NewReader(a), int64(len(a)), subject); err == nil {
		t.Fatal("traversal accepted")
	}
}
func TestProducerAPIBindsJobRunAndArchive(t *testing.T) {
	_, p, v, _, _ := producerFixture(t)
	candidate := &producerCandidate{policy: p, subject: v.artifactSHA256}
	digest := sha256.Sum256([]byte("archive"))
	job := apiJob{ID: 999, RunID: p.runID, Head: hex.EncodeToString(p.source[:]), CheckURL: "https://api.github.com/repos/" + p.repository + "/check-runs/" + uintString(p.checkRunID), Status: "completed", Conclusion: "success", Name: "native build"}
	notExpired := false
	artifact := apiArtifact{ID: 42, Expired: &notExpired, Digest: "sha256:" + hex.EncodeToString(digest[:]), Size: 7}
	artifact.Run.ID = p.runID
	artifact.Run.Repo = p.repositoryID
	artifact.Run.HeadRepo = p.repositoryID
	artifact.Run.Head = hex.EncodeToString(p.source[:])
	api := &producerAPI{client: &http.Client{Transport: apiTransport(func(req *http.Request) (*http.Response, error) {
		if req.URL.Host != "api.github.com" || req.URL.Scheme != "https" {
			t.Fatal("incorrect API origin")
		}
		var body []byte
		if strings.Contains(req.URL.Path, "/jobs") {
			body, _ = json.Marshal(apiJobs{Total: 1, Jobs: []apiJob{job}})
		} else {
			body, _ = json.Marshal(artifact)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(body)), Header: http.Header{}}, nil
	})}}
	if err := api.verify(context.Background(), candidate, 42, digest, 7, "native build"); err != nil {
		t.Fatalf("valid API evidence rejected: %v", err)
	}
	artifact.Run.Repo++
	if err := api.verify(context.Background(), candidate, 42, digest, 7, "native build"); err == nil {
		t.Fatal("wrong repository accepted")
	}
	artifact.Run.Repo--
	job.Conclusion = "failure"
	if err := api.verify(context.Background(), candidate, 42, digest, 7, "native build"); err == nil {
		t.Fatal("failed job accepted")
	}
	job.Conclusion = "success"
	p.repository = "../other"
	candidate.policy = p
	if err := api.verify(context.Background(), candidate, 42, digest, 7, "native build"); err == nil {
		t.Fatal("invalid repository path accepted")
	}
}

func TestArchiveRejectsDuplicateAndSymlink(t *testing.T) {
	body := []byte("native fixture")
	subject := sha256.Sum256(body)
	var b bytes.Buffer
	w := zip.NewWriter(&b)
	for i := 0; i < 2; i++ {
		f, err := w.Create(artifactEntry)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = f.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyArchive(bytes.NewReader(b.Bytes()), int64(b.Len()), subject); err == nil {
		t.Fatal("duplicate entries accepted")
	}
	b.Reset()
	w = zip.NewWriter(&b)
	h := &zip.FileHeader{Name: artifactEntry}
	h.SetMode(os.ModeSymlink | 0777)
	f, err := w.CreateHeader(h)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.Write(body); err != nil {
		t.Fatal(err)
	}
	if err = w.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err = verifyArchive(bytes.NewReader(b.Bytes()), int64(b.Len()), subject); err == nil {
		t.Fatal("symlink accepted")
	}
}

func TestProducerAPIRejectsChangingJobsAndMissingExpiry(t *testing.T) {
	_, p, v, _, _ := producerFixture(t)
	candidate := &producerCandidate{policy: p, subject: v.artifactSHA256}
	digest := sha256.Sum256([]byte("archive"))
	job := apiJob{ID: 999, RunID: p.runID, Head: hex.EncodeToString(p.source[:]), CheckURL: "https://api.github.com/repos/" + p.repository + "/check-runs/" + uintString(p.checkRunID), Status: "completed", Conclusion: "success", Name: "native build"}
	calls := 0
	artifact := apiArtifact{ID: 42, Digest: "sha256:" + hex.EncodeToString(digest[:]), Size: 7}
	artifact.Run.ID = p.runID
	artifact.Run.Repo = p.repositoryID
	artifact.Run.HeadRepo = p.repositoryID
	artifact.Run.Head = hex.EncodeToString(p.source[:])
	api := &producerAPI{client: &http.Client{Transport: apiTransport(func(req *http.Request) (*http.Response, error) {
		var body []byte
		if strings.Contains(req.URL.Path, "/jobs") {
			calls++
			if calls == 2 {
				job.Name = "changed"
			}
			body, _ = json.Marshal(apiJobs{Total: 1, Jobs: []apiJob{job}})
		} else {
			body, _ = json.Marshal(artifact)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(body)), Header: http.Header{}}, nil
	})}}
	if err := api.verify(context.Background(), candidate, 42, digest, 7, "native build"); err == nil {
		t.Fatal("changing job accepted")
	}
	calls = 0
	job.Name = "native build"
	api.client.Transport = apiTransport(func(req *http.Request) (*http.Response, error) {
		var body []byte
		if strings.Contains(req.URL.Path, "/jobs") {
			body, _ = json.Marshal(apiJobs{Total: 1, Jobs: []apiJob{job}})
		} else {
			body, _ = json.Marshal(artifact)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(body)), Header: http.Header{}}, nil
	})
	if err := api.verify(context.Background(), candidate, 42, digest, 7, "native build"); err == nil {
		t.Fatal("missing artifact expiry flag accepted")
	}
}
