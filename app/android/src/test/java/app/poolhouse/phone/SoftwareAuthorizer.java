package app.poolhouse.phone;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;

/** A person's approval key held in memory for the checks: it signs at once and counts the times it was asked. */
final class SoftwareAuthorizer implements Authorizer {
    final KeyPair signing;
    int asked;
    String refuses;
    /** Whether a strong fingerprint is set up, and whether the last signature asked for one. */
    boolean strongSetUp = true;
    boolean lastStrong;

    SoftwareAuthorizer() throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("EC");
        generator.initialize(new ECGenParameterSpec("secp256r1"));
        signing = generator.generateKeyPair();
    }

    @Override public byte[] publicPoint() throws Exception {
        return WebPushCipher.point(signing.getPublic());
    }

    @Override public boolean strongAvailable() {
        return strongSetUp;
    }

    @Override public void sign(byte[] message, String title, boolean strong, Result result) {
        asked++;
        lastStrong = strong;
        if (refuses != null) {
            result.failed(refuses);
            return;
        }
        try {
            Signature signature = Signature.getInstance("SHA256withECDSA");
            signature.initSign(signing.getPrivate());
            signature.update(message);
            result.signed(signature.sign());
        } catch (Exception failure) {
            result.failed("The key could not sign.");
        }
    }

    boolean verifies(byte[] message, byte[] der) throws Exception {
        Signature signature = Signature.getInstance("SHA256withECDSA");
        signature.initVerify(signing.getPublic());
        signature.update(message);
        return signature.verify(der);
    }
}
