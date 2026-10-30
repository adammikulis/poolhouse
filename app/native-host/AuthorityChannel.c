#include "AuthorityChannel.h"
#include <dispatch/dispatch.h>
#include <stdlib.h>
#include <string.h>

/* One parser, owned by the Rust authority library. */
extern uint8_t ph_authority_frame_kind(const uint8_t *bytes, size_t length);
enum { FRAME_LIMIT = 326 };

struct ph_authority_channel {
    xpc_connection_t connection;
    dispatch_queue_t queue;
    ph_native_xpc_caller_policy policy;
    ph_channel_handler handler;
    void *context;
    bool closed;
};

static void disposed(void *context) {
    ph_authority_channel *channel = context;
    ph_channel_handler handler = channel->handler;
    void *owner = channel->context;
    dispatch_queue_t queue = channel->queue;
    CFRelease(channel->policy.requirement);
    free(channel);
    dispatch_async(queue, ^{ handler(owner,NULL,PH_CHANNEL_DISPOSED,NULL,0); });
    dispatch_release(queue);
}

static void close_channel(ph_authority_channel *channel) {
    if (channel->closed) return;
    channel->closed = true;
    xpc_connection_cancel(channel->connection);
    xpc_connection_t connection = channel->connection;
    channel->handler(channel->context,channel,PH_CHANNEL_END,NULL,0);
    xpc_release(connection);
}

void ph_authority_channel_close(ph_authority_channel *channel) {
    if (!channel || dispatch_get_specific(channel) != channel) return;
    close_channel(channel);
}

static void receive(ph_authority_channel *channel, xpc_object_t message) {
    if (channel->closed) return;
    if (xpc_get_type(message) != XPC_TYPE_DICTIONARY ||
        !ph_native_xpc_caller_matches(message,channel->connection,&channel->policy) ||
        xpc_dictionary_get_count(message) != 1) { close_channel(channel); return; }
    xpc_object_t value = xpc_dictionary_get_value(message,"frame");
    if (!value || xpc_get_type(value) != XPC_TYPE_DATA) { close_channel(channel); return; }
    size_t length = xpc_data_get_length(value);
    if (!length || length > FRAME_LIMIT) { close_channel(channel); return; }
    uint8_t *frame = malloc(length);
    if (!frame || xpc_data_get_bytes(value,frame,0,length) != length) { free(frame); close_channel(channel); return; }
    if (!ph_authority_frame_kind(frame,length)) { free(frame); close_channel(channel); return; }
    channel->handler(channel->context,channel,PH_CHANNEL_FRAME,frame,length);
    free(frame);
}

/* XPC objects may not be released while inactive. The no-op handler owns no
 * external state and makes a failed setup safe to cancel and release. */
static void discard_inactive(xpc_connection_t connection) {
    xpc_connection_set_event_handler(connection, ^(xpc_object_t event) { (void)event; });
    xpc_connection_activate(connection);
    xpc_connection_cancel(connection);
    xpc_release(connection);
}

bool ph_authority_channel_send(ph_authority_channel *channel, const uint8_t *frame, size_t length) {
    if (!channel || channel->closed || !frame || !length || length > FRAME_LIMIT ||
        dispatch_get_specific(channel) != channel) return false;
    if (!ph_authority_frame_kind(frame,length)) return false;
    xpc_object_t message = xpc_dictionary_create(NULL,NULL,0);
    if (!message) { close_channel(channel); return false; }
    xpc_dictionary_set_data(message,"frame",frame,length);
    xpc_connection_send_message(channel->connection,message);
    xpc_release(message);
    return true; /* XPC accepted the send; delivery is not confirmed. */
}

static bool open_channel(xpc_connection_t connection, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context) {
    if (!connection || xpc_get_type(connection) != XPC_TYPE_CONNECTION) return false;
    if (!policy || !policy->requirement || !handler) { discard_inactive(connection); return false; }
    CFStringRef text = NULL;
    if (SecRequirementCopyString(policy->requirement,kSecCSDefaultFlags,&text) || !text) {
        if (text) CFRelease(text);
        discard_inactive(connection);
        return false;
    }
    char requirement[16385];
    CFIndex length = CFStringGetLength(text), written = 0;
    CFIndex converted_count = CFStringGetBytes(text,CFRangeMake(0,length),kCFStringEncodingUTF8,0,false,
        (UInt8 *)requirement,sizeof(requirement)-1,&written);
    bool converted = length > 0 && converted_count == length && written > 0 && written < (CFIndex)sizeof(requirement)
        && !memchr(requirement,0,(size_t)written);
    if (converted) requirement[written] = 0;
    CFRelease(text);
    if (!converted || xpc_connection_set_peer_code_signing_requirement(connection,requirement)) { discard_inactive(connection); return false; }
    ph_authority_channel *channel = calloc(1,sizeof(*channel));
    if (!channel) { discard_inactive(connection); return false; }
    channel->queue = dispatch_queue_create("app.poolhouse.authority.channel",DISPATCH_QUEUE_SERIAL);
    if (!channel->queue) { free(channel); discard_inactive(connection); return false; }
    channel->connection = connection;
    channel->policy = *policy;
    CFRetain(channel->policy.requirement);
    channel->handler = handler;
    channel->context = context;
    dispatch_queue_set_specific(channel->queue,channel,channel,NULL);
    xpc_connection_set_target_queue(connection,channel->queue);
    xpc_connection_set_context(connection,channel);
    xpc_connection_set_finalizer_f(connection,disposed);
    xpc_connection_set_event_handler(connection, ^(xpc_object_t event) { receive(channel,event); });
    dispatch_suspend(channel->queue);
    dispatch_async(channel->queue, ^{ if (!channel->closed) channel->handler(channel->context,channel,PH_CHANNEL_START,NULL,0); });
    xpc_connection_activate(connection);
    dispatch_resume(channel->queue);
    return true;
}

bool ph_authority_channel_connect(const ph_native_xpc_caller_policy *policy, ph_channel_handler handler, void *context) {
    xpc_connection_t connection = xpc_connection_create_mach_service("app.poolhouse.authority",NULL,XPC_CONNECTION_MACH_SERVICE_PRIVILEGED);
    if (!connection) return false;
    if (open_channel(connection,policy,handler,context)) return true;
    /* open_channel owns the connection after installing its finalizer. */
    return false;
}

bool ph_authority_channel_accept(xpc_connection_t connection, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context) {
    if (!connection || xpc_get_type(connection) != XPC_TYPE_CONNECTION) return false;
    xpc_retain(connection);
    return open_channel(connection,policy,handler,context);
}

#ifdef TESTING
bool ph_authority_channel_test_connect(xpc_endpoint_t endpoint, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context) {
    if (!endpoint || xpc_get_type(endpoint) != XPC_TYPE_ENDPOINT) return false;
    xpc_connection_t connection = xpc_connection_create_from_endpoint(endpoint);
    return connection && open_channel(connection,policy,handler,context);
}
#endif
