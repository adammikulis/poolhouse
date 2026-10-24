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

/* Trusted in-process caller supplies its owned receive right and writable allocation.
 * One receive, no retries. timeout must be finite/nonzero. This consumes one queued
 * message; on success all carried rights are destroyed after extraction. On error
 * the caller retains port custody and must settle/destroy its own queue/receive right.
 * buffer/output cannot alias. Output changes only on PH_MACH_OBSERVED.
 * kernel_error is optional; it receives the actual receive status, never body data. */
ph_mach_observation_result ph_mach_observe_sender(mach_port_t receive,
    void *buffer, mach_msg_size_t capacity, mach_msg_timeout_t timeout,
    ph_mach_sender_observation *output, mach_msg_return_t *kernel_error);
#ifdef TESTING
bool ph_mach_test_extract(const void *bytes, size_t capacity, ph_mach_sender_observation *output);
#endif
#endif
