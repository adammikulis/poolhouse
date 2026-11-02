package app.poolhouse.phone;

import org.json.JSONObject;

/**
 * Signs this phone's own addition with its approval key: the proof that it holds the key and, for a code that carries terms, the key's
 * acceptance of being listed as an admin key under them. The request is handed over only when every signature was made; a prompt the
 * person leaves sends nothing.
 */
final class AddSigning {
    static final String PROOF_TITLE = "Add this phone";
    static final String ACCEPT_TITLE = "Accept being listed as an admin of this pool";
    private AddSigning() { }

    interface Done {
        /** The request to send, with its proof and, where the code carries terms, its acceptance. */
        void signed(JSONObject request);

        void failed(String sentence);
    }

    static void sign(Authorizer key, AddLink link, String device, JSONObject body, Done done) {
        if (link.admit != null && !key.strongAvailable()) {
            done.failed("This pool lists the phones that approve for it, which takes your fingerprint or a strong face unlock, and this phone has not set one up: set one up in the phone's settings, then scan a new code.");
            return;
        }
        byte[] proof;
        try {
            if (link.admit != null && !Hex.of(key.publicPoint()).equals(body.getString("key"))) throw new SecurityException("The key in the request is not this phone's approval key.");
            proof = AddRequest.proofBytes(device, body);
        } catch (Exception failure) {
            done.failed("This phone could not prepare its addition, so nothing was sent.");
            return;
        }
        key.sign(proof, PROOF_TITLE, false, new Authorizer.Result() {
            @Override public void signed(byte[] der) {
                proved(key, link, body, der, done);
            }

            @Override public void failed(String sentence) {
                done.failed(sentence);
            }
        });
    }

    private static void proved(Authorizer key, AddLink link, JSONObject body, byte[] der, Done done) {
        JSONObject request;
        byte[] acceptance = null;
        try {
            request = AddRequest.withProof(body, der);
            if (link.admit != null) {
                acceptance = AddRequest.acceptanceBytes(link.admit, key.publicPoint());
            }
        } catch (Exception failure) {
            done.failed("This phone could not prepare its addition, so nothing was sent.");
            return;
        }
        if (acceptance == null) {
            done.signed(request);
            return;
        }
        key.sign(acceptance, ACCEPT_TITLE, true, new Authorizer.Result() {
            @Override public void signed(byte[] accepted) {
                JSONObject complete;
                try {
                    complete = AddRequest.withAcceptance(request, accepted);
                } catch (Exception failure) {
                    done.failed("The acceptance could not be read, so nothing was sent.");
                    return;
                }
                done.signed(complete);
            }

            @Override public void failed(String sentence) {
                done.failed(sentence);
            }
        });
    }
}
