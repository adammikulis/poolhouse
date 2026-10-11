package app.poolhouse.phone;

/** The node refused a request. {@code getMessage()} is one sentence a person can read. */
final class NodeError extends Exception {
    final String code;

    NodeError(String code, String sentence) {
        super(sentence);
        this.code = code;
    }
}
