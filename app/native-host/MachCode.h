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

/* Verify the actual sender of one still-owned kernel message. Success transfers a
 * retained SecCodeRef; the caller keeps the message and releases the code itself.
 * This is an observation, not owner, session or person admission. */
ph_mach_code_result ph_mach_observe_code(const ph_mach_message *message,
    SecRequirementRef requirement, ph_mach_code_observation *output);
#endif
