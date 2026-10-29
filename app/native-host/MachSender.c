#include "MachSender.h"
#include <bsm/libbsm.h>
#include <limits.h>
#include <stdlib.h>
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
    mach_msg_bits_t local = MACH_MSGH_BITS_LOCAL(header.msgh_bits);
    if (header.msgh_size < sizeof(header) || header.msgh_size > capacity
        || MACH_MSGH_BITS_REMOTE(header.msgh_bits) != 0
        || MACH_MSGH_BITS_VOUCHER(header.msgh_bits) != 0
        || MACH_MSGH_BITS_OTHER(header.msgh_bits) != 0
        || (local != 0 && local != MACH_MSG_TYPE_PORT_SEND
            && local != MACH_MSG_TYPE_PORT_SEND_ONCE)
        || header.msgh_remote_port != MACH_PORT_NULL
        || header.msgh_voucher_port != MACH_PORT_NULL) return false;
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

struct ph_mach_message {
    mach_msg_header_t *buffer;
    size_t body_size;
    ph_mach_sender_observation sender;
};

/* Existing approval-card frame limit; this is a message bound, not authority. */
#define PH_MACH_MAX_BODY 98304u

ph_mach_observation_result ph_mach_receive(mach_port_t receive, size_t maximum_body,
    mach_msg_timeout_t timeout, ph_mach_message **output, mach_msg_return_t *kernel_error) {
    if (kernel_error) *kernel_error = MACH_MSG_SUCCESS;
    if (!MACH_PORT_VALID(receive) || !output || *output || !timeout || maximum_body == 0 || maximum_body > PH_MACH_MAX_BODY)
        return PH_MACH_INVALID_ARGUMENT;
    size_t capacity = sizeof(mach_msg_header_t) + maximum_body + sizeof(natural_t) + sizeof(mach_msg_max_trailer_t);
    ph_mach_message *message = calloc(1, sizeof(*message));
    if (!message) return PH_MACH_RECEIVE_FAILED;
    message->buffer = calloc(1, capacity);
    if (!message->buffer) { free(message); return PH_MACH_RECEIVE_FAILED; }
    mach_msg_header_t *buffer = message->buffer;
    mach_msg_return_t result = mach_msg(buffer, MACH_RCV_MSG | MACH_RCV_TIMEOUT
        | MACH_RCV_TRAILER_TYPE(MACH_MSG_TRAILER_FORMAT_0)
        | MACH_RCV_TRAILER_ELEMENTS(MACH_RCV_TRAILER_AUDIT),
        0, (mach_msg_size_t)capacity, receive, timeout, MACH_PORT_NULL);
    if (kernel_error) *kernel_error = result;
    if (result != MACH_MSG_SUCCESS) { free(buffer); free(message); return PH_MACH_RECEIVE_FAILED; }
    bool okay = extract(buffer, capacity, &message->sender);
    if (okay) {
        message->body_size = buffer->msgh_size - sizeof(*buffer);
        okay = message->body_size <= maximum_body;
    }
    if (!okay) { mach_msg_destroy(buffer); free(buffer); free(message); return PH_MACH_INVALID_MESSAGE; }
    *output = message;
    return PH_MACH_OBSERVED;
}

const ph_mach_sender_observation *ph_mach_sender(const ph_mach_message *message) {
    return message ? &message->sender : NULL;
}

const void *ph_mach_body(const ph_mach_message *message, size_t *size) {
    if (!message || !size) return NULL;
    *size = message->body_size;
    return (const unsigned char *)message->buffer + sizeof(mach_msg_header_t);
}

void ph_mach_message_destroy(ph_mach_message **message) {
    if (!message || !*message) return;
    ph_mach_message *owned = *message;
    *message = NULL;
    mach_msg_destroy(owned->buffer);
    free(owned->buffer);
    free(owned);
}
#ifdef TESTING
bool ph_mach_test_extract(const void *bytes, size_t capacity, ph_mach_sender_observation *output) {
    return extract(bytes, capacity, output);
}
#endif
