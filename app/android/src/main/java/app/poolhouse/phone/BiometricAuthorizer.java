package app.poolhouse.phone;

import android.app.Activity;
import android.hardware.biometrics.BiometricManager;
import android.hardware.biometrics.BiometricPrompt;
import android.os.CancellationSignal;
import android.security.keystore.KeyPermanentlyInvalidatedException;
import java.security.Signature;

/** The approval key behind the person's fingerprint, face or device credential: one prompt, and the prompt is what unlocks the signature. */
final class BiometricAuthorizer implements Authorizer {
    private final Activity activity;
    private static final java.util.concurrent.Executor EXECUTOR = java.util.concurrent.Executors.newSingleThreadExecutor();
    private volatile boolean cancelled;
    private volatile CancellationSignal cancellation;

    BiometricAuthorizer(Activity activity) {
        this.activity = activity;
    }

    @Override public byte[] publicPoint() throws Exception {
        return ApprovalKey.publicPoint();
    }

    @Override public boolean strongAvailable() {
        BiometricManager biometrics = activity.getSystemService(BiometricManager.class);
        return biometrics != null && biometrics.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS;
    }

    /** Asks on the screen's own thread: a second prompt for one answer is asked from the first prompt's callback. */
    @Override public void sign(byte[] message, String title, boolean strong, Result result) {
        activity.runOnUiThread(() -> { if (!cancelled) prompt(message, title, strong, result); });
    }

    void cancel() {
        cancelled = true;
        CancellationSignal current = cancellation;
        if (current != null) current.cancel();
    }

    private void prompt(byte[] message, String title, boolean strong, Result result) {
        final Signature signature;
        try {
            signature = ApprovalKey.signature();
        } catch (KeyPermanentlyInvalidatedException lost) {
            result.failed("A new fingerprint or face was added to this phone, so its approval key is gone. Add this phone again.");
            return;
        } catch (Exception failure) {
            result.failed("This phone's approval key could not be used. Add this phone again.");
            return;
        }
        BiometricPrompt prompt = new BiometricPrompt.Builder(activity).setTitle(title).setSubtitle(strong ? "Use your fingerprint or strong face unlock" : "Use your fingerprint, strong face unlock or your device PIN")
                .setAllowedAuthenticators(strong ? BiometricManager.Authenticators.BIOMETRIC_STRONG : ApprovalKey.AUTHENTICATORS).setConfirmationRequired(false).build();
        CancellationSignal signal = new CancellationSignal();
        cancellation = signal;
        if (cancelled) { signal.cancel(); return; }
        prompt.authenticate(new BiometricPrompt.CryptoObject(signature), signal, EXECUTOR, new BiometricPrompt.AuthenticationCallback() {
            @Override public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult unlocked) {
                try {
                    if (cancelled) return;
                    Signature ready = unlocked.getCryptoObject().getSignature();
                    ready.update(message);
                    byte[] signed = ready.sign();
                    if (!cancelled) result.signed(signed);
                } catch (Exception failure) {
                    result.failed("The approval could not be signed, so nothing was changed.");
                }
            }

            @Override public void onAuthenticationError(int error, CharSequence shown) {
                result.failed("Nothing was changed: the approval was not confirmed.");
            }
        });
    }
}
