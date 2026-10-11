package app.poolhouse.phone;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * What this phone keeps about the device it was added to: the certificate to trust, the addresses, its token, its push key and
 * the address of its push distributor. One file, sealed by an Android Keystore key that asks the person for nothing (so the
 * app can read approvals and open a notification while the screen is locked), and never part of a backup. The approval key is
 * elsewhere ({@link ApprovalKey}) and needs the person's fingerprint every time it signs.
 */
final class PhoneStore implements PushHandler.Keys {
    private static final String ALIAS = "poolhouse-phone-store";
    private static final byte[] AAD = "poolhouse/android/phone-store".getBytes(StandardCharsets.UTF_8);
    private static PhoneStore single;
    private final AtomicFile file;
    private JSONObject data;

    private PhoneStore(Context context) {
        this.file = new AtomicFile(new File(context.getApplicationContext().getNoBackupFilesDir(), "phone-store"));
    }

    static synchronized PhoneStore of(Context context) {
        if (single == null) single = new PhoneStore(context);
        return single;
    }

    private KeyStore keys() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        return store;
    }

    private SecretKey wrapKey() throws Exception {
        if (!keys().containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build());
            generator.generateKey();
        }
        return (SecretKey) keys().getKey(ALIAS, null);
    }

    synchronized boolean added() {
        try {
            return load().has("token");
        } catch (Exception unreadable) {
            return false;
        }
    }

    private JSONObject load() throws Exception {
        if (data != null) return data;
        if (!file.getBaseFile().exists()) return data = new JSONObject();
        JSONObject sealed = new JSONObject(new String(file.readFully(), StandardCharsets.UTF_8));
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, wrapKey(), new GCMParameterSpec(128, Base64.getDecoder().decode(sealed.getString("iv"))));
        cipher.updateAAD(AAD);
        return data = new JSONObject(new String(cipher.doFinal(Base64.getDecoder().decode(sealed.getString("ciphertext"))), StandardCharsets.UTF_8));
    }

    private void write() throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, wrapKey());
        cipher.updateAAD(AAD);
        JSONObject sealed = new JSONObject().put("iv", Base64.getEncoder().encodeToString(cipher.getIV()))
                .put("ciphertext", Base64.getEncoder().encodeToString(cipher.doFinal(data.toString().getBytes(StandardCharsets.UTF_8))));
        FileOutputStream stream = file.startWrite();
        try {
            stream.write(sealed.toString().getBytes(StandardCharsets.UTF_8));
            file.finishWrite(stream);
        } catch (Exception failure) {
            file.failWrite(stream);
            throw failure;
        }
    }

    /** A push key for this phone: made here, kept sealed, opened by nothing but this app. */
    static KeyPair newPushKey() throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("EC");
        generator.initialize(new ECGenParameterSpec("secp256r1"));
        return generator.generateKeyPair();
    }

    /** Keep what the device gave when it added this phone. */
    synchronized void keepAdded(AddLink link, String device, String deviceName, String phoneId, String name, String token, KeyPair push, byte[] auth) throws Exception {
        JSONArray addresses = new JSONArray();
        for (String endpoint : link.endpoints) addresses.put(endpoint);
        byte[] instance = new byte[16];
        new SecureRandom().nextBytes(instance);
        data = new JSONObject().put("pin", link.pin).put("endpoints", addresses).put("device", device).put("device_name", deviceName).put("phone", phoneId).put("name", name)
                .put("token", token).put("push_private", Hex.of(push.getPrivate().getEncoded())).put("push_point", Hex.of(WebPushCipher.point(push.getPublic())))
                .put("push_auth", Hex.of(auth)).put("push_instance", Hex.of(instance)).put("clock_offset_ms", 0L).put("state", "active");
        write();
    }

    synchronized void set(String key, Object value) throws Exception {
        load().put(key, value);
        write();
    }

    synchronized void forget() throws Exception {
        data = new JSONObject();
        file.delete();
        if (keys().containsAlias(ALIAS)) keys().deleteEntry(ALIAS);
        ApprovalKey.forget();
    }

    synchronized String text(String key) {
        try {
            return load().optString(key, "");
        } catch (Exception unreadable) {
            return "";
        }
    }

    String token() {
        return text("token");
    }

    String device() {
        return text("device");
    }

    String deviceName() {
        return text("device_name");
    }

    String phoneId() {
        return text("phone");
    }

    /** The token that a push distributor's broadcasts must carry, so another app cannot give this phone an address. */
    String instance() {
        return text("push_instance");
    }

    boolean waitingForApproval() {
        return "pending".equals(text("state"));
    }

    synchronized List<String> endpoints() {
        List<String> out = new ArrayList<>();
        try {
            JSONArray listed = load().getJSONArray("endpoints");
            for (int i = 0; i < listed.length(); i++) out.add(listed.getString(i));
        } catch (Exception unreadable) {
            return out;
        }
        return out;
    }

    NodeApi api() {
        return new NodeApi(Transport.pinned(), text("pin"), endpoints());
    }

    /** The node's clock now, from the offset learnt from its last answer. */
    synchronized long nodeNowMs() {
        try {
            return System.currentTimeMillis() + load().optLong("clock_offset_ms", 0);
        } catch (Exception unreadable) {
            return System.currentTimeMillis();
        }
    }

    synchronized void learnClock(long nodeMs) {
        try {
            set("clock_offset_ms", nodeMs - System.currentTimeMillis());
        } catch (Exception unwritten) {
            return;
        }
    }

    @Override public PrivateKey privateKey() throws Exception {
        return KeyFactory.getInstance("EC").generatePrivate(new PKCS8EncodedKeySpec(Hex.parse(text("push_private"))));
    }

    @Override public byte[] point() {
        return Hex.parse(text("push_point"));
    }

    @Override public byte[] auth() {
        return Hex.parse(text("push_auth"));
    }
}
