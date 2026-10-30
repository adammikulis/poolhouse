#include "NativeStaticImage.h"

bool ph_native_static_image_matches(SecStaticCodeRef code, SecRequirementRef requirement,
                                    const ph_native_identity_observation *expected) {
    if (!code || !requirement || !expected || !expected->publisher_present ||
        (expected->signing_flags & kSecCodeSignatureAdhoc) ||
        !(expected->signing_flags & kSecCodeSignatureRuntime)) return false;
    const SecCSFlags flags = kSecCSCheckAllArchitectures | kSecCSCheckNestedCode | kSecCSStrictValidate;
    if (SecStaticCodeCheckValidity(code, flags, requirement) != errSecSuccess) return false;
    ph_native_identity_observation actual = {0};
    return ph_native_identity_observe(code, &actual) && actual.publisher_present &&
        !(actual.signing_flags & kSecCodeSignatureAdhoc) &&
        (actual.signing_flags & kSecCodeSignatureRuntime) && ph_native_identity_equal(&actual, expected);
}
