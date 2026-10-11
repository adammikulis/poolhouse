package app.poolhouse.phone;

import android.app.KeyguardManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.hardware.biometrics.BiometricManager;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.security.keystore.StrongBoxUnavailableException;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;

/**
 * The key that signs approvals: ECDSA P-256 in Android Keystore, in the secure hardware where there is some and in StrongBox where
 * the phone has it, signing only after the person's fingerprint, strong face unlock or device credential, every time, and lost
 * when a new fingerprint is enrolled. Its private half never leaves the hardware.
 */
final class ApprovalKey {
    static final String ALIAS = "poolhouse-phone-approval";
    static final int AUTHENTICATORS = BiometricManager.Authenticators.BIOMETRIC_STRONG | BiometricManager.Authenticators.DEVICE_CREDENTIAL;
    private ApprovalKey() { }

    private static KeyStore store() throws Exception {
        KeyStore keys = KeyStore.getInstance("AndroidKeyStore");
        keys.load(null);
        return keys;
    }

    static boolean exists() {
        try {
            return store().containsAlias(ALIAS);
        } catch (Exception unreadable) {
            return false;
        }
    }

    /** Whether this phone has a screen lock and a way to ask the person: without them a key that needs the person cannot be made. */
    static boolean ready(Context context) {
        KeyguardManager lock = context.getSystemService(KeyguardManager.class);
        BiometricManager biometrics = context.getSystemService(BiometricManager.class);
        return lock != null && lock.isDeviceSecure() && biometrics != null && biometrics.canAuthenticate(AUTHENTICATORS) == BiometricManager.BIOMETRIC_SUCCESS;
    }

    private static KeyGenParameterSpec.Builder spec() {
        return new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN).setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256).setUserAuthenticationRequired(true)
                .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG | KeyProperties.AUTH_DEVICE_CREDENTIAL).setInvalidatedByBiometricEnrollment(true);
    }

    /** Make the key: in StrongBox when the phone has it, else in the trusted environment. */
    static void create(Context context) throws Exception {
        if (!ready(context)) throw new IllegalStateException("Set a screen lock and a fingerprint or face unlock first: the key that approves for you needs them.");
        forget();
        KeyPairGenerator generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
        if (context.getPackageManager().hasSystemFeature(PackageManager.FEATURE_STRONGBOX_KEYSTORE)) {
            try {
                generator.initialize(spec().setIsStrongBoxBacked(true).build());
                generator.generateKeyPair();
                return;
            } catch (StrongBoxUnavailableException notThere) {
                forget();
            }
        }
        generator.initialize(spec().build());
        generator.generateKeyPair();
    }

    /** The key's public half as an uncompressed point. */
    static byte[] publicPoint() throws Exception {
        return WebPushCipher.point(store().getCertificate(ALIAS).getPublicKey());
    }

    /** A signature ready to be unlocked by the person's prompt: the prompt takes it as its crypto object. */
    static Signature signature() throws Exception {
        Signature signature = Signature.getInstance("SHA256withECDSA");
        signature.initSign((PrivateKey) store().getKey(ALIAS, null));
        return signature;
    }

    static void forget() {
        try {
            if (store().containsAlias(ALIAS)) store().deleteEntry(ALIAS);
        } catch (Exception gone) {
            return;
        }
    }
}
