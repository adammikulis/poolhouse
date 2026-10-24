#ifndef PH_MACH_CODE_H
#define PH_MACH_CODE_H
#include "MachSender.h"
#include <Security/Security.h>

/* Private historical observation. The caller owns code and must CFRelease it.
 * requirement is a comparison input, not admitted installation policy. Neither
 * code validity nor this copyable data proves owner, current liveness, protected
 * launch, message/body custody, receiver incarnation or authorization. */
typedef struct {
    ph_mach_sender_observation sender;
    SecCodeRef code;
} ph_mach_code_observation;

typedef enum {
    PH_MACH_CODE_OBSERVED,
    PH_MACH_CODE_INVALID_ARGUMENT,
    PH_MACH_CODE_RECEIVE_FAILED,
    PH_MACH_CODE_UNKNOWN
} ph_mach_code_result;

/* One actual receive through MachSender, then immediate public audit-token code
 * lookup and no-network validity comparison. No PID/path or weaker fallback.
 * Output changes only on success; release any previous owned output separately.
 * buffer/output cannot alias. Message-right cleanup follows MachSender's contract.
 * This is not a token-injection API, verified sender or owner/provider admission. */
ph_mach_code_result ph_mach_observe_code(mach_port_t receive, void *buffer,
    mach_msg_size_t capacity, mach_msg_timeout_t timeout, SecRequirementRef requirement,
    ph_mach_code_observation *output);
#endif
