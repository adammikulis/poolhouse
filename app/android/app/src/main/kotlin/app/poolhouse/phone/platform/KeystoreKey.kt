package app.poolhouse.phone.platform

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import app.poolhouse.core.net.PhoneKey
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.PublicKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

private const val STORE = "AndroidKeyStore"
private const val ALIAS = "poolhouse.link"

/**
 * The phone's key (docs/phone-api.md section 2): ECDSA P-256 in Android Keystore, in StrongBox where the phone has it, never
 * exportable, and usable for each request without asking the person.
 */
class KeystoreKey private constructor(private val privateKey: PrivateKey, override val publicKey: PublicKey) : PhoneKey {
    override fun sign(data: ByteArray): ByteArray = Signature.getInstance("SHA256withECDSA").run {
        initSign(privateKey)
        update(data)
        sign()
    }

    companion object {
        private fun store() = KeyStore.getInstance(STORE).apply { load(null) }

        /** The key of the present link, or null when there is none. */
        fun existing(): KeystoreKey? {
            val store = store()
            val key = store.getKey(ALIAS, null) as? PrivateKey ?: return null
            return KeystoreKey(key, store.getCertificate(ALIAS).publicKey)
        }

        /** A new key, replacing any before: each link has its own. */
        fun create(): KeystoreKey {
            val made = try {
                generate(strongBox = true)
            } catch (unavailable: StrongBoxUnavailableException) {
                generate(strongBox = false)
            }
            return KeystoreKey(made.private, made.public)
        }

        private fun generate(strongBox: Boolean) = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, STORE).run {
            initialize(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .setIsStrongBoxBacked(strongBox)
                    .build(),
            )
            generateKeyPair()
        }

        fun delete() {
            store().deleteEntry(ALIAS)
        }
    }
}
