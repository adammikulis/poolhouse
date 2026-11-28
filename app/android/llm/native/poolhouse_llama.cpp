// The phone's llama.cpp: load a GGUF model, answer a conversation piece by piece, stop on request.
// The Java side is app.poolhouse.llm.android.LlamaNative; text crosses as UTF-8 bytes.

#include <android/log.h>
#include <jni.h>

#include <atomic>
#include <chrono>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "chat.h"
#include "common.h"
#include "json.h"
#include "log.h"
#include "sampling.h"
#include "ggml-backend.h"
#include "llama.h"
#include "mtmd-helper.h"
#include "mtmd.h"

namespace {

constexpr const char * TAG = "PoolhouseLlama";

// The finish codes LlamaEngine.kt reads.
enum { FINISH_STOP = 0, FINISH_LENGTH = 1, FINISH_CANCELLED = 3 };

struct Session {
    llama_model * model = nullptr;
    llama_context * context = nullptr;
    mtmd_context * vision = nullptr;
    common_chat_templates_ptr templates;
    std::vector<llama_token> cached;  // the text tokens the memory holds, empty after pictures
    std::atomic<bool> stop{false};
    std::string device = "CPU";
    std::string kv = "f16";

    ~Session() {
        if (vision) mtmd_free(vision);
        templates.reset();
        if (context) llama_free(context);
        if (model) llama_model_free(model);
    }
};

// Warnings and errors only: nothing the runtime says below that reaches the phone's log.
void log_to_android(ggml_log_level level, const char * text, void *) {
    if (level == GGML_LOG_LEVEL_ERROR) __android_log_write(ANDROID_LOG_ERROR, TAG, text);
    else if (level == GGML_LOG_LEVEL_WARN) __android_log_write(ANDROID_LOG_WARN, TAG, text);
}

std::string bytes(JNIEnv * env, jbyteArray array) {
    if (!array) return "";
    const jsize size = env->GetArrayLength(array);
    std::string out(size, '\0');
    env->GetByteArrayRegion(array, 0, size, reinterpret_cast<jbyte *>(&out[0]));
    return out;
}

jbyteArray array_of(JNIEnv * env, const std::string & text) {
    jbyteArray out = env->NewByteArray(static_cast<jsize>(text.size()));
    env->SetByteArrayRegion(out, 0, static_cast<jsize>(text.size()), reinterpret_cast<const jbyte *>(text.data()));
    return out;
}

void raise(JNIEnv * env, const std::string & message) {
    if (!env->ExceptionCheck()) env->ThrowNew(env->FindClass("java/lang/IllegalStateException"), message.c_str());
}

// The length of the longest beginning of `text` that ends on a whole UTF-8 character.
size_t whole(const std::string & text) {
    size_t end = text.size();
    size_t back = 0;
    while (back < 4 && back < end) {
        const unsigned char c = text[end - 1 - back];
        if ((c & 0xC0) != 0x80) {
            const size_t need = c < 0x80 ? 1 : (c >> 5) == 0x6 ? 2 : (c >> 4) == 0xE ? 3 : 4;
            return back + 1 >= need ? end : end - back - 1;
        }
        back++;
    }
    return end;
}

int64_t micros_since(std::chrono::steady_clock::time_point from) {
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - from).count();
}

}  // namespace

extern "C" {

JNIEXPORT jstring JNICALL Java_app_poolhouse_llm_android_LlamaNative_initialise(JNIEnv * env, jobject, jstring directory) {
    llama_log_set(log_to_android, nullptr);
    mtmd_helper_log_set(log_to_android, nullptr);
    common_log_pause(common_log_main());
    const char * path = env->GetStringUTFChars(directory, nullptr);
    ggml_backend_load_all_from_path(path);
    env->ReleaseStringUTFChars(directory, path);
    llama_backend_init();
    std::string devices;
    for (size_t i = 0; i < ggml_backend_dev_count(); i++) {
        ggml_backend_dev_t device = ggml_backend_dev_get(i);
        const auto type = ggml_backend_dev_type(device);
        devices += type == GGML_BACKEND_DEVICE_TYPE_CPU ? "cpu\t" : "gpu\t";
        devices += std::string(ggml_backend_dev_name(device)) + "\t" + ggml_backend_dev_description(device) + "\n";
    }
    return env->NewStringUTF(devices.c_str());
}

JNIEXPORT jlong JNICALL Java_app_poolhouse_llm_android_LlamaNative_load(
        JNIEnv * env, jobject, jstring model_path, jstring projector_path, jboolean gpu, jint n_ctx, jint threads, jboolean quantised) {
    auto session = std::make_unique<Session>();
    const char * model_chars = env->GetStringUTFChars(model_path, nullptr);
    const std::string model_file = model_chars;
    env->ReleaseStringUTFChars(model_path, model_chars);
    std::string projector_file;
    if (projector_path) {
        const char * chars = env->GetStringUTFChars(projector_path, nullptr);
        projector_file = chars;
        env->ReleaseStringUTFChars(projector_path, chars);
    }
    try {
        ggml_backend_dev_t accelerator = nullptr;
        for (size_t i = 0; gpu && !accelerator && i < ggml_backend_dev_count(); i++) {
            ggml_backend_dev_t device = ggml_backend_dev_get(i);
            if (ggml_backend_dev_type(device) != GGML_BACKEND_DEVICE_TYPE_CPU) accelerator = device;
        }
        std::vector<ggml_backend_dev_t> devices;
        if (accelerator) devices.push_back(accelerator);
        devices.push_back(nullptr);
        llama_model_params model_params = llama_model_default_params();
        model_params.devices = devices.data();
        model_params.n_gpu_layers = accelerator ? 999 : 0;
        session->model = llama_model_load_from_file(model_file.c_str(), model_params);
        if (!session->model) throw std::runtime_error("llama.cpp could not load this model.");
        if (accelerator) session->device = ggml_backend_dev_description(accelerator);

        llama_context_params context_params = llama_context_default_params();
        context_params.n_ctx = n_ctx;
        context_params.n_threads = threads;
        context_params.n_threads_batch = threads;
        // A picture is decoded in one piece, which must fit one physical batch.
        context_params.n_ubatch = projector_file.empty() ? context_params.n_ubatch : 1024;
        context_params.offload_kqv = accelerator != nullptr;
        context_params.op_offload = accelerator != nullptr;
        context_params.no_perf = true;
        if (quantised) {
            // The pool's cache default: 8 bit keys and values, which need flash attention.
            llama_context_params small = context_params;
            small.type_k = GGML_TYPE_Q8_0;
            small.type_v = GGML_TYPE_Q8_0;
            small.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
            session->context = llama_init_from_model(session->model, small);
            if (session->context) session->kv = "q8_0";
        }
        if (!session->context) session->context = llama_init_from_model(session->model, context_params);
        if (!session->context) throw std::runtime_error("llama.cpp could not make a context of this size.");
        llama_set_abort_callback(session->context, [](void * data) { return static_cast<Session *>(data)->stop.load(); }, session.get());

        session->templates = common_chat_templates_init(session->model, "");
        if (!projector_file.empty()) {
            mtmd_context_params vision_params = mtmd_context_params_default();
            vision_params.use_gpu = accelerator != nullptr;
            vision_params.device = accelerator;
            vision_params.n_threads = threads;
            vision_params.print_timings = false;
            session->vision = mtmd_init_from_file(projector_file.c_str(), session->model, vision_params);
            if (!session->vision) throw std::runtime_error("llama.cpp could not load the picture encoder.");
        }
    } catch (const std::exception & failure) {
        raise(env, failure.what());
        return 0;
    }
    return reinterpret_cast<jlong>(session.release());
}

// "device\tcontext\tkv"
JNIEXPORT jstring JNICALL Java_app_poolhouse_llm_android_LlamaNative_describe(JNIEnv * env, jobject, jlong handle) {
    auto * session = reinterpret_cast<Session *>(handle);
    const std::string out = session->device + "\t" + std::to_string(llama_n_ctx(session->context)) + "\t" + session->kv;
    return env->NewStringUTF(out.c_str());
}

JNIEXPORT void JNICALL Java_app_poolhouse_llm_android_LlamaNative_stop(JNIEnv *, jobject, jlong handle, jboolean stop) {
    reinterpret_cast<Session *>(handle)->stop = stop;
}

JNIEXPORT void JNICALL Java_app_poolhouse_llm_android_LlamaNative_free(JNIEnv *, jobject, jlong handle) {
    delete reinterpret_cast<Session *>(handle);
}

// Returns {finish, prompt tokens, output tokens, first token us, prompt us, generation us, context used, reused tokens}.
JNIEXPORT jlongArray JNICALL Java_app_poolhouse_llm_android_LlamaNative_generate(
        JNIEnv * env, jobject, jlong handle, jbyteArray messages_json, jobjectArray pictures,
        jint max_tokens, jfloat temperature, jint top_k, jfloat top_p, jint seed, jboolean keep, jobject sink) {
    auto * session = reinterpret_cast<Session *>(handle);
    const auto started = std::chrono::steady_clock::now();
    jmethodID on_piece = env->GetMethodID(env->GetObjectClass(sink), "piece", "([B)V");
    llama_memory_t memory = llama_get_memory(session->context);
    const llama_vocab * vocab = llama_model_get_vocab(session->model);
    jlong values[8] = {FINISH_STOP, 0, 0, 0, 0, 0, 0, 0};
    common_sampler * sampler = nullptr;
    std::vector<mtmd_bitmap *> bitmaps;
    mtmd_input_chunks * chunks = nullptr;
    auto release = [&]() {
        if (sampler) common_sampler_free(sampler);
        if (chunks) mtmd_input_chunks_free(chunks);
        for (auto * bitmap : bitmaps) mtmd_bitmap_free(bitmap);
        if (!keep) {
            llama_memory_clear(memory, true);
            session->cached.assign(session->cached.size(), 0);
            session->cached.clear();
        }
    };
    try {
        common_chat_templates_inputs inputs;
        inputs.messages = common_chat_msgs_parse_oaicompat(common_json::parse(bytes(env, messages_json)));
        inputs.enable_thinking = false;
        common_chat_session chat(session->templates.get(), vocab, inputs);

        common_params_sampling sampling;
        sampling.seed = static_cast<uint32_t>(seed);
        sampling.temp = temperature;
        sampling.top_k = top_k;
        sampling.top_p = top_p;
        chat.apply_sampling(sampling);
        sampler = common_sampler_init(session->model, sampling);
        if (!sampler) throw std::runtime_error("llama.cpp could not set up the reply.");

        const int n_ctx = static_cast<int>(llama_n_ctx(session->context));
        const int n_batch = static_cast<int>(llama_n_batch(session->context));
        const auto prompt_started = std::chrono::steady_clock::now();
        int used = 0;
        const jsize picture_count = pictures ? env->GetArrayLength(pictures) : 0;
        if (picture_count > 0) {
            if (!session->vision) throw std::runtime_error("This model was loaded without picture input.");
            llama_memory_clear(memory, true);
            session->cached.clear();
            for (jsize i = 0; i < picture_count; i++) {
                auto picture = static_cast<jbyteArray>(env->GetObjectArrayElement(pictures, i));
                const std::string data = bytes(env, picture);
                env->DeleteLocalRef(picture);
                auto made = mtmd_helper_bitmap_init_from_buf(session->vision, reinterpret_cast<const unsigned char *>(data.data()),
                                                             data.size(), false, mtmd_helper_init_opt_default());
                if (!made.bitmap) throw std::runtime_error("A picture could not be read.");
                bitmaps.push_back(made.bitmap);
            }
            const std::string & prompt = chat.prompt();
            mtmd_input_text text{prompt.c_str(), prompt.size(), true, true};
            chunks = mtmd_input_chunks_init();
            std::vector<const mtmd_bitmap *> given(bitmaps.begin(), bitmaps.end());
            if (mtmd_tokenize(session->vision, chunks, &text, given.data(), given.size()) != 0) {
                throw std::runtime_error("The pictures could not be placed in the conversation.");
            }
            const int total = static_cast<int>(mtmd_helper_get_n_tokens(chunks));
            if (total >= n_ctx) {
                throw std::runtime_error("Input token ids are too long: " + std::to_string(total) + " >= " + std::to_string(n_ctx));
            }
            llama_pos past = 0;
            if (mtmd_helper_eval_chunks(session->vision, session->context, chunks, 0, 0, n_batch, true, &past) != 0) {
                if (!session->stop) throw std::runtime_error("llama.cpp could not read the conversation.");
            }
            used = static_cast<int>(past);
            values[1] = total;
        } else {
            std::vector<llama_token> prompt = common_tokenize(vocab, chat.prompt(), true, true);
            if (prompt.empty()) throw std::runtime_error("There is nothing to answer.");
            if (static_cast<int>(prompt.size()) >= n_ctx) {
                throw std::runtime_error("Input token ids are too long: " + std::to_string(prompt.size()) + " >= " + std::to_string(n_ctx));
            }
            size_t same = 0;
            while (same < session->cached.size() && same + 1 < prompt.size() && session->cached[same] == prompt[same]) same++;
            // A sliding window cache may not give back part of itself; then it is read again from the start.
            if (same == 0 || !llama_memory_seq_rm(memory, 0, static_cast<llama_pos>(same), -1)) {
                llama_memory_clear(memory, true);
                same = 0;
            }
            session->cached.resize(same);
            for (size_t at = same; at < prompt.size() && !session->stop; at += n_batch) {
                const int count = static_cast<int>(std::min<size_t>(n_batch, prompt.size() - at));
                const int code = llama_decode(session->context, llama_batch_get_one(prompt.data() + at, count));
                if (code != 0) {
                    llama_memory_clear(memory, true);
                    session->cached.clear();
                    if (session->stop) break;
                    throw std::runtime_error("llama.cpp could not read the conversation (code " + std::to_string(code) + ").");
                }
                session->cached.insert(session->cached.end(), prompt.begin() + at, prompt.begin() + at + count);
            }
            used = static_cast<int>(prompt.size());
            values[1] = static_cast<jlong>(prompt.size());
            values[7] = static_cast<jlong>(same);
        }
        values[4] = micros_since(prompt_started);

        std::string sent;
        auto send = [&](const std::string & content) {
            const size_t end = whole(content);
            if (end <= sent.size()) return;
            jbyteArray piece = array_of(env, content.substr(sent.size(), end - sent.size()));
            sent = content.substr(0, end);
            env->CallVoidMethod(sink, on_piece, piece);
            env->DeleteLocalRef(piece);
        };
        const int limit = max_tokens > 0 ? max_tokens : n_ctx;
        const auto generation_started = std::chrono::steady_clock::now();
        std::string raw;
        int made = 0;
        values[0] = FINISH_LENGTH;
        while (true) {
            if (session->stop) {
                values[0] = FINISH_CANCELLED;
                break;
            }
            llama_token token = common_sampler_sample(sampler, session->context, -1);
            common_sampler_accept(sampler, token, true);
            if (made == 0) values[3] = micros_since(started);
            if (llama_vocab_is_eog(vocab, token)) {
                values[0] = FINISH_STOP;
                break;
            }
            made++;
            const std::string text = common_token_to_piece(vocab, token, true);
            raw += text;
            common_chat_input chunk;
            chunk.append(text, token);
            send(chat.feed(chunk).content);
            if (env->ExceptionCheck()) break;
            bool stopped = false;
            for (const auto & stop : chat.additional_stops()) {
                if (!stop.empty() && raw.size() >= stop.size() && raw.compare(raw.size() - stop.size(), stop.size(), stop) == 0) stopped = true;
            }
            if (stopped) {
                values[0] = FINISH_STOP;
                break;
            }
            if (made >= limit || used + 1 >= n_ctx) break;
            if (llama_decode(session->context, llama_batch_get_one(&token, 1)) != 0) {
                if (session->stop) {
                    values[0] = FINISH_CANCELLED;
                    break;
                }
                throw std::runtime_error("llama.cpp stopped during the reply.");
            }
            used++;
            if (picture_count == 0) session->cached.push_back(token);
        }
        values[5] = micros_since(generation_started);
        values[2] = made;
        values[6] = used;
        if (!env->ExceptionCheck() && values[0] != FINISH_CANCELLED) send(chat.finish().content);
    } catch (const std::exception & failure) {
        release();
        raise(env, failure.what());
        return nullptr;
    }
    release();
    jlongArray out = env->NewLongArray(8);
    env->SetLongArrayRegion(out, 0, 8, values);
    return out;
}

}  // extern "C"
