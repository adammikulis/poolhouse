package app.poolhouse.phone;

import java.security.PrivateKey;

/**
 * What the phone does with what a push channel brings: open the sealed notification with the push key and show it, and tell the
 * node the address a distributor gave. It signs nothing and asks for nothing: no prompt can come from a push.
 */
final class PushHandler implements PushChannel.Listener {
    interface Keys {
        PrivateKey privateKey() throws Exception;

        byte[] point();

        byte[] auth();
    }

    interface Display {
        void show(PushPayload payload, NotificationModel model);
    }

    interface Node {
        void setPushAddress(String endpoint) throws Exception;
    }

    private final Keys keys;
    private final Display display;
    private final Node node;
    private final java.util.function.LongSupplier nodeClock;

    PushHandler(Keys keys, Display display, Node node, java.util.function.LongSupplier nodeClock) {
        this.keys = keys;
        this.display = display;
        this.node = node;
        this.nodeClock = nodeClock;
    }

    @Override public void address(String endpoint) {
        try {
            node.setPushAddress(endpoint);
        } catch (Exception unreachable) {
            return;
        }
    }

    @Override public void message(byte[] body) {
        try {
            PushPayload payload = PushPayload.parse(WebPushCipher.decrypt(body, keys.privateKey(), keys.point(), keys.auth()));
            display.show(payload, NotificationModel.of(payload, nodeClock.getAsLong()));
        } catch (Exception notForThisPhone) {
            return;
        }
    }

    @Override public void failed(String sentence) {
        return;
    }
}
