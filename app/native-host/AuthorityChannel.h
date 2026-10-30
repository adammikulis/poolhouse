#ifndef PH_AUTHORITY_CHANNEL_H
#define PH_AUTHORITY_CHANNEL_H
#include "NativeCaller.h"
#include <stddef.h>
#include <stdint.h>

/* A serial transport candidate, never an authenticated Store witness. A
 * caller must establish protected policy and bind each frame to Store/session
 * authority separately. Only handshake frames are supported here. */
typedef struct ph_authority_channel ph_authority_channel;
typedef enum { PH_CHANNEL_START, PH_CHANNEL_FRAME, PH_CHANNEL_END, PH_CHANNEL_DISPOSED } ph_channel_event;
typedef void (*ph_channel_handler)(void *context, ph_authority_channel *channel,
    ph_channel_event event, const uint8_t *frame, size_t length);

/* Connect to the fixed privileged service. The handler is always called on
 * this channel's serial queue; context remains live until DISPOSED. A FRAME
 * pointer is borrowed only for that callback and must be copied to retain it. */
bool ph_authority_channel_connect(const ph_native_xpc_caller_policy *policy, ph_channel_handler handler, void *context);
/* Accept only an XPC connection delivered by the service's actual listener.
 * This function cannot authenticate the listener or authorize its caller. */
bool ph_authority_channel_accept(xpc_connection_t connection, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context);
/* Send and close are valid only from this channel's callback. Send borrows an
 * immutable frame for the call and XPC copies it; callers must not encode
 * secrets into handshake fields. There is no operation-send API. Close consumes
 * access to the channel; context remains alive through DISPOSED. */
bool ph_authority_channel_send(ph_authority_channel *channel, const uint8_t *frame, size_t length);
void ph_authority_channel_close(ph_authority_channel *channel);
#ifdef TESTING
bool ph_authority_channel_test_connect(xpc_endpoint_t endpoint, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context);
#endif
#endif
