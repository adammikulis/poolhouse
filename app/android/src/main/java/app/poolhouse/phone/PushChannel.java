package app.poolhouse.phone;

/**
 * How a wake-up reaches the phone while the app is closed: a push distributor gives the app an address, and what is posted to
 * that address arrives as bytes. The first implementation is UnifiedPush ({@link UnifiedPushChannel}); the checks use one in memory.
 * The bytes are always the node's sealed notification, so a channel carries ciphertext and nothing it can read.
 */
interface PushChannel {
    /** Ask the distributor for an address; it is given to the listener when it arrives. A phone with no distributor just never gets one. */
    void register();

    /** Give the address back. */
    void unregister();

    interface Listener {
        /** The address the node should post to. */
        void address(String endpoint);

        /** One sealed notification, as posted. */
        void message(byte[] body);

        /** The distributor refused or went away. */
        void failed(String sentence);
    }
}
