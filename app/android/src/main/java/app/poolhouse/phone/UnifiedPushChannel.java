package app.poolhouse.phone;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import java.util.List;

/**
 * UnifiedPush: the wake-up for a phone whose app is closed. A distributor app the person installed (ntfy is one) gives this app an
 * address; the node posts the sealed notification there. No account of ours is needed and the distributor reads ciphertext only.
 * The messages come back to {@link PushReceiver}, which accepts only the ones that carry this app's instance token.
 */
final class UnifiedPushChannel implements PushChannel {
    static final String ADDRESS_REQUEST = "org.unifiedpush.android.distributor.REGISTER";
    static final String ADDRESS_RELEASE = "org.unifiedpush.android.distributor.UNREGISTER";
    private final Context context;
    private final String instance;

    UnifiedPushChannel(Context context, String instance) {
        this.context = context.getApplicationContext();
        this.instance = instance;
    }

    /** The package of the distributor installed on this phone, or an empty string when there is none. */
    static String distributor(Context context) {
        PackageManager packages = context.getPackageManager();
        List<ResolveInfo> found = packages.queryBroadcastReceivers(new Intent(ADDRESS_REQUEST), 0);
        return found.isEmpty() ? "" : found.get(0).activityInfo.packageName;
    }

    private void tell(String action) {
        String package_ = distributor(context);
        if (package_.isEmpty()) return;
        context.sendBroadcast(new Intent(action).setPackage(package_).putExtra("token", instance).putExtra("application", context.getPackageName()));
    }

    @Override public void register() {
        tell(ADDRESS_REQUEST);
    }

    @Override public void unregister() {
        tell(ADDRESS_RELEASE);
    }
}
