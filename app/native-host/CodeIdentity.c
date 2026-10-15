#include "Capture.h"
#include <Security/Security.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <mach-o/loader.h>
#include <mach/machine.h>

static bool thin_header(struct mach_header_64 header) {
#if defined(__arm64__)
    return header.magic == MH_MAGIC_64 && header.cputype == CPU_TYPE_ARM64
        && header.cpusubtype == CPU_SUBTYPE_ARM64_ALL && header.filetype == MH_EXECUTE;
#else
    (void)header; return false;
#endif
}

static bool thin_native(const char *path) {
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return false;
    struct mach_header_64 header;
    bool okay = pread(fd, &header, sizeof(header), 0) == sizeof(header) && thin_header(header);
    close(fd); return okay;
}

static bool policy(CFDictionaryRef information) {
    CFTypeRef flags = CFDictionaryGetValue(information, kSecCodeInfoFlags);
    uint32_t bits = 0;
    if (!flags || CFGetTypeID(flags) != CFNumberGetTypeID()
        || !CFNumberGetValue(flags, kCFNumberSInt32Type, &bits) || !(bits & kSecCodeSignatureRuntime)) return false;
    CFTypeRef value = CFDictionaryGetValue(information, kSecCodeInfoEntitlementsDict);
    if (!value) return true;
    if (CFGetTypeID(value) != CFDictionaryGetTypeID()) return false;
    return CFDictionaryGetCount(value) == 0;
}

static bool code(const char *path, const char *main, const char *pin) {
    if (!path || !pin || strlen(pin) != 40 || !thin_native(main)) return false;
    for (size_t index = 0; index < 40; index++)
        if (!((pin[index] >= '0' && pin[index] <= '9') || (pin[index] >= 'a' && pin[index] <= 'f'))) return false;
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)path, strlen(path), false);
    if (!url) return false;
    SecStaticCodeRef code = NULL;
    OSStatus status = SecStaticCodeCreateWithPath(url, kSecCSDefaultFlags, &code);
    CFRelease(url);
    if (status || !code) return false;
    SecCSFlags checks = kSecCSStrictValidate | kSecCSCheckAllArchitectures | kSecCSCheckNestedCode;
    status = SecStaticCodeCheckValidity(code, checks, NULL);
    CFDictionaryRef information = NULL;
    bool okay = !status && !SecCodeCopySigningInformation(code, kSecCSSigningInformation, &information) && information;
    if (okay) {
        CFTypeRef unique = CFDictionaryGetValue(information, kSecCodeInfoUnique);
        okay = unique && CFGetTypeID(unique) == CFDataGetTypeID() && CFDataGetLength(unique) == 20 && policy(information);
        const UInt8 *bytes = okay ? CFDataGetBytePtr(unique) : NULL;
        const char alphabet[] = "0123456789abcdef";
        for (size_t index = 0; okay && index < 20; index++)
            okay = pin[index * 2] == alphabet[bytes[index] >> 4] && pin[index * 2 + 1] == alphabet[bytes[index] & 15];
    }
    if (information) CFRelease(information);
    CFRelease(code); return okay;
}

bool ph_host_protected_code(const char *directory, const char *paths, const char *helper_pin, const char *gui_pin) {
    if (!directory || !paths || !ph_host_protected_tree(directory, paths)) return false;
    size_t size = strlen(directory) + 64;
    char *helper = malloc(size), *gui = malloc(size), *main = malloc(size);
    if (!helper || !gui || !main) { free(helper); free(gui); free(main); return false; }
    snprintf(helper, size, "%s/Helper/PoolhouseAuthority", directory);
    snprintf(gui, size, "%s/GUI/PoolhouseApproval.app", directory);
    snprintf(main, size, "%s/GUI/PoolhouseApproval.app/Contents/MacOS/PoolhouseApproval", directory);
    bool okay = code(helper, helper, helper_pin) && code(gui, main, gui_pin) && ph_host_protected_tree(directory, paths);
    free(helper); free(gui); free(main); return okay;
}

#ifdef TESTING
bool ph_host_test_header(uint32_t magic, int32_t cpu, int32_t subtype, uint32_t type) {
    struct mach_header_64 header = {0};
    header.magic = magic; header.cputype = cpu; header.cpusubtype = subtype; header.filetype = type;
    return thin_header(header);
}

bool ph_host_test_policy(uint32_t flags, const char *entitlement) {
    CFNumberRef number = CFNumberCreate(NULL, kCFNumberSInt32Type, &flags);
    CFMutableDictionaryRef info = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFMutableDictionaryRef entitlements = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFStringRef name = entitlement ? CFStringCreateWithCString(NULL, entitlement, kCFStringEncodingUTF8) : NULL;
    if (name) { CFDictionarySetValue(entitlements, name, kCFBooleanTrue); CFRelease(name); }
    CFDictionarySetValue(info, kSecCodeInfoFlags, number);
    CFDictionarySetValue(info, kSecCodeInfoEntitlementsDict, entitlements);
    bool okay = policy(info);
    CFRelease(number); CFRelease(entitlements); CFRelease(info); return okay;
}
#endif
