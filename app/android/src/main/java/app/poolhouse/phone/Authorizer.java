package app.poolhouse.phone;

/**
 * The person's approval key: asks the person (a fingerprint or the device credential) and signs. The phone's own key does this
 * behind a prompt; the checks use a key in memory that signs at once.
 */
interface Authorizer {
    /** The key's public half as an uncompressed P-256 point (65 bytes). */
    byte[] publicPoint() throws Exception;

    /** Ask the person, then sign {@code message}; exactly one of the two callbacks is called, once. */
    void sign(byte[] message, String title, Result result);

    interface Result {
        /** The DER signature (ECDSA, P-256, SHA-256). */
        void signed(byte[] der);

        /** One sentence for the person: why nothing was signed. */
        void failed(String sentence);
    }
}
