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
	"os"
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

// extractArchive uses one decoder and writes only into the held service-created
// stage. Any error leaves the recorded stage for the authority to reconcile.
func extractArchive(ctx context.Context, archive io.ReaderAt, size int64, stage *os.File) (archiveDigest, subjectDigest [32]byte, written int64, err error) {
	fail := func() ([32]byte, [32]byte, int64, error) { return [32]byte{}, [32]byte{}, 0, invalid }
	if ctx == nil || ctx.Err() != nil || archive == nil || stage == nil || size <= 0 || size > maxArtifact {
		return fail()
	}
	st, e := stage.Stat()
	if e != nil || !st.Mode().IsRegular() || st.Size() != 0 {
		return fail()
	}
	zr, e := zip.NewReader(archive, size)
	if e != nil || len(zr.File) != 1 {
		return fail()
	}
	entry := zr.File[0]
	if entry.Name != artifactEntry || !entry.Mode().IsRegular() || entry.Flags&1 != 0 || (entry.Method != zip.Store && entry.Method != zip.Deflate) || entry.UncompressedSize64 == 0 || entry.UncompressedSize64 > maxArtifact {
		return fail()
	}
	f, e := entry.Open()
	if e != nil {
		return fail()
	}
	subject := sha256.New()
	n, e := io.Copy(io.MultiWriter(io.NewOffsetWriter(stage, 0), subject), io.LimitReader(contextReader{ctx: ctx, reader: f}, maxArtifact+1))
	closeErr := f.Close()
	if e != nil || closeErr != nil || n <= 0 || n > maxArtifact || uint64(n) != entry.UncompressedSize64 || stage.Sync() != nil {
		return fail()
	}
	readback := sha256.New()
	m, e := io.Copy(readback, contextReader{ctx: ctx, reader: io.NewSectionReader(stage, 0, n)})
	if e != nil || m != n || !bytes.Equal(subject.Sum(nil), readback.Sum(nil)) {
		return fail()
	}
	final, e := stage.Stat()
	if e != nil || final.Size() != n {
		return fail()
	}
	h := sha256.New()
	m, e = io.Copy(h, contextReader{ctx: ctx, reader: io.NewSectionReader(archive, 0, size)})
	if e != nil || m != size {
		return fail()
	}
	copy(archiveDigest[:], h.Sum(nil))
	copy(subjectDigest[:], subject.Sum(nil))
	return archiveDigest, subjectDigest, n, nil
}

type contextReader struct {
	ctx    context.Context
	reader io.Reader
}

func (r contextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.reader.Read(p)
}
