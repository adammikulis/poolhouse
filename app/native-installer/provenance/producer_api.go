package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
)

const artifactEntry = "poolhouse-native-artifact"

var repositoryName = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

type apiJob struct {
	ID         uint64 `json:"id"`
	RunID      uint64 `json:"run_id"`
	Head       string `json:"head_sha"`
	CheckURL   string `json:"check_run_url"`
	Status     string `json:"status"`
	Conclusion string `json:"conclusion"`
	Name       string `json:"name"`
}
type apiJobs struct {
	Total uint64   `json:"total_count"`
	Jobs  []apiJob `json:"jobs"`
}
type apiArtifact struct {
	ID      uint64 `json:"id"`
	Expired *bool  `json:"expired"`
	Digest  string `json:"digest"`
	Size    uint64 `json:"size_in_bytes"`
	Run     struct {
		ID       uint64 `json:"id"`
		Repo     uint64 `json:"repository_id"`
		HeadRepo uint64 `json:"head_repository_id"`
		Head     string `json:"head_sha"`
	} `json:"workflow_run"`
}
type producerAPI struct{ client *http.Client }

func newProducerAPI() *producerAPI {
	transport := &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext, TLSHandshakeTimeout: 5 * time.Second, ResponseHeaderTimeout: 5 * time.Second, MaxIdleConns: 1}
	return &producerAPI{client: &http.Client{Transport: transport, Timeout: 20 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}
func (a *producerAPI) get(ctx context.Context, path string, out any) error {
	u := url.URL{Scheme: "https", Host: "api.github.com", Path: path}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return invalid
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	req.Header.Set("User-Agent", "Poolhouse-Native-Authority")
	resp, err := a.client.Do(req)
	if err != nil {
		return invalid
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return invalid
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, (1<<20)+1))
	if err != nil || len(data) > 1<<20 || !uniqueJSON(data) {
		return invalid
	}
	if err = json.Unmarshal(data, out); err != nil {
		return invalid
	}
	return nil
}
func (a *producerAPI) jobs(ctx context.Context, p producerPolicy) ([]apiJob, error) {
	parts := strings.Split(p.repository, "/")
	if !repositoryName.MatchString(p.repository) || len(parts) != 2 || parts[0] == "." || parts[0] == ".." || parts[1] == "." || parts[1] == ".." || p.runID == 0 || p.attempt == 0 {
		return nil, invalid
	}
	prefix := "/repos/" + p.repository + "/actions/runs/" + uintString(p.runID) + "/attempts/" + uintString(p.attempt) + "/jobs"
	// GitHub's default page is 30; use its full attempt list with explicitly bounded pages.
	// Construct the query separately so a repository name cannot control it.
	var all []apiJob
	var total uint64
	for page := uint64(1); page <= 16; page++ {
		u := url.URL{Scheme: "https", Host: "api.github.com", Path: prefix, RawQuery: fmt.Sprintf("per_page=100&page=%d", page)}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
		if err != nil {
			return nil, invalid
		}
		req.Header.Set("Accept", "application/vnd.github+json")
		req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
		req.Header.Set("User-Agent", "Poolhouse-Native-Authority")
		resp, err := a.client.Do(req)
		if err != nil {
			return nil, invalid
		}
		data, readErr := io.ReadAll(io.LimitReader(resp.Body, (1<<20)+1))
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK || readErr != nil || len(data) > 1<<20 || !uniqueJSON(data) {
			return nil, invalid
		}
		var result apiJobs
		if json.Unmarshal(data, &result) != nil || result.Total > 1600 || len(result.Jobs) > 100 {
			return nil, invalid
		}
		if page == 1 {
			total = result.Total
		} else if total != result.Total {
			return nil, invalid
		}
		all = append(all, result.Jobs...)
		if uint64(len(all)) > total {
			return nil, invalid
		}
		if uint64(len(all)) == total {
			break
		}
		if len(result.Jobs) != 100 {
			return nil, invalid
		}
	}
	if uint64(len(all)) != total {
		return nil, invalid
	}
	sort.Slice(all, func(i, j int) bool { return all[i].ID < all[j].ID })
	for i, j := range all {
		if j.ID == 0 || (i > 0 && j.ID == all[i-1].ID) {
			return nil, invalid
		}
	}
	return all, nil
}

// API results attest GitHub's run and archive record, not which job uploaded it.
// The separately verified OIDC audience is the producer job's assertion of the subject.
func (a *producerAPI) verify(ctx context.Context, candidate *producerCandidate, artifactID uint64, archiveDigest [32]byte, archiveSize uint64, jobName string) error {
	if a == nil || a.client == nil || candidate == nil || artifactID == 0 || archiveSize == 0 || jobName == "" || len(jobName) > 256 {
		return invalid
	}
	p := candidate.policy
	first, err := a.jobs(ctx, p)
	if err != nil {
		return invalid
	}
	second, err := a.jobs(ctx, p)
	if err != nil || len(first) != len(second) {
		return invalid
	}
	matches := 0
	for i, j := range first {
		if j != second[i] {
			return invalid
		}
		if j.CheckURL == "https://api.github.com/repos/"+p.repository+"/check-runs/"+uintString(p.checkRunID) {
			matches++
			if j.RunID != p.runID || j.Head != hex.EncodeToString(p.source[:]) || j.CheckURL != "https://api.github.com/repos/"+p.repository+"/check-runs/"+uintString(p.checkRunID) || j.Status != "completed" || j.Conclusion != "success" || j.Name != jobName {
				return invalid
			}
		}
	}
	if matches != 1 {
		return invalid
	}
	var artifact apiArtifact
	if err = a.get(ctx, "/repos/"+p.repository+"/actions/artifacts/"+uintString(artifactID), &artifact); err != nil {
		return invalid
	}
	if artifact.ID != artifactID || artifact.Expired == nil || *artifact.Expired || artifact.Size != archiveSize || artifact.Digest != "sha256:"+hex.EncodeToString(archiveDigest[:]) || artifact.Run.ID != p.runID || artifact.Run.Repo != p.repositoryID || artifact.Run.HeadRepo != p.repositoryID || artifact.Run.Head != hex.EncodeToString(p.source[:]) {
		return invalid
	}
	return nil
}

func verifyArchive(archive io.ReaderAt, size int64, subject [32]byte) ([32]byte, error) {
	var empty [32]byte
	if archive == nil || size <= 0 || size > maxArtifact {
		return empty, invalid
	}
	zr, err := zip.NewReader(archive, size)
	if err != nil || len(zr.File) != 1 {
		return empty, invalid
	}
	entry := zr.File[0]
	if entry.Name != artifactEntry || !entry.Mode().IsRegular() || entry.UncompressedSize64 > maxArtifact {
		return empty, invalid
	}
	f, err := entry.Open()
	if err != nil {
		return empty, invalid
	}
	h := sha256.New()
	n, err := io.Copy(h, io.LimitReader(f, maxArtifact+1))
	closeErr := f.Close()
	if err != nil || closeErr != nil || n > maxArtifact || uint64(n) != entry.UncompressedSize64 || !bytes.Equal(h.Sum(nil), subject[:]) {
		return empty, invalid
	}
	ah := sha256.New()
	if _, err = io.Copy(ah, io.NewSectionReader(archive, 0, size)); err != nil {
		return empty, invalid
	}
	var digest [32]byte
	copy(digest[:], ah.Sum(nil))
	return digest, nil
}
