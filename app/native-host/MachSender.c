#include "MachSender.h"
#include <bsm/libbsm.h>
#include <limits.h>
#include <string.h>

_Static_assert(sizeof(audit_token_t) == 8 * sizeof(uint32_t), "eight SDK audit words");
_Static_assert(sizeof(int) == sizeof(uint32_t), "lossless libbsm PID-version bits");

/* Factored from the measured own-process ABI fixture: check actual allocation and
 * returned message size BEFORE rounding, then check trailer format and extent.
 * The opaque message body is never a source of sender identity. */
static bool extract(const void *bytes, size_t capacity, ph_mach_sender_observation *output) {
    if (!bytes || !output || capacity < sizeof(mach_msg_header_t)) return false;
    mach_msg_header_t header;
    memcpy(&header, bytes, sizeof(header));
    if (header.msgh_size < sizeof(header) || header.msgh_size > capacity
        || (header.msgh_bits & MACH_MSGH_BITS_COMPLEX)) return false;
    size_t size = header.msgh_size, rounding = sizeof(natural_t) - 1;
    if (size > SIZE_MAX - rounding) return false;
    size_t offset = (size + rounding) & ~rounding;
    if (offset > capacity || sizeof(mach_msg_trailer_t) > capacity - offset) return false;
    mach_msg_trailer_t base;
    memcpy(&base, (const unsigned char *)bytes + offset, sizeof(base));
    if (base.msgh_trailer_type != MACH_MSG_TRAILER_FORMAT_0
        || base.msgh_trailer_size < sizeof(mach_msg_audit_trailer_t)
        || base.msgh_trailer_size > capacity - offset) return false;
    mach_msg_audit_trailer_t trailer;
    memcpy(&trailer, (const unsigned char *)bytes + offset, sizeof(trailer));
    ph_mach_sender_observation observed = {0};
    observed.audit = trailer.msgh_audit;
    observed.pid = (uint32_t)audit_token_to_pid(observed.audit);
    int version = audit_token_to_pidversion(observed.audit);
    memcpy(&observed.pidversion, &version, sizeof(version));
    *output = observed;
    return true;
}

ph_mach_observation_result ph_mach_observe_sender(mach_port_t receive,
    void *buffer, mach_msg_size_t capacity, mach_msg_timeout_t timeout,
    ph_mach_sender_observation *output, mach_msg_return_t *kernel_error) {
    if (kernel_error) *kernel_error = MACH_MSG_SUCCESS;
    if (!MACH_PORT_VALID(receive) || !buffer || !output || !timeout
        || (uintptr_t)buffer % _Alignof(mach_msg_header_t)
        || capacity < sizeof(mach_msg_header_t) + sizeof(mach_msg_max_trailer_t))
        return PH_MACH_INVALID_ARGUMENT;
    mach_msg_return_t result = mach_msg(buffer, MACH_RCV_MSG | MACH_RCV_TIMEOUT
        | MACH_RCV_TRAILER_TYPE(MACH_MSG_TRAILER_FORMAT_0)
        | MACH_RCV_TRAILER_ELEMENTS(MACH_RCV_TRAILER_AUDIT),
        0, capacity, receive, timeout, MACH_PORT_NULL);
    if (kernel_error) *kernel_error = result;
    if (result != MACH_MSG_SUCCESS) return PH_MACH_RECEIVE_FAILED;
    bool okay = extract(buffer, capacity, output);
    /* Only a successful kernel receive reaches destroy: never attacker-supplied
     * userspace bytes. Handles simple reply/voucher rights and complex descriptors
     * even when the latter message shape is refused above. */
    mach_msg_destroy(buffer);
    return okay ? PH_MACH_OBSERVED : PH_MACH_INVALID_MESSAGE;
}
#ifdef TESTING
bool ph_mach_test_extract(const void *bytes, size_t capacity, ph_mach_sender_observation *output) {
    return extract(bytes, capacity, output);
}
#endif
