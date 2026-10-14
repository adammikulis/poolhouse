package app.poolhouse.phone;

import java.io.ByteArrayOutputStream;
import java.math.BigInteger;
import java.util.Arrays;

/** Strict DER P-256 signatures with s in the lower half of the group. */
final class P256Signature {
    private static final BigInteger ORDER = new BigInteger("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16);
    private P256Signature() { }

    static byte[] normalize(byte[] signature) {
        byte[] der = signature.clone();
        if (der.length < 8 || der.length > 72 || der[0] != 0x30 || (der[1] & 255) != der.length - 2) throw bad();
        int[] offset = {2};
        BigInteger r = scalar(der, offset);
        BigInteger s = scalar(der, offset);
        if (offset[0] != der.length) throw bad();
        if (s.compareTo(ORDER.shiftRight(1)) > 0) s = ORDER.subtract(s);
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        integer(body, r);
        integer(body, s);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(0x30);
        out.write(body.size());
        byte[] bytes = body.toByteArray();
        out.write(bytes, 0, bytes.length);
        return out.toByteArray();
    }

    private static BigInteger scalar(byte[] der, int[] offset) {
        int at = offset[0];
        if (at + 2 > der.length || der[at] != 2) throw bad();
        int size = der[at + 1] & 255;
        int start = at + 2;
        if (size < 1 || size > 33 || start + size > der.length || (der[start] & 128) != 0) throw bad();
        if (size > 1 && der[start] == 0 && (der[start + 1] & 128) == 0) throw bad();
        BigInteger value = new BigInteger(1, Arrays.copyOfRange(der, start, start + size));
        if (value.signum() == 0 || value.compareTo(ORDER) >= 0) throw bad();
        offset[0] = start + size;
        return value;
    }

    private static void integer(ByteArrayOutputStream out, BigInteger value) {
        byte[] bytes = value.toByteArray();
        out.write(2);
        out.write(bytes.length);
        out.write(bytes, 0, bytes.length);
    }

    private static IllegalArgumentException bad() {
        return new IllegalArgumentException("That is not a P-256 signature.");
    }
}
