#ifndef PH_AUTHORITY_CHANNEL_H
#define PH_AUTHORITY_CHANNEL_H
#include "NativeCaller.h"
#include <stddef.h>
#include <stdint.h>

/* A serial transport candidate, never an authenticated Store witness. A
 * caller must establish protected policy and bind each frame to Store/session
 * authority separately. Only handshake frames are supported here. */
typedef struct ph_authority_channel ph_authority_channel;
typedef struct ph_authority_connection_control ph_authority_connection_control;
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
/* Recheck the actual current FRAME sender using independently authenticated
 * policy. Valid only inside its channel callback; this is an observation, not
 * an authorization, recipient check or cancellation/commit fence. */
bool ph_authority_channel_revalidate_current(ph_authority_channel *channel,
    const ph_native_xpc_caller_policy *policy);
/* A callback may copy one owned control for cancellation from another thread.
 * Cancel is idempotent and cannot preempt a callback already running. It never
 * proves delivery or effect settlement. Each
 * copied control must be released exactly once; it remains inert after END. */
ph_authority_connection_control *ph_authority_connection_control_copy(ph_authority_channel *channel);
void ph_authority_connection_control_cancel(ph_authority_connection_control *control);
void ph_authority_connection_control_release(ph_authority_connection_control *control);

/* The listener owns the fixed service and its bounded adopted connections.
 * Its policy is a candidate until the service binds it to protected Store
 * state; the listener never issues an operation or an authority witness. */
typedef struct ph_authority_listener ph_authority_listener;
typedef struct ph_authority_session ph_authority_session;
typedef enum { PH_LISTENER_START, PH_LISTENER_END, PH_LISTENER_DISPOSED } ph_listener_event;
typedef void (*ph_listener_handler)(void *context, ph_authority_listener *listener, ph_listener_event event);
typedef void (*ph_session_handler)(void *context, ph_authority_session *session, ph_authority_channel *channel,
    ph_channel_event event, const uint8_t *frame, size_t length);
/* Context remains live through LISTENER_DISPOSED; a session pointer is valid
 * only until its CHANNEL_DISPOSED callback returns. */
bool ph_authority_listener_start(const ph_native_xpc_caller_policy *policy, ph_listener_handler handler,
    ph_session_handler session_handler, void *context);
/* Listener stop is valid only from a listener callback. A session callback
 * can request the same stop while its session remains owned. */
void ph_authority_listener_stop(ph_authority_listener *listener);
void ph_authority_session_stop_listener(ph_authority_session *session);
/* Copy on the listener callback queue to request cancellation off-queue using
 * the same connection-control cancel/release API as a channel. */
ph_authority_connection_control *ph_authority_listener_control_copy(ph_authority_listener *listener);
#ifdef TESTING
/* Test hook runs immediately before enqueue while the cancel gate is held. */
void ph_authority_channel_test_send_hook(ph_authority_channel *channel, void (*hook)(void *), void *context);
bool ph_authority_channel_test_connect(xpc_endpoint_t endpoint, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context);
bool ph_authority_channel_test_service(const char *name, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context);
pid_t ph_authority_channel_test_pid(ph_authority_channel *channel);
int ph_authority_channel_test_error(ph_authority_channel *channel);
bool ph_authority_listener_test_start(const ph_native_xpc_caller_policy *policy, ph_listener_handler handler,
    ph_session_handler session_handler, void *context);
xpc_endpoint_t ph_authority_listener_test_endpoint(ph_authority_listener *listener);
#endif
#endif
