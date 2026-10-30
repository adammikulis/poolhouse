#include "AuthorityChannel.h"
#include <dispatch/dispatch.h>
#include <limits.h>
#include <pthread.h>
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
    ph_authority_connection_control *control;
    bool closed;
#ifdef TESTING
    void (*send_hook)(void *);
    void *send_hook_context;
#endif
};

struct ph_authority_connection_control {
    pthread_mutex_t lock;
    unsigned references;
    xpc_connection_t connection;
};

static ph_authority_connection_control *new_control(xpc_connection_t connection) {
    ph_authority_connection_control *control = calloc(1,sizeof(*control));
    if (!control) return NULL;
    if (pthread_mutex_init(&control->lock,NULL)) { free(control); return NULL; }
    control->references = 1;
    control->connection = connection;
    xpc_retain(connection);
    return control;
}

static ph_authority_connection_control *copy_control(ph_authority_connection_control *control) {
    pthread_mutex_lock(&control->lock);
    bool okay = control->connection && control->references != UINT_MAX;
    if (okay) control->references++;
    pthread_mutex_unlock(&control->lock);
    return okay ? control : NULL;
}

static xpc_connection_t take_connection(ph_authority_connection_control *control) {
    pthread_mutex_lock(&control->lock);
    xpc_connection_t connection = control->connection;
    control->connection = NULL;
    pthread_mutex_unlock(&control->lock);
    return connection;
}

void ph_authority_connection_control_cancel(ph_authority_connection_control *control) {
    if (!control) return;
    xpc_connection_t connection = take_connection(control);
    if (connection) { xpc_connection_cancel(connection); xpc_release(connection); }
}

void ph_authority_connection_control_release(ph_authority_connection_control *control) {
    if (!control) return;
    pthread_mutex_lock(&control->lock);
    bool last = --control->references == 0;
    xpc_connection_t connection = last ? control->connection : NULL;
    if (last) control->connection = NULL;
    pthread_mutex_unlock(&control->lock);
    if (connection) { xpc_connection_cancel(connection); xpc_release(connection); }
    if (last) { pthread_mutex_destroy(&control->lock); free(control); }
}

ph_authority_connection_control *ph_authority_connection_control_copy(ph_authority_channel *channel) {
    if (!channel || channel->closed || dispatch_get_specific(channel) != channel) return NULL;
    return copy_control(channel->control);
}

static bool control_active(ph_authority_connection_control *control) {
    pthread_mutex_lock(&control->lock);
    bool active = control->connection != NULL;
    pthread_mutex_unlock(&control->lock);
    return active;
}

static void disposed(void *context) {
    ph_authority_channel *channel = context;
    ph_channel_handler handler = channel->handler;
    void *owner = channel->context;
    dispatch_queue_t queue = channel->queue;
    CFRelease(channel->policy.requirement);
    ph_authority_connection_control_release(channel->control);
    free(channel);
    dispatch_async(queue, ^{ handler(owner,NULL,PH_CHANNEL_DISPOSED,NULL,0); });
    dispatch_release(queue);
}

static void close_channel(ph_authority_channel *channel) {
    if (channel->closed) return;
    channel->closed = true;
    xpc_connection_cancel(channel->connection);
    xpc_connection_t held = take_connection(channel->control);
    if (held) xpc_release(held);
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
    if (!control_active(channel->control)) { close_channel(channel); return; }
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

static bool set_requirement(xpc_connection_t connection, SecRequirementRef requirement_ref) {
    if (!requirement_ref) return false;
    CFStringRef text = NULL;
    if (SecRequirementCopyString(requirement_ref,kSecCSDefaultFlags,&text) || !text) {
        if (text) CFRelease(text);
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
    return converted && !xpc_connection_set_peer_code_signing_requirement(connection,requirement);
}

bool ph_authority_channel_send(ph_authority_channel *channel, const uint8_t *frame, size_t length) {
    if (!channel || channel->closed || !frame || !length || length > FRAME_LIMIT ||
        dispatch_get_specific(channel) != channel) return false;
    if (!ph_authority_frame_kind(frame,length)) return false;
    xpc_object_t message = xpc_dictionary_create(NULL,NULL,0);
    if (!message) { close_channel(channel); return false; }
    xpc_dictionary_set_data(message,"frame",frame,length);
    pthread_mutex_lock(&channel->control->lock);
    bool active = channel->control->connection != NULL;
#ifdef TESTING
    if (active && channel->send_hook) channel->send_hook(channel->send_hook_context);
#endif
    if (active) xpc_connection_send_message(channel->connection,message);
    pthread_mutex_unlock(&channel->control->lock);
    xpc_release(message);
    return active; /* An accepted send is not confirmed delivery. */
}

static bool open_channel(xpc_connection_t connection, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context) {
    if (!connection || xpc_get_type(connection) != XPC_TYPE_CONNECTION) return false;
    if (!policy || !policy->requirement || !handler) { discard_inactive(connection); return false; }
    if (!set_requirement(connection,policy->requirement)) { discard_inactive(connection); return false; }
    ph_authority_channel *channel = calloc(1,sizeof(*channel));
    if (!channel) { discard_inactive(connection); return false; }
    channel->queue = dispatch_queue_create("app.poolhouse.authority.channel",DISPATCH_QUEUE_SERIAL);
    if (!channel->queue) { free(channel); discard_inactive(connection); return false; }
    channel->control = new_control(connection);
    if (!channel->control) { dispatch_release(channel->queue); free(channel); discard_inactive(connection); return false; }
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

enum { LISTENER_LIMIT = 32 };
struct ph_authority_session {
    ph_authority_listener *listener;
    xpc_connection_t connection; /* owned and released on the listener queue */
    ph_authority_channel *channel; /* borrowed only on the channel queue */
    struct ph_authority_session *next;
};
struct ph_authority_listener {
    xpc_connection_t connection;
    dispatch_queue_t queue;
    ph_native_xpc_caller_policy policy;
    ph_listener_handler handler;
    ph_session_handler session_handler;
    void *context;
    ph_authority_connection_control *control;
    ph_authority_session *sessions;
    size_t count;
    bool stopping;
    bool finalized;
};

static void listener_maybe_dispose(ph_authority_listener *listener) {
    if (!listener->stopping || !listener->finalized || listener->count) return;
    ph_listener_handler handler = listener->handler;
    void *context = listener->context;
    dispatch_queue_t queue = listener->queue;
    CFRelease(listener->policy.requirement);
    ph_authority_connection_control_release(listener->control);
    free(listener);
    handler(context,NULL,PH_LISTENER_DISPOSED);
    dispatch_release(queue);
}

static void listener_stop_on_queue(ph_authority_listener *listener) {
    if (listener->stopping) return;
    listener->stopping = true;
    xpc_connection_cancel(listener->connection);
    xpc_connection_t held = take_connection(listener->control);
    if (held) xpc_release(held);
    for (ph_authority_session *session = listener->sessions; session; session = session->next)
        if (session->connection) xpc_connection_cancel(session->connection);
    listener->handler(listener->context,listener,PH_LISTENER_END);
    xpc_release(listener->connection);
}

void ph_authority_listener_stop(ph_authority_listener *listener) {
    if (!listener || dispatch_get_specific(listener) != listener) return;
    listener_stop_on_queue(listener);
}

ph_authority_connection_control *ph_authority_listener_control_copy(ph_authority_listener *listener) {
    if (!listener || listener->stopping || dispatch_get_specific(listener) != listener) return NULL;
    return copy_control(listener->control);
}

void ph_authority_session_stop_listener(ph_authority_session *session) {
    if (!session || !session->channel || dispatch_get_specific(session->channel) != session->channel) return;
    ph_authority_listener *listener = session->listener;
    /* The invoking session keeps the listener alive through its DISPOSED
     * callback; submission precedes that callback on the channel queue. */
    dispatch_async(listener->queue, ^{ listener_stop_on_queue(listener); });
}

static void listener_finalized(void *context) {
    ph_authority_listener *listener = context;
    dispatch_async(listener->queue, ^{ listener->finalized = true; listener_maybe_dispose(listener); });
}

static void session_event(void *context, ph_authority_channel *channel, ph_channel_event event,
    const uint8_t *frame, size_t length) {
    ph_authority_session *session = context;
    ph_authority_listener *listener = session->listener;
    if (event == PH_CHANNEL_START) session->channel = channel;
    listener->session_handler(listener->context,session,channel,event,frame,length);
    if (event == PH_CHANNEL_END) {
        session->channel = NULL;
        dispatch_async(listener->queue, ^{
            if (session->connection) { xpc_release(session->connection); session->connection = NULL; }
        });
    } else if (event == PH_CHANNEL_DISPOSED) {
        dispatch_async(listener->queue, ^{
            ph_authority_session **entry = &listener->sessions;
            while (*entry && *entry != session) entry = &(*entry)->next;
            if (*entry == session) { *entry = session->next; listener->count--; free(session); }
            listener_maybe_dispose(listener);
        });
    }
}

static void listener_event(ph_authority_listener *listener, xpc_object_t event) {
    if (listener->stopping) {
        if (xpc_get_type(event) == XPC_TYPE_CONNECTION) { xpc_retain(event); discard_inactive((xpc_connection_t)event); }
        return;
    }
    if (xpc_get_type(event) != XPC_TYPE_CONNECTION) { listener_stop_on_queue(listener); return; }
    xpc_connection_t connection = (xpc_connection_t)event;
    if (listener->count == LISTENER_LIMIT) { xpc_retain(connection); discard_inactive(connection); return; }
    ph_authority_session *session = calloc(1,sizeof(*session));
    if (!session) { xpc_retain(connection); discard_inactive(connection); return; }
    session->listener = listener;
    session->connection = connection;
    xpc_retain(connection); /* registry retain, released on END */
    session->next = listener->sessions;
    listener->sessions = session;
    listener->count++;
    if (!ph_authority_channel_accept(connection,&listener->policy,session_event,session)) {
        listener->sessions = session->next;
        listener->count--;
        xpc_release(session->connection);
        free(session);
    }
}

static bool open_listener(xpc_connection_t connection, const ph_native_xpc_caller_policy *policy,
    ph_listener_handler handler, ph_session_handler session_handler, void *context) {
    if (!connection || xpc_get_type(connection) != XPC_TYPE_CONNECTION) return false;
    if (!policy || !policy->requirement || !handler || !session_handler || !set_requirement(connection,policy->requirement)) {
        discard_inactive(connection); return false;
    }
    ph_authority_listener *listener = calloc(1,sizeof(*listener));
    if (!listener) { discard_inactive(connection); return false; }
    listener->queue = dispatch_queue_create("app.poolhouse.authority.listener",DISPATCH_QUEUE_SERIAL);
    if (!listener->queue) { free(listener); discard_inactive(connection); return false; }
    listener->control = new_control(connection);
    if (!listener->control) { dispatch_release(listener->queue); free(listener); discard_inactive(connection); return false; }
    listener->connection = connection;
    listener->policy = *policy;
    CFRetain(listener->policy.requirement);
    listener->handler = handler;
    listener->session_handler = session_handler;
    listener->context = context;
    dispatch_queue_set_specific(listener->queue,listener,listener,NULL);
    xpc_connection_set_target_queue(connection,listener->queue);
    xpc_connection_set_context(connection,listener);
    xpc_connection_set_finalizer_f(connection,listener_finalized);
    xpc_connection_set_event_handler(connection, ^(xpc_object_t event) { listener_event(listener,event); });
    dispatch_suspend(listener->queue);
    dispatch_async(listener->queue, ^{ listener->handler(listener->context,listener,PH_LISTENER_START); });
    xpc_connection_activate(connection);
    dispatch_resume(listener->queue);
    return true;
}

bool ph_authority_listener_start(const ph_native_xpc_caller_policy *policy, ph_listener_handler handler,
    ph_session_handler session_handler, void *context) {
    xpc_connection_t listener = xpc_connection_create_mach_service("app.poolhouse.authority",NULL,XPC_CONNECTION_MACH_SERVICE_LISTENER);
    return listener && open_listener(listener,policy,handler,session_handler,context);
}

#ifdef TESTING
void ph_authority_channel_test_send_hook(ph_authority_channel *channel, void (*hook)(void *), void *context) {
    if (!channel || dispatch_get_specific(channel) != channel) return;
    channel->send_hook = hook;
    channel->send_hook_context = context;
}
bool ph_authority_channel_test_connect(xpc_endpoint_t endpoint, const ph_native_xpc_caller_policy *policy,
    ph_channel_handler handler, void *context) {
    if (!endpoint || xpc_get_type(endpoint) != XPC_TYPE_ENDPOINT) return false;
    xpc_connection_t connection = xpc_connection_create_from_endpoint(endpoint);
    return connection && open_channel(connection,policy,handler,context);
}
bool ph_authority_listener_test_start(const ph_native_xpc_caller_policy *policy, ph_listener_handler handler,
    ph_session_handler session_handler, void *context) {
    xpc_connection_t listener = xpc_connection_create(NULL,NULL);
    return listener && open_listener(listener,policy,handler,session_handler,context);
}
xpc_endpoint_t ph_authority_listener_test_endpoint(ph_authority_listener *listener) {
    if (!listener || listener->stopping || dispatch_get_specific(listener) != listener) return NULL;
    return xpc_endpoint_create(listener->connection);
}
#endif
