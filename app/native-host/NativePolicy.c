#include "NativePolicy.h"
#include "PolicyRequirements.h"
#include <stdlib.h>
#include <string.h>

struct ph_native_policy_candidate {
    ph_manifest_values manifest;
    SecRequirementRef requirements[3];
    ph_policy_files *files;
};

static bool capture(ph_policy_files *files, const uint8_t *bytes, size_t length, ph_native_policy_candidate **output) {
    if (!files || !bytes || !output || *output) { ph_policy_files_destroy(&files); return false; }
    ph_manifest_values manifest = {0};
    uint8_t digests[3][32];
    if (!ph_native_manifest_decode(bytes, length, &manifest)) { ph_policy_files_destroy(&files); return false; }
    memcpy(digests[0], manifest.helper.requirement_digest, 32);
    memcpy(digests[1], manifest.gui.requirement_digest, 32);
    memcpy(digests[2], manifest.node.requirement_digest, 32);
    SecRequirementRef requirements[3] = {NULL, NULL, NULL};
    bool okay = ph_policy_requirements_load(files, manifest.generation, digests, requirements)
        && ph_policy_files_validate(files);
    ph_native_policy_candidate *candidate = okay ? calloc(1, sizeof(*candidate)) : NULL;
    if (!candidate) {
        for (unsigned index = 0; index < 3; index++) if (requirements[index]) CFRelease(requirements[index]);
        ph_policy_files_destroy(&files);
        return false;
    }
    candidate->manifest = manifest; candidate->files = files;
    for (unsigned index = 0; index < 3; index++) candidate->requirements[index] = requirements[index];
    *output = candidate;
    return true;
}

bool ph_native_policy_capture(ph_native_policy_candidate **output) {
    if (!output || *output) return false;
    uint8_t bytes[16384]; size_t length = 0; ph_policy_files *files = NULL;
    return ph_policy_files_begin(&files, bytes, sizeof(bytes), &length) && capture(files, bytes, length, output);
}
bool ph_native_policy_validate(const ph_native_policy_candidate *candidate) {
    return candidate && ph_policy_files_validate(candidate->files);
}
const ph_manifest_values *ph_native_policy_manifest(const ph_native_policy_candidate *candidate) {
    return candidate ? &candidate->manifest : NULL;
}
SecRequirementRef ph_native_policy_requirement(const ph_native_policy_candidate *candidate, unsigned role) {
    return candidate && role < 3 ? candidate->requirements[role] : NULL;
}
void ph_native_policy_destroy(ph_native_policy_candidate **candidate) {
    if (!candidate || !*candidate) return;
    ph_native_policy_candidate *owned = *candidate; *candidate = NULL;
    for (unsigned index = 0; index < 3; index++) if (owned->requirements[index]) CFRelease(owned->requirements[index]);
    ph_policy_files_destroy(&owned->files);
    free(owned);
}
#ifdef TESTING
bool ph_native_policy_fixture(int root, uint32_t owner, ph_native_policy_candidate **output) {
    if (!output || *output) return false;
    uint8_t bytes[16384]; size_t length = 0; ph_policy_files *files = NULL;
    return ph_policy_files_fixture_begin(root, owner, &files, bytes, sizeof(bytes), &length)
        && capture(files, bytes, length, output);
}
#endif
