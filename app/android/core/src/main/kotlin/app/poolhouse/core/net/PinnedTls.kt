package app.poolhouse.core.net

import app.poolhouse.core.link.hex
import app.poolhouse.core.link.sha256
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/** The SHA-256 of a certificate's DER bytes as 64 lowercase hex digits: the only thing that says which computer answered. */
fun pinOf(certificate: java.security.cert.Certificate): String = hex(sha256(certificate.encoded))

/**
 * TLS 1.3 that trusts exactly one certificate, the one whose SHA-256 is `pin`; names, dates and authorities say nothing here.
 * With no pin yet (a typed code), the certificate seen is accepted and remembered in [seen] so the caller can pin it, and nothing
 * is kept until the computer has proved it knows the code.
 */
class PinnedTls(private val pin: String?) {
    @Volatile
    var seen: String? = null
        private set

    @Suppress("CustomX509TrustManager")
    private val trust = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
            throw CertificateException("The app accepts no client certificate.")

        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            val leaf = chain?.firstOrNull() ?: throw CertificateException("The computer sent no certificate.")
            val found = pinOf(leaf)
            if (pin != null && found != pin) throw PinMismatch()
            seen = found
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private val factory = SSLContext.getInstance("TLSv1.3").apply { init(null, arrayOf(trust), null) }.socketFactory

    private val names = HostnameVerifier { _, session ->
        val leaf = session.peerCertificates.firstOrNull()
        leaf != null && pinOf(leaf) == (pin ?: seen)
    }

    fun apply(connection: HttpsURLConnection) {
        connection.sslSocketFactory = factory
        connection.hostnameVerifier = names
    }
}

/** The certificate is not the linked computer's. */
class PinMismatch : CertificateException("This is not the linked computer: its certificate is different.")
