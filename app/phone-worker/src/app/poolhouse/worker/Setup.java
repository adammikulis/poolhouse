package app.poolhouse.worker;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * Makes the guest once: the pinned Debian layer is downloaded, checked against its digest, unpacked and given the
 * tools the tests need. Each finished step leaves a mark, so an interrupted setup goes on where it stopped.
 */
final class Setup {
    /** ``debian:trixie-slim`` for linux/arm64 (Python 3.13), by content digest. */
    static final String LAYER_DIGEST = "48fb00926fe468d83ca04ee6775f36350d17e0f328dfcb03aadb5de0c52ae3f6";
    private static final String TOKEN_URL = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/debian:pull";
    private static final String BLOB_URL = "https://registry-1.docker.io/v2/library/debian/blobs/sha256:";

    private final Guest guest;

    Setup(Guest guest) {
        this.guest = guest;
    }

    void ensure(Worker worker) throws IOException, InterruptedException {
        guest.unpack();
        File unpacked = new File(guest.files, "unpacked-" + LAYER_DIGEST);
        if (!unpacked.exists()) {
            worker.say("downloading the Linux files");
            File archive = new File(guest.files, "rootfs.tar.gz");
            download(archive);
            worker.say("unpacking the Linux files");
            worker.finish(guest.extract(archive), "extract");
            Files.delete(archive.toPath());
            Guest.write(unpacked, "");
        }
        guest.writeResolver();
        File ready = new File(guest.files, "ready");
        byte[] script = Files.readAllBytes(new File(guest.files, "guest/setup.sh").toPath());
        String wanted = digest(script);
        if (ready.exists() && wanted.equals(new String(Files.readAllBytes(ready.toPath()), StandardCharsets.UTF_8))) {
            return;
        }
        worker.say("installing Python, git and the compilers");
        worker.finish(guest.run(true, "/bin/sh", "/opt/worker/setup.sh"), "setup");
        Guest.write(ready, wanted);
    }

    private static HttpURLConnection open(String address, String bearer) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        if (!"https".equals(connection.getURL().getProtocol())) {
            throw new IOException("only https is followed");
        }
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(30_000);
        connection.setReadTimeout(60_000);
        if (bearer != null) {
            connection.setRequestProperty("Authorization", "Bearer " + bearer);
        }
        return connection;
    }

    private static void download(File target) throws IOException {
        String token;
        try (InputStream in = open(TOKEN_URL, null).getInputStream()) {
            ByteArrayOutputStream text = new ByteArrayOutputStream();
            copy(in, text, null, 1 << 20);
            token = new JSONObject(text.toString("UTF-8")).getString("token");
        } catch (org.json.JSONException broken) {
            throw new IOException("the registry sent no token", broken);
        }
        HttpURLConnection connection = open(BLOB_URL + LAYER_DIGEST, token);
        int status = connection.getResponseCode();
        if (status == 302 || status == 307) {
            connection = open(connection.getHeaderField("Location"), null);   // the token never leaves the registry
        }
        MessageDigest sha = sha256();
        try (InputStream in = connection.getInputStream(); OutputStream out = Files.newOutputStream(target.toPath())) {
            copy(in, out, sha, 512L << 20);
        }
        if (!hex(sha.digest()).equals(LAYER_DIGEST)) {
            Files.delete(target.toPath());
            throw new IOException("the downloaded Linux files do not match their digest");
        }
    }

    private static void copy(InputStream in, OutputStream out, MessageDigest sha, long most) throws IOException {
        byte[] block = new byte[1 << 16];
        long total = 0;
        for (int read = in.read(block); read >= 0; read = in.read(block)) {
            total += read;
            if (total > most) {
                throw new IOException("a download is larger than it may be");
            }
            out.write(block, 0, read);
            if (sha != null) {
                sha.update(block, 0, read);
            }
        }
    }

    private static MessageDigest sha256() throws IOException {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IOException(impossible);
        }
    }

    private static String digest(byte[] data) throws IOException {
        return hex(sha256().digest(data));
    }

    private static String hex(byte[] data) {
        StringBuilder text = new StringBuilder();
        for (byte one : data) {
            text.append(String.format("%02x", one & 0xff));
        }
        return text.toString();
    }
}
