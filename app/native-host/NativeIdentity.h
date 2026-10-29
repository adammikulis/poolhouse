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
#ifdef TESTING
bool ph_native_identity_test_digest(CFDataRef data, bool present, uint8_t output[32]);
bool ph_native_identity_test_publisher(CFDictionaryRef information, ph_native_identity_observation *output);
#endif
#endif
