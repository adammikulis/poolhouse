#ifndef PH_NATIVE_REQUEST_H
#define PH_NATIVE_REQUEST_H
#include "MachSender.h"

/* Confine the request and its right to one authority-process dispatch owner.
 * The reply route is caller-selected, not recipient authentication: no token,
 * capability, credential or approval evidence may use this public reply. */
ph_mach_observation_result ph_mach_receive_request(mach_port_t receive, size_t maximum_body,
    mach_msg_timeout_t timeout, ph_mach_message **output, mach_msg_return_t *kernel_error);
typedef enum { PH_PUBLIC_REPLY_NOT_SENT, PH_PUBLIC_REPLY_SENT_UNCONFIRMED } ph_public_reply_result;
ph_public_reply_result ph_mach_reply_public(ph_mach_message **message, const uint8_t *bytes, size_t length);
#ifdef TESTING
void ph_mach_test_fail_public_reply(mach_msg_return_t result);
#endif
#endif
