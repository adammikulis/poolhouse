#include "PolicyRequirements.h"
#include "NativeIdentity.h"

static bool decode(const ph_protected_requirements *raw, const uint8_t expected[3][32], SecRequirementRef output[3]) {
    if (!raw || !expected || !output || output[0] || output[1] || output[2]) return false;
    SecRequirementRef result[3] = {NULL, NULL, NULL};
    bool okay = true;
    for (size_t index = 0; okay && index < 3; index++) {
        if (!raw->length[index] || raw->length[index] > PH_REQUIREMENT_LIMIT) { okay = false; break; }
        CFDataRef data = CFDataCreate(NULL, raw->bytes[index], (CFIndex)raw->length[index]);
        okay = data && ph_native_requirement_decode(data, expected[index], &result[index]);
        if (data) CFRelease(data);
    }
    if (!okay) {
        for (size_t index = 0; index < 3; index++) if (result[index]) CFRelease(result[index]);
        return false;
    }
    for (size_t index = 0; index < 3; index++) output[index] = result[index];
    return true;
}

bool ph_policy_requirements_load(uint64_t generation, const uint8_t expected[3][32], SecRequirementRef output[3]) {
    if (!expected || !output || output[0] || output[1] || output[2]) return false;
    ph_protected_requirements raw = {0};
    return ph_requirements_read(generation, &raw) && decode(&raw, expected, output);
}
#ifdef TESTING
bool ph_policy_requirements_test_decode(const ph_protected_requirements *raw, const uint8_t expected[3][32],
    SecRequirementRef output[3]) {
    return decode(raw, expected, output);
}
#endif
