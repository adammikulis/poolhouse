package app.poolhouse.phone;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/** Pending node challenge data, not permission to enroll or replace a key. */
final class PoolKeyEnrollment {
    private final byte[] challenge;
    final String node, device, approvalKey, request;
    final long expires;

    private PoolKeyEnrollment(String node, String device, String approvalKey, String request, String nonce, long expires) throws Exception {
        this.node = node;
        this.device = device;
        this.approvalKey = approvalKey;
        this.request = request;
        this.expires = expires;
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        buffer.write("poolhouse.poolkey-enrollment.challenge.v1\0".getBytes(StandardCharsets.US_ASCII));
        DataOutputStream bytes = new DataOutputStream(buffer);
        for (String field : new String[]{node, device, approvalKey, request, nonce}) bytes.write(Hex.parse(field));
        bytes.writeLong(expires);
        challenge = MessageDigest.getInstance("SHA-256").digest(buffer.toByteArray());
    }

    /** Expected identities come from this app's existing linked-node and ApprovalKey state. */
    static PoolKeyEnrollment checked(String node, String device, String approvalKey, String request, String nonce,
                                     long expires, long now, String pinnedNode, String linkedDevice, String localApprovalKey) throws Exception {
        for (String value : new String[]{node, device, approvalKey, request, nonce}) {
            if (value == null || !value.matches("[0-9a-f]{64}") || value.equals("0".repeat(64))) throw bad();
        }
        if (!node.equals(pinnedNode) || !device.equals(linkedDevice) || !approvalKey.equals(localApprovalKey)
                || now < 0 || expires <= now || expires - now > 300) throw bad();
        return new PoolKeyEnrollment(node, device, approvalKey, request, nonce, expires);
    }

    byte[] challenge() { return challenge.clone(); }
    String binding() { return Hex.of(challenge); }

    void requireCurrent(long now) {
        if (now < 0 || now >= expires || expires - now > 300) throw bad();
    }

    /** The caller reconstructs these fields from its own alias and certificate chain. */
    byte[] consent(byte[] localPoint, String chainDigest) throws Exception {
        if (localPoint == null || localPoint.length != 65 || localPoint[0] != 4
                || chainDigest == null || !chainDigest.matches("[0-9a-f]{64}") || chainDigest.equals("0".repeat(64))) throw bad();
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        buffer.write("poolhouse.poolkey-enrollment.consent.v1\0".getBytes(StandardCharsets.US_ASCII));
        buffer.write(challenge);
        buffer.write(localPoint);
        buffer.write(Hex.parse(chainDigest));
        return buffer.toByteArray();
    }

    private static SecurityException bad() { return new SecurityException("This pool key enrollment no longer matches this phone."); }
}
