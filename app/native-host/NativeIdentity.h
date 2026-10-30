#ifndef PH_NATIVE_IDENTITY_H
#define PH_NATIVE_IDENTITY_H
#include <Security/Security.h>
#include <stdbool.h>
#include <stdint.h>

/* OS-returned signing observations only. This does not validate code, publisher,
 * process, installation, entitlement policy, or authorize an effect. */
typedef struct {
    uint8_t code_directory[20];
    uint8_t requirement_digest[32];
    uint8_t entitlements_digest[32];
    uint8_t publisher_digest[32];
    uint32_t signing_flags;
    bool entitlements_present;
    bool publisher_present;
} ph_native_identity_observation;
bool ph_native_identity_observe(SecStaticCodeRef code, ph_native_identity_observation *output);
/* Exact observation comparison; neither value establishes trust. */
bool ph_native_identity_equal(const ph_native_identity_observation *left, const ph_native_identity_observation *right);
/* Strict all-architecture and nested-resource validation. An optional
 * requirement only has authority when independently authenticated. */
bool ph_native_static_valid(SecStaticCodeRef code, SecRequirementRef requirement);
/* Decode and check bytes against a separately supplied digest. Neither input
 * authenticates an installation or gives authority. Output must start NULL. */
bool ph_native_requirement_decode(CFDataRef bytes, const uint8_t expected_digest[32], SecRequirementRef *output);
#ifdef TESTING
bool ph_native_identity_test_digest(CFDataRef data, bool present, uint8_t output[32]);
bool ph_native_identity_test_publisher(CFDictionaryRef information, ph_native_identity_observation *output);
#endif
#endif
