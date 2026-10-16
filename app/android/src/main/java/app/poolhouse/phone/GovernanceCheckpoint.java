package app.poolhouse.phone;

import android.content.Context;
import java.io.File;

/** The app-owned location of a pool key's governance observations. */
final class GovernanceCheckpoint {
    private GovernanceCheckpoint() { }

    static File directory(Context context, String handle) throws Exception {
        PoolKeyBinding.alias(handle);
        File base = new File(context.getNoBackupFilesDir(), "pool-governance");
        File directory = new File(base, handle);
        if (!directory.getCanonicalFile().getParentFile().equals(base.getCanonicalFile())) throw new SecurityException("This pool key has no private checkpoint.");
        return directory;
    }
}
