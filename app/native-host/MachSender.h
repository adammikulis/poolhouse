#ifndef PH_MACH_SENDER_H
#define PH_MACH_SENDER_H
#include <bsm/audit.h>
#include <mach/mach.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Private kernel observation only. No issuer, caller admission or process-lifetime proof.
 * The audit token names the sender at send time; it does not prove current liveness.
 * In particular, no contemporaneous PID lookup is promoted to generation-bound birth. */
typedef struct {
    audit_token_t audit;
    uint32_t pid;
    uint32_t pidversion;
} ph_mach_sender_observation;

typedef enum {
    PH_MACH_OBSERVED,
    PH_MACH_INVALID_ARGUMENT,
    PH_MACH_RECEIVE_FAILED,
    PH_MACH_INVALID_MESSAGE
} ph_mach_observation_result;

/* One kernel receive owns the actual message body, audit trailer and rights.
 * Complex messages, reply rights and vouchers are refused. Accessors borrow
 * data until destruction. An observation is not admission or a reply channel. */
typedef struct ph_mach_message ph_mach_message;
ph_mach_observation_result ph_mach_receive(mach_port_t receive, size_t maximum_body,
    mach_msg_timeout_t timeout, ph_mach_message **output, mach_msg_return_t *kernel_error);
const ph_mach_sender_observation *ph_mach_sender(const ph_mach_message *message);
const void *ph_mach_body(const ph_mach_message *message, size_t *size);
void ph_mach_message_destroy(ph_mach_message **message);
#ifdef TESTING
bool ph_mach_test_extract(const void *bytes, size_t capacity, ph_mach_sender_observation *output);
#endif
#endif
