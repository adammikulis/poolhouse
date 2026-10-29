#ifndef POOLHOUSE_PROVENANCE_LAYOUT_H
#define POOLHOUSE_PROVENANCE_LAYOUT_H
#include <stdint.h>

/* The table is generated from the verifier's cgo header. The consumer compares
 * every size, alignment, count, offset and width before making a verifier call. */
#define PH_BYTES_FIELDS(X) X(data) X(size)
#define PH_VERSION_FIELDS(X) X(version) X(digest)
#define PH_INPUT_FIELDS(X) \
 X(version) X(bundle) X(oidc_token) X(prior_root) X(roots) X(timestamp) X(snapshot) X(targets) X(target) \
 X(root_count) X(jwks) X(san) X(repository_uri) X(repository_id) X(source_digest) X(source_ref) \
 X(signer_uri) X(signer_digest) X(config_uri) X(config_digest) X(trigger) X(run_uri) \
 X(repository) X(job_workflow_ref) X(job_name) X(installation) X(head) X(policy_digest) X(nonce) \
 X(source) X(workflow) X(root_version) X(timestamp_version) X(snapshot_version) X(targets_version) \
 X(generation) X(repository_number) X(run_number) X(attempt) X(check_run) X(artifact_id) \
 X(previous_time) X(trusted_time) X(stage_fd) X(archive_fd)
#define PH_RESULT_FIELDS(X) \
 X(version) X(artifact_sha256) X(archive_sha256) X(installation) X(head) X(policy_digest) X(nonce) \
 X(target_sha256) X(jwks_sha256) X(oidc_key_sha256) X(root_version) X(timestamp_version) X(snapshot_version) X(targets_version) \
 X(generation) X(device) X(inode) X(size) X(archive_device) X(archive_inode) X(archive_size) X(artifact_id) \
 X(trusted_time) X(producer_observed_time)
#define PH_COUNT_FIELD(name) + 1
enum { PH_BYTES_COUNT=0 PH_BYTES_FIELDS(PH_COUNT_FIELD), PH_VERSION_COUNT=0 PH_VERSION_FIELDS(PH_COUNT_FIELD),
       PH_INPUT_COUNT=0 PH_INPUT_FIELDS(PH_COUNT_FIELD), PH_RESULT_COUNT=0 PH_RESULT_FIELDS(PH_COUNT_FIELD) };
#undef PH_COUNT_FIELD

/* Scalar queries avoid requiring a Rust mirror of the layout table itself. */
enum { PH_LAYOUT_BYTES=1, PH_LAYOUT_VERSION=2, PH_LAYOUT_INPUT=3, PH_LAYOUT_RESULT=4 };
enum { PH_LAYOUT_SIZE=1, PH_LAYOUT_ALIGN=2, PH_LAYOUT_COUNT=3, PH_LAYOUT_OFFSET=4, PH_LAYOUT_WIDTH=5 };
/* UINT64_MAX denotes an invalid version, type, property or field index. */
uint64_t ph_provenance_layout_query(uint32_t version, uint32_t type, uint32_t property, uint32_t index);
#endif
