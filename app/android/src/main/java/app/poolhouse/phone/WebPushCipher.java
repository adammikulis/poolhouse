package app.poolhouse.phone;

import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.AlgorithmParameters;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.ECParameterSpec;
import java.security.spec.ECPoint;
import java.security.spec.ECPrivateKeySpec;
import java.security.spec.ECPublicKeySpec;
import java.util.Arrays;
import javax.crypto.Cipher;
import javax.crypto.KeyAgreement;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * Web Push payload encryption (RFC 8291, with the record format of RFC 8188): what the node seals a notification with and
 * this phone opens. The push service sees the address, the length and the time; the phone's push key opens the rest.
 */
final class WebPushCipher {
    private static final int RECORD = 4096;
    private WebPushCipher() { }

    static ECParameterSpec curve() throws Exception {
        AlgorithmParameters parameters = AlgorithmParameters.getInstance("EC");
        parameters.init(new ECGenParameterSpec("secp256r1"));
        return parameters.getParameterSpec(ECParameterSpec.class);
    }

    /** The uncompressed point (65 bytes) of a P-256 public key. */
    static byte[] point(PublicKey key) throws Exception {
        ECPoint w = ((java.security.interfaces.ECPublicKey) key).getW();
        byte[] out = new byte[65];
        out[0] = 4;
        copy(w.getAffineX(), out, 1);
        copy(w.getAffineY(), out, 33);
        return out;
    }

    private static void copy(BigInteger value, byte[] out, int at) {
        byte[] raw = value.toByteArray();
        int from = Math.max(0, raw.length - 32);
        System.arraycopy(raw, from, out, at + 32 - (raw.length - from), raw.length - from);
    }

    static PublicKey publicKey(byte[] point) throws Exception {
        if (point.length != 65 || point[0] != 4) throw new IllegalArgumentException("A P-256 key is an uncompressed point of 65 bytes.");
        ECPoint w = new ECPoint(new BigInteger(1, Arrays.copyOfRange(point, 1, 33)), new BigInteger(1, Arrays.copyOfRange(point, 33, 65)));
        return KeyFactory.getInstance("EC").generatePublic(new ECPublicKeySpec(w, curve()));
    }

    static PrivateKey privateKey(byte[] scalar) throws Exception {
        return KeyFactory.getInstance("EC").generatePrivate(new ECPrivateKeySpec(new BigInteger(1, scalar), curve()));
    }

    private static byte[] hmac(byte[] key, byte[] data) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key.length == 0 ? new byte[32] : key, "HmacSHA256"));
        return mac.doFinal(data);
    }

    private static byte[] expand(byte[] prk, byte[] info, int length) throws Exception {
        byte[] input = Arrays.copyOf(info, info.length + 1);
        input[info.length] = 1;
        return Arrays.copyOf(hmac(prk, input), length);
    }

    private static byte[] join(byte[]... parts) {
        int size = 0;
        for (byte[] part : parts) size += part.length;
        ByteBuffer out = ByteBuffer.allocate(size);
        for (byte[] part : parts) out.put(part);
        return out.array();
    }

    private static byte[][] keys(byte[] ecdh, byte[] senderPoint, byte[] receiverPoint, byte[] auth, byte[] salt) throws Exception {
        byte[] early = hmac(auth, ecdh);
        byte[] ikm = expand(early, join("WebPush: info\0".getBytes(StandardCharsets.US_ASCII), receiverPoint, senderPoint), 32);
        byte[] prk = hmac(salt, ikm);
        return new byte[][] {expand(prk, "Content-Encoding: aes128gcm\0".getBytes(StandardCharsets.US_ASCII), 16),
                expand(prk, "Content-Encoding: nonce\0".getBytes(StandardCharsets.US_ASCII), 12)};
    }

    /** The plaintext of a push body for the phone whose push key is {@code receiver} (point {@code receiverPoint}) and secret {@code auth}. */
    static byte[] decrypt(byte[] body, PrivateKey receiver, byte[] receiverPoint, byte[] auth) throws Exception {
        if (body.length < 21 + 65 + 17 || body[20] != 65) throw new IllegalArgumentException("That is not a push for this phone.");
        byte[] salt = Arrays.copyOfRange(body, 0, 16);
        byte[] sender = Arrays.copyOfRange(body, 21, 86);
        KeyAgreement agreement = KeyAgreement.getInstance("ECDH");
        agreement.init(receiver);
        agreement.doPhase(publicKey(sender), true);
        byte[][] derived = keys(agreement.generateSecret(), sender, receiverPoint, auth, salt);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(derived[0], "AES"), new GCMParameterSpec(128, derived[1]));
        byte[] record;
        try {
            record = cipher.doFinal(body, 86, body.length - 86);
        } catch (java.security.GeneralSecurityException notForThisPhone) {
            throw new IllegalArgumentException("That is not a push for this phone.");
        }
        int end = record.length - 1;
        while (end >= 0 && record[end] == 0) end--;
        if (end < 0 || record[end] != 2) throw new IllegalArgumentException("That is not a push for this phone.");
        return Arrays.copyOf(record, end);
    }

    /** A push body for tests and for a stand-in push service: the plaintext sealed to {@code receiverPoint} and {@code auth}. */
    static byte[] encrypt(byte[] plaintext, byte[] receiverPoint, byte[] auth, SecureRandom random) throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("EC");
        generator.initialize(new ECGenParameterSpec("secp256r1"), random);
        KeyPair sender = generator.generateKeyPair();
        byte[] senderPoint = point(sender.getPublic());
        byte[] salt = new byte[16];
        random.nextBytes(salt);
        KeyAgreement agreement = KeyAgreement.getInstance("ECDH");
        agreement.init(sender.getPrivate());
        agreement.doPhase(publicKey(receiverPoint), true);
        return seal(plaintext, agreement.generateSecret(), senderPoint, receiverPoint, auth, salt);
    }

    /** The body for a given agreed secret and salt: the deterministic core, which the RFC's own example checks. */
    static byte[] seal(byte[] plaintext, byte[] ecdh, byte[] senderPoint, byte[] receiverPoint, byte[] auth, byte[] salt) throws Exception {
        byte[][] derived = keys(ecdh, senderPoint, receiverPoint, auth, salt);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(derived[0], "AES"), new GCMParameterSpec(128, derived[1]));
        byte[] record = cipher.doFinal(join(plaintext, new byte[] {2}));
        return join(salt, ByteBuffer.allocate(4).putInt(RECORD).array(), new byte[] {65}, senderPoint, record);
    }
}
