#include "NativeIdentity.h"
#include <CommonCrypto/CommonDigest.h>
#include <CoreFoundation/CoreFoundation.h>
#include <string.h>

/* Limits are for OS-returned data; no caller-supplied bytes are promoted. */
#define MAX_IDENTITY_BYTES (1024u * 1024u)

bool ph_native_static_valid(SecStaticCodeRef code, SecRequirementRef requirement) {
    const SecCSFlags flags = kSecCSCheckAllArchitectures | kSecCSCheckNestedCode | kSecCSStrictValidate;
    return code && SecStaticCodeCheckValidity(code, flags, requirement) == errSecSuccess;
}

bool ph_native_identity_equal(const ph_native_identity_observation *left, const ph_native_identity_observation *right) {
    return left && right && left->signing_flags == right->signing_flags
        && left->entitlements_present == right->entitlements_present && left->publisher_present == right->publisher_present
        && !memcmp(left->code_directory,right->code_directory,sizeof(left->code_directory))
        && !memcmp(left->requirement_digest,right->requirement_digest,sizeof(left->requirement_digest))
        && !memcmp(left->entitlements_digest,right->entitlements_digest,sizeof(left->entitlements_digest))
        && !memcmp(left->publisher_digest,right->publisher_digest,sizeof(left->publisher_digest));
}

static bool digest(CFDataRef data, bool present, const char *domain, size_t domain_size, uint8_t output[32]) {
    if (present && (!data || CFGetTypeID(data) != CFDataGetTypeID())) return false;
    CFIndex length = present ? CFDataGetLength(data) : 0;
    if (length < 0 || (uint64_t)length > MAX_IDENTITY_BYTES) return false;
    uint8_t tag = present ? 1 : 0, size[8];
    uint64_t n = (uint64_t)length;
    for (unsigned i = 0; i < 8; i++) size[7 - i] = (uint8_t)(n >> (i * 8));
    CC_SHA256_CTX context;
    if (!CC_SHA256_Init(&context) || !CC_SHA256_Update(&context, domain, (CC_LONG)domain_size)
        || !CC_SHA256_Update(&context, &tag, sizeof(tag)) || !CC_SHA256_Update(&context, size, sizeof(size))
        || (length && !CC_SHA256_Update(&context, CFDataGetBytePtr(data), (CC_LONG)length))
        || !CC_SHA256_Final(output, &context)) return false;
    return true;
}

/* The Security SDK's public-key representation is pinned as bytes, not
 * described as SPKI. A certificate is an observation, not verified trust. */
static bool publisher(CFDictionaryRef information, ph_native_identity_observation *result) {
    CFTypeRef raw_flags = CFDictionaryGetValue(information, kSecCodeInfoFlags);
    int32_t flags = 0;
    if (!raw_flags || CFGetTypeID(raw_flags) != CFNumberGetTypeID()
        || !CFNumberGetValue((CFNumberRef)raw_flags, kCFNumberSInt32Type, &flags)) return false;
    result->signing_flags = (uint32_t)flags;
    CFTypeRef raw_chain = CFDictionaryGetValue(information, kSecCodeInfoCertificates);
    if (!raw_chain) return (flags & kSecCodeSignatureAdhoc) != 0;
    if ((flags & kSecCodeSignatureAdhoc) || CFGetTypeID(raw_chain) != CFArrayGetTypeID()
        || CFArrayGetCount((CFArrayRef)raw_chain) == 0) return false;
    CFTypeRef leaf = CFArrayGetValueAtIndex((CFArrayRef)raw_chain, 0);
    if (!leaf || CFGetTypeID(leaf) != SecCertificateGetTypeID()) return false;
    SecKeyRef key = SecCertificateCopyKey((SecCertificateRef)leaf);
    if (!key) return false;
    CFDictionaryRef attributes = SecKeyCopyAttributes(key);
    CFErrorRef error = NULL;
    CFDataRef bytes = SecKeyCopyExternalRepresentation(key, &error);
    bool okay = false;
    if (attributes && CFGetTypeID(attributes) == CFDictionaryGetTypeID() && bytes) {
        CFTypeRef type = CFDictionaryGetValue(attributes, kSecAttrKeyType);
        CFTypeRef size = CFDictionaryGetValue(attributes, kSecAttrKeySizeInBits);
        int bits = 0;
        if (type && size && CFGetTypeID(size) == CFNumberGetTypeID()
            && CFNumberGetValue((CFNumberRef)size, kCFNumberIntType, &bits)) {
            static const char rsa[] = "poolhouse/native-code/publisher-rsa/v1";
            static const char ec[] = "poolhouse/native-code/publisher-ec/v1";
            if (CFEqual(type, kSecAttrKeyTypeRSA) && bits >= 2048 && bits <= 8192)
                okay = digest(bytes, true, rsa, sizeof(rsa), result->publisher_digest);
            else if (CFEqual(type, kSecAttrKeyTypeECSECPrimeRandom) && (bits == 256 || bits == 384 || bits == 521))
                okay = digest(bytes, true, ec, sizeof(ec), result->publisher_digest);
        }
    }
    if (error) CFRelease(error);
    if (bytes) CFRelease(bytes);
    if (attributes) CFRelease(attributes);
    CFRelease(key);
    result->publisher_present = okay;
    return okay;
}

bool ph_native_identity_observe(SecStaticCodeRef code, ph_native_identity_observation *output) {
    if (!code || !output) return false;
    SecRequirementRef requirement = NULL;
    CFDataRef requirement_bytes = NULL;
    CFDictionaryRef information = NULL;
    ph_native_identity_observation result = {0};
    bool okay = SecCodeCopyDesignatedRequirement(code, kSecCSDefaultFlags, &requirement) == errSecSuccess
        && requirement && SecRequirementCopyData(requirement, kSecCSDefaultFlags, &requirement_bytes) == errSecSuccess
        && requirement_bytes && CFDataGetLength(requirement_bytes) > 0
        && SecCodeCopySigningInformation(code, kSecCSSigningInformation, &information) == errSecSuccess
        && information && CFGetTypeID(information) == CFDictionaryGetTypeID();
    if (okay) {
        CFTypeRef unique = CFDictionaryGetValue(information, kSecCodeInfoUnique);
        okay = unique && CFGetTypeID(unique) == CFDataGetTypeID() && CFDataGetLength((CFDataRef)unique) == 20;
        if (okay) memcpy(result.code_directory, CFDataGetBytePtr((CFDataRef)unique), sizeof(result.code_directory));
        CFTypeRef entitlements = CFDictionaryGetValue(information, kSecCodeInfoEntitlements);
        result.entitlements_present = entitlements != NULL;
        static const char req_domain[] = "poolhouse/native-code/requirement/v1";
        static const char ent_domain[] = "poolhouse/native-code/entitlements/v1";
        okay = okay && digest(requirement_bytes, true, req_domain, sizeof(req_domain), result.requirement_digest)
            && digest((CFDataRef)entitlements, result.entitlements_present, ent_domain, sizeof(ent_domain), result.entitlements_digest)
            && publisher(information, &result);
    }
    if (information) CFRelease(information);
    if (requirement_bytes) CFRelease(requirement_bytes);
    if (requirement) CFRelease(requirement);
    if (okay) *output = result;
    return okay;
}

bool ph_native_requirement_decode(CFDataRef bytes, const uint8_t expected_digest[32], SecRequirementRef *output) {
    if (!bytes || CFGetTypeID(bytes) != CFDataGetTypeID() || !expected_digest || !output || *output) return false;
    CFIndex length = CFDataGetLength(bytes);
    if (length <= 0 || (uint64_t)length > MAX_IDENTITY_BYTES) return false;
    uint8_t nonzero = 0;
    for (size_t index = 0; index < 32; index++) nonzero |= expected_digest[index];
    if (!nonzero) return false;
    static const char domain[] = "poolhouse/native-code/requirement/v1";
    uint8_t observed[32];
    if (!digest(bytes, true, domain, sizeof(domain), observed) || memcmp(observed, expected_digest, sizeof(observed))) return false;
    SecRequirementRef requirement = NULL;
    CFDataRef copied = NULL;
    bool okay = SecRequirementCreateWithData(bytes, kSecCSDefaultFlags, &requirement) == errSecSuccess && requirement
        && SecRequirementCopyData(requirement, kSecCSDefaultFlags, &copied) == errSecSuccess && copied
        && CFDataGetLength(copied) == length
        && !memcmp(CFDataGetBytePtr(copied), CFDataGetBytePtr(bytes), (size_t)length);
    if (copied) CFRelease(copied);
    if (!okay) { if (requirement) CFRelease(requirement); return false; }
    *output = requirement;
    return true;
}

#ifdef TESTING
bool ph_native_identity_test_digest(CFDataRef data, bool present, uint8_t output[32]) {
    static const char domain[] = "poolhouse/native-code/entitlements/v1";
    return output && digest(data, present, domain, sizeof(domain), output);
}
bool ph_native_identity_test_publisher(CFDictionaryRef information, ph_native_identity_observation *output) {
    return information && output && publisher(information, output);
}
#endif
