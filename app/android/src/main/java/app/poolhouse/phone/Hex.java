package app.poolhouse.phone;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;

/** Lower-case hex and the SHA-256 of bytes: how the node writes keys, fingerprints and signatures. */
final class Hex {
    private Hex() { }

    static String of(byte[] bytes) {
        StringBuilder text = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) text.append(String.format(Locale.ROOT, "%02x", value & 255));
        return text.toString();
    }

    static byte[] parse(String text) {
        if (text == null || text.length() % 2 != 0 || !text.matches("[0-9a-fA-F]*")) throw new IllegalArgumentException("That is not hex.");
        byte[] out = new byte[text.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(text.substring(2 * i, 2 * i + 2), 16);
        return out;
    }

    static String sha256(byte[] bytes) {
        try {
            return of(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    /** The first 24 characters of a fingerprint in six groups of four, as the node shows them for two screens to be compared. */
    static String words(String fingerprint) {
        if (fingerprint.length() < 24) return "";
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < 24; i += 4) out.append(out.length() == 0 ? "" : " ").append(fingerprint, i, i + 4);
        return out.toString();
    }

    static String sha256(String text) {
        return sha256(text.getBytes(StandardCharsets.UTF_8));
    }
}
