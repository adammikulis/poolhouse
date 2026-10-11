package app.poolhouse.phone;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** The plain screens of this app: a column of text, buttons and one line that says what is happening. Built in code, one page at a time. */
final class Ui {
    static final int INK = Color.rgb(16, 46, 52);
    static final int WARN = Color.rgb(146, 64, 14);
    static final int DANGER = Color.rgb(153, 27, 27);
    static final int CALM = Color.rgb(20, 108, 120);
    final Activity activity;
    final LinearLayout content;
    final TextView notice;

    private Ui(Activity activity, String title, String subtitle) {
        this.activity = activity;
        content = new LinearLayout(activity);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(28, 40, 28, 28);
        content.setBackgroundColor(Color.rgb(244, 248, 249));
        ScrollView scrolling = new ScrollView(activity);
        scrolling.setFillViewport(true);
        scrolling.addView(content);
        activity.setContentView(scrolling);
        TextView head = text(title, 26);
        head.setTextColor(INK);
        head.setTypeface(Typeface.DEFAULT_BOLD);
        if (!subtitle.isEmpty()) text(subtitle, 16);
        notice = text("", 15);
        notice.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
    }

    static Ui page(Activity activity, String title, String subtitle) {
        return new Ui(activity, title, subtitle);
    }

    TextView text(String value, int size) {
        TextView view = new TextView(activity);
        view.setText(value);
        view.setTextSize(size);
        view.setPadding(0, 0, 0, 16);
        content.addView(view);
        return view;
    }

    TextView line(String value, int color, boolean bold) {
        TextView view = text(value, 16);
        view.setTextColor(color);
        view.setTypeface(bold ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
        view.setTextIsSelectable(false);
        return view;
    }

    Button button(String label, Runnable action) {
        Button button = new Button(activity);
        button.setText(label);
        button.setAllCaps(false);
        button.setFilterTouchesWhenObscured(true);
        button.setOnClickListener(view -> action.run());
        content.addView(button, new LinearLayout.LayoutParams(-1, -2));
        return button;
    }

    EditText input(String hint) {
        EditText field = new EditText(activity);
        field.setHint(hint);
        field.setContentDescription(hint);
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        field.setSaveEnabled(false);
        field.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        content.addView(field, new LinearLayout.LayoutParams(-1, -2));
        return field;
    }

    void say(String sentence) {
        notice.setText(sentence);
    }
}
