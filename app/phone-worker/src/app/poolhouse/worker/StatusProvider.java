package app.poolhouse.worker;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/**
 * What the app that holds the switch shows: one row of {@code enabled}, {@code state} in words, this device's
 * fingerprint and its pool (empty for a phone in no pool). Read only, behind the same permission as the switch.
 */
public class StatusProvider extends ContentProvider {
    static Uri uri(Context context) {
        return Uri.parse("content://" + context.getPackageName() + ".state/status");
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Cursor query(Uri asked, String[] projection, String selection, String[] arguments, String order) {
        Context context = getContext();
        boolean enabled = context.getSharedPreferences("worker", Context.MODE_PRIVATE).getBoolean("enabled", false);
        String state = "off";
        String device = "";
        String pool = "";
        if (enabled) {
            state = text(new File(context.getFilesDir(), "status")).trim();
            try {
                JSONObject node = new JSONObject(text(new File(context.getFilesDir(), "work/node-status.json")));
                device = node.optString("device");
                pool = node.optString("pool");
            } catch (JSONException notYet) {
                // The node has not answered yet: the state's words say what the phone is doing.
            }
        }
        MatrixCursor found = new MatrixCursor(new String[] {"enabled", "state", "device", "pool"});
        found.addRow(new Object[] {enabled ? 1 : 0, state, device, pool});
        found.setNotificationUri(context.getContentResolver(), uri(context));
        return found;
    }

    private static String text(File file) {
        try {
            return new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
        } catch (IOException absent) {
            return "";
        }
    }

    @Override
    public String getType(Uri asked) {
        return null;
    }

    @Override
    public Uri insert(Uri asked, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri asked, String selection, String[] arguments) {
        return 0;
    }

    @Override
    public int update(Uri asked, ContentValues values, String selection, String[] arguments) {
        return 0;
    }
}
