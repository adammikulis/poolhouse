package app.poolhouse.phone;

/** How a request reaches the device: pinned TLS in the app, something in memory in the checks. */
interface Transport {
    /** The body of the answer to {@code method path} at {@code endpoint}, whose certificate must have the SHA-256 {@code pin}. */
    byte[] request(String endpoint, String pin, String method, String path, String token, byte[] body) throws Exception;

    /** The transport the app uses: TLS 1.3 to the one certificate the code named, and only to a device on the local network. */
    static Transport pinned() {
        return (endpoint, pin, method, path, token, body) -> {
            try (PinnedHttps client = new PinnedHttps()) {
                return client.request(endpoint, pin, method, path, token, body);
            }
        };
    }
}
