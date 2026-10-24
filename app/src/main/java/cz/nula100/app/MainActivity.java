package cz.nula100.app;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.location.GnssStatus;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.Process;
import android.os.SystemClock;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.Locale;

public class MainActivity extends Activity implements SensorEventListener, LocationListener {

    private static final int REQ_LOC = 7;
    static final int C_BG = 0xFF000000, C_TEXT = 0xFFFFFFFF, C_MUTED = 0xFF8B929C, C_LINE = 0xFF23272E,
            C_AMBER = 0xFFFFC21A, C_GREEN = 0xFF2BE07A, C_CYAN = 0xFF35D6FF, C_RED = 0xFFFF4B4B, C_PANEL = 0xFF14171B;
    private static final Locale CZ = new Locale("cs", "CZ");

    private SensorManager sm;
    private LocationManager lm;
    private Sensor accel, gyro;
    private HandlerThread ioThread;
    private Handler io;
    private final FusionEngine engine = new FusionEngine();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private long sensorOffset = Long.MIN_VALUE;
    private GnssStatus.Callback gnssCallback;
    private volatile int satsUsed = -1;
    private boolean listening, askedOnce, deniedForever;
    private ToneGenerator tone;
    private SharedPreferences prefs;

    private TextView pill, gpsInfo, speedTv, timerTv, detailTv, msgTv, histTv;
    private Button mainBtn;
    private Bar bar;
    private int lastState = -1, savedRun = -1;

    // ================================================================ životní cyklus

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setStatusBarColor(C_BG);
        getWindow().setNavigationBarColor(C_BG);
        sm = (SensorManager) getSystemService(Context.SENSOR_SERVICE);
        lm = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        accel = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
        gyro = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE);
        prefs = getSharedPreferences("nula100", MODE_PRIVATE);
        try { tone = new ToneGenerator(AudioManager.STREAM_MUSIC, 90); } catch (RuntimeException e) { tone = null; }
        buildUi();
    }

    @Override
    public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
        buildUi();
    }

    @Override
    protected void onResume() {
        super.onResume();
        startListening();
        if (!hasLocPerm() && !askedOnce) { askedOnce = true; requestLocPerm(); }
        ui.removeCallbacks(renderLoop);
        ui.post(renderLoop);
    }

    @Override
    protected void onPause() {
        super.onPause();
        ui.removeCallbacks(renderLoop);
        stopListening();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (tone != null) tone.release();
    }

    // ================================================================ oprávnění

    private boolean hasLocPerm() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    private void requestLocPerm() {
        requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOC);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        super.onRequestPermissionsResult(code, perms, res);
        if (code != REQ_LOC) return;
        if (hasLocPerm()) {
            deniedForever = false;
            stopListening();
            startListening();
        } else {
            deniedForever = !shouldShowRequestPermissionRationale(Manifest.permission.ACCESS_FINE_LOCATION);
        }
    }

    private boolean gpsEnabled() {
        try { return lm.isProviderEnabled(LocationManager.GPS_PROVIDER); } catch (Exception e) { return false; }
    }

    // ================================================================ senzory

    private void startListening() {
        if (listening) return;
        ioThread = new HandlerThread("mereni", Process.THREAD_PRIORITY_URGENT_DISPLAY);
        ioThread.start();
        io = new Handler(ioThread.getLooper());
        if (accel != null) sm.registerListener(this, accel, SensorManager.SENSOR_DELAY_FASTEST, io);
        if (gyro != null) sm.registerListener(this, gyro, SensorManager.SENSOR_DELAY_FASTEST, io);
        if (hasLocPerm()) {
            try {
                lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 0L, 0f, this, ioThread.getLooper());
                gnssCallback = new GnssStatus.Callback() {
                    @Override
                    public void onSatelliteStatusChanged(GnssStatus s) {
                        int used = 0;
                        for (int i = 0; i < s.getSatelliteCount(); i++) if (s.usedInFix(i)) used++;
                        satsUsed = used;
                    }
                };
                lm.registerGnssStatusCallback(gnssCallback, io);
            } catch (SecurityException | IllegalArgumentException e) {
                gnssCallback = null;
            }
        }
        listening = true;
    }

    private void stopListening() {
        if (!listening) return;
        sm.unregisterListener(this);
        try { lm.removeUpdates(this); } catch (SecurityException ignored) { }
        if (gnssCallback != null) { lm.unregisterGnssStatusCallback(gnssCallback); gnssCallback = null; }
        ioThread.quitSafely();
        listening = false;
    }

    @Override
    public void onSensorChanged(SensorEvent ev) {
        long t = ev.timestamp;
        if (sensorOffset == Long.MIN_VALUE) {
            // většina zařízení používá stejnou časovou osu jako GPS; pokud ne, srovnáme ji
            long d = SystemClock.elapsedRealtimeNanos() - t;
            sensorOffset = Math.abs(d) > 500_000_000L ? d : 0;
        }
        t += sensorOffset;
        int type = ev.sensor.getType();
        if (type == Sensor.TYPE_ACCELEROMETER) engine.onAccel(t, ev.values[0], ev.values[1], ev.values[2]);
        else if (type == Sensor.TYPE_GYROSCOPE) engine.onGyro(t, ev.values[0], ev.values[1], ev.values[2]);
    }

    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) { }

    @Override
    public void onLocationChanged(Location l) {
        double speed = l.hasSpeed() ? l.getSpeed() : Double.NaN;
        double sAcc = Double.NaN;
        if (Build.VERSION.SDK_INT >= 26 && l.hasSpeedAccuracy()) sAcc = l.getSpeedAccuracyMetersPerSecond();
        double hAcc = l.hasAccuracy() ? l.getAccuracy() : 999;
        engine.onGps(l.getElapsedRealtimeNanos(), speed, sAcc, hAcc);
    }

    // starší Androidy tyto metody volají, proto musí být implementované
    @Override public void onStatusChanged(String provider, int status, Bundle extras) { }
    @Override public void onProviderEnabled(String provider) { }
    @Override public void onProviderDisabled(String provider) { }

    // ================================================================ UI

    private int dp(float v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private TextView text(float sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        t.setTypeface(Typeface.create("sans-serif-condensed", bold ? Typeface.BOLD : Typeface.NORMAL));
        t.setFontFeatureSettings("tnum");
        t.setIncludeFontPadding(false);
        return t;
    }

    private TextView label(String s) {
        TextView t = text(13, C_MUTED, true);
        t.setText(s);
        t.setLetterSpacing(0.18f);
        t.setGravity(Gravity.CENTER);
        return t;
    }

    private GradientDrawable round(int color, float radiusDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(radiusDp));
        return d;
    }

    private void buildUi() {
        boolean land = getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(C_BG);
        root.setPadding(dp(20), dp(land ? 10 : 18), dp(20), dp(land ? 10 : 18));

        // horní řádek: stav + GPS
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        pill = text(15, C_BG, true);
        pill.setLetterSpacing(0.1f);
        pill.setPadding(dp(14), dp(8), dp(14), dp(8));
        top.addView(pill);
        View sp = new View(this);
        top.addView(sp, new LinearLayout.LayoutParams(0, 1, 1f));
        gpsInfo = text(13, C_MUTED, false);
        gpsInfo.setGravity(Gravity.END);
        top.addView(gpsInfo);
        root.addView(top);

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(land ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL);
        body.setGravity(Gravity.CENTER);

        // rychlost
        LinearLayout speedBlock = new LinearLayout(this);
        speedBlock.setOrientation(LinearLayout.VERTICAL);
        speedBlock.setGravity(Gravity.CENTER);
        speedBlock.addView(label("RYCHLOST"));
        speedTv = text(land ? 120 : 150, C_TEXT, true);
        speedTv.setGravity(Gravity.CENTER);
        speedTv.setText("0");
        speedBlock.addView(speedTv);
        TextView unit = text(20, C_MUTED, false);
        unit.setText("km/h");
        unit.setGravity(Gravity.CENTER);
        speedBlock.addView(unit);
        bar = new Bar(this);
        LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(10));
        bl.topMargin = dp(16);
        speedBlock.addView(bar, bl);

        // čas
        LinearLayout timeBlock = new LinearLayout(this);
        timeBlock.setOrientation(LinearLayout.VERTICAL);
        timeBlock.setGravity(Gravity.CENTER);
        timeBlock.addView(label("ČAS 0–100 KM/H"));
        timerTv = text(land ? 80 : 96, C_MUTED, true);
        timerTv.setGravity(Gravity.CENTER);
        timerTv.setText("0,00");
        timeBlock.addView(timerTv);
        detailTv = text(15, C_MUTED, false);
        detailTv.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        dl.topMargin = dp(8);
        timeBlock.addView(detailTv, dl);
        msgTv = text(17, C_TEXT, false);
        msgTv.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams ml = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        ml.topMargin = dp(14);
        timeBlock.addView(msgTv, ml);

        if (land) {
            body.addView(speedBlock, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
            View gap = new View(this);
            body.addView(gap, new LinearLayout.LayoutParams(dp(24), 1));
            body.addView(timeBlock, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        } else {
            body.addView(speedBlock, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1.15f));
            body.addView(timeBlock, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        }
        root.addView(body, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        // tlačítko + historie
        mainBtn = new Button(this);
        mainBtn.setAllCaps(false);
        mainBtn.setTextSize(TypedValue.COMPLEX_UNIT_SP, 22);
        mainBtn.setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD));
        mainBtn.setLetterSpacing(0.08f);
        mainBtn.setStateListAnimator(null);
        mainBtn.setOnClickListener(v -> onMainButton());
        root.addView(mainBtn, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(land ? 60 : 76)));
        histTv = text(14, C_MUTED, false);
        histTv.setGravity(Gravity.CENTER);
        histTv.setOnLongClickListener(v -> { prefs.edit().remove("hist").apply(); return true; });
        LinearLayout.LayoutParams hl = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        hl.topMargin = dp(10);
        root.addView(histTv, hl);

        setContentView(root);
        lastState = -1;
    }

    private void onMainButton() {
        if (!hasLocPerm()) {
            if (deniedForever) {
                Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", getPackageName(), null));
                startActivity(i);
            } else requestLocPerm();
            return;
        }
        if (!gpsEnabled()) { startActivity(new Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)); return; }
        if (accel == null) return;
        FusionEngine.Snapshot s = engine.snapshot();
        boolean busy = s.state == FusionEngine.WAITING || s.state == FusionEngine.ARMED
                || s.state == FusionEngine.MEASURING || (s.state == FusionEngine.FINISHED && s.refining);
        if (busy) engine.reset(); else engine.arm();
    }

    private final Runnable renderLoop = new Runnable() {
        @Override public void run() { render(); ui.postDelayed(this, 33); }
    };

    private String sec(double s) { return String.format(CZ, "%.2f", s); }

    private void render() {
        FusionEngine.Snapshot s = engine.snapshot();
        boolean perm = hasLocPerm(), gpsOn = gpsEnabled();

        // rychlost
        int kmh = (int) Math.round(s.speedKmh);
        speedTv.setText(String.valueOf(kmh));
        int barColor = s.state == FusionEngine.FINISHED ? C_GREEN : s.state == FusionEngine.MEASURING ? C_CYAN : C_MUTED;
        bar.set((float) Math.min(1.0, s.speedKmh / 100.0), barColor);

        // čas
        if (s.state == FusionEngine.MEASURING) {
            timerTv.setText(sec(s.elapsedS));
            timerTv.setTextColor(C_TEXT);
        } else if (s.state == FusionEngine.FINISHED) {
            timerTv.setText(sec(s.resultS));
            timerTv.setTextColor(s.refining ? C_AMBER : C_GREEN);
        } else {
            timerTv.setText("0,00");
            timerTv.setTextColor(C_MUTED);
        }
        if (s.state == FusionEngine.FINISHED) {
            String g = Double.isNaN(s.gpsResultS) ? "–" : sec(s.gpsResultS) + " s";
            detailTv.setText("jen GPS: " + g + "   ·   max " + String.format(CZ, "%.2f", s.peakG) + " g");
        } else detailTv.setText("sekundy");

        // stav
        String pillText; int pillColor; String msg; String btn; int btnColor; int btnText = C_BG;
        boolean busy = s.state == FusionEngine.WAITING || s.state == FusionEngine.ARMED
                || s.state == FusionEngine.MEASURING || (s.state == FusionEngine.FINISHED && s.refining);
        if (!perm) {
            pillText = "BEZ POLOHY"; pillColor = C_RED;
            msg = deniedForever ? "Přístup k poloze je zakázaný. Povol ho v nastavení aplikace." : "Aplikace potřebuje GPS pro přesnou rychlost.";
            btn = deniedForever ? "OTEVŘÍT NASTAVENÍ" : "POVOLIT POLOHU"; btnColor = C_AMBER;
        } else if (!gpsOn) {
            pillText = "GPS VYPNUTÉ"; pillColor = C_RED;
            msg = "Zapni polohu (GPS) v telefonu.";
            btn = "ZAPNOUT GPS"; btnColor = C_AMBER;
        } else if (accel == null) {
            pillText = "BEZ SENZORU"; pillColor = C_RED;
            msg = "Telefon nemá akcelerometr, měření nejde spustit.";
            btn = "NEDOSTUPNÉ"; btnColor = C_PANEL; btnText = C_MUTED;
        } else {
            msg = s.msg;
            switch (s.state) {
                case FusionEngine.WAITING: pillText = "PŘÍPRAVA"; pillColor = C_AMBER; break;
                case FusionEngine.ARMED:
                    pillText = "PŘIPRAVENO";
                    pillColor = (SystemClock.uptimeMillis() / 500) % 2 == 0 ? C_AMBER : 0xFF8A6A10;
                    break;
                case FusionEngine.MEASURING: pillText = "MĚŘÍM"; pillColor = C_CYAN; break;
                case FusionEngine.FINISHED: pillText = s.refining ? "CÍL · UPŘESŇUJI" : "CÍL"; pillColor = C_GREEN; break;
                case FusionEngine.ABORTED: pillText = "ZRUŠENO"; pillColor = C_RED; break;
                default: pillText = "NEČINNÉ"; pillColor = C_MUTED; break;
            }
            if (busy) { btn = "ZRUŠIT"; btnColor = C_PANEL; btnText = C_TEXT; }
            else { btn = s.state == FusionEngine.IDLE ? "PŘIPRAVIT" : "NOVÉ MĚŘENÍ"; btnColor = C_AMBER; }
        }
        pill.setText(pillText);
        pill.setBackground(round(pillColor, 999));
        msgTv.setText(msg);
        mainBtn.setText(btn);
        mainBtn.setTextColor(btnText);
        mainBtn.setBackground(round(btnColor, 16));

        // GPS info
        StringBuilder gi = new StringBuilder();
        if (perm && gpsOn) {
            if (s.gpsFresh) gi.append(String.format(CZ, "GPS %.0f Hz · ±%.0f m", s.gpsHz, s.gpsHAcc));
            else gi.append("GPS hledá signál…");
            if (satsUsed >= 0) gi.append(" · ").append(satsUsed).append(" sat");
        } else gi.append("GPS —");
        gi.append('\n').append(String.format(CZ, "senzory %.0f Hz%s", s.sensorHz, gyro != null ? " · gyro" : ""));
        gpsInfo.setText(gi.toString());

        // přechody stavů: zvuk a vibrace
        if (s.state != lastState) {
            if (lastState != -1) {
                if (s.state == FusionEngine.ARMED) beep(ToneGenerator.TONE_PROP_BEEP, 80);
                else if (s.state == FusionEngine.MEASURING) { beep(ToneGenerator.TONE_PROP_BEEP2, 120); vibrate(40); }
                else if (s.state == FusionEngine.FINISHED) { beep(ToneGenerator.TONE_PROP_ACK, 300); vibrate(150); }
                else if (s.state == FusionEngine.ABORTED) beep(ToneGenerator.TONE_PROP_NACK, 300);
            }
            lastState = s.state;
        }
        if (s.state == FusionEngine.FINISHED && !s.refining && savedRun != s.runId && !Double.isNaN(s.resultS)) {
            savedRun = s.runId;
            saveResult(s.resultS);
        }
        histTv.setText(historyText());
    }

    private void beep(int toneType, int ms) {
        if (tone != null) try { tone.startTone(toneType, ms); } catch (RuntimeException ignored) { }
    }

    @SuppressWarnings("deprecation")
    private void vibrate(int ms) {
        Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
        if (v == null || !v.hasVibrator()) return;
        if (Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
        else v.vibrate(ms);
    }

    private void saveResult(double s) {
        String old = prefs.getString("hist", "");
        String add = String.format(Locale.US, "%.2f", s);
        String[] parts = old.isEmpty() ? new String[0] : old.split(";");
        StringBuilder sb = new StringBuilder(add);
        for (int i = 0; i < parts.length && i < 9; i++) sb.append(';').append(parts[i]);
        prefs.edit().putString("hist", sb.toString()).apply();
    }

    private String historyText() {
        String h = prefs.getString("hist", "");
        if (h.isEmpty()) return "Zatím žádné měření.";
        String[] p = h.split(";");
        double best = Double.MAX_VALUE;
        StringBuilder last = new StringBuilder();
        for (int i = 0; i < p.length; i++) {
            try {
                double v = Double.parseDouble(p[i]);
                best = Math.min(best, v);
                if (i < 4) { if (last.length() > 0) last.append("  ·  "); last.append(sec(v)); }
            } catch (NumberFormatException ignored) { }
        }
        return "Nejlepší " + sec(best) + " s     Poslední: " + last;
    }

    /** Ukazatel postupu k 100 km/h. */
    static final class Bar extends View {
        private float f;
        private int color = C_MUTED;
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final RectF r = new RectF();

        Bar(Context c) { super(c); }

        void set(float v, int col) {
            if (v != f || col != color) { f = v; color = col; invalidate(); }
        }

        @Override
        protected void onDraw(Canvas c) {
            float w = getWidth(), h = getHeight();
            p.setColor(C_LINE);
            r.set(0, 0, w, h);
            c.drawRoundRect(r, h / 2, h / 2, p);
            if (f > 0) {
                p.setColor(color);
                r.set(0, 0, Math.max(h, w * f), h);
                c.drawRoundRect(r, h / 2, h / 2, p);
            }
        }
    }
}
