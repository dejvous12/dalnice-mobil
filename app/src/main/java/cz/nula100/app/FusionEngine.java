package cz.nula100.app;

/**
 * Měřicí jádro 0–100 km/h.
 *
 * Princip (sensor fusion):
 *  1. Ve stoji se z akcelerometru odhadne vektor gravitace v souřadnicích telefonu.
 *     Telefon tak může být v držáku natočený libovolně.
 *  2. Gyroskop během jízdy natáčí odhad gravitace (kompenzace „podřepnutí“ auta při rozjezdu).
 *  3. Start: vodorovné zrychlení překročí práh. Čas startu se dohledá zpětně
 *     v kruhovém bufferu na okamžik, kdy zrychlení ještě bylo nulové (přesnost v ms).
 *  4. Směr jízdy se naučí z prvních ~0,4 s rozjezdu.
 *  5. Kalmanův filtr se stavem [rychlost, bias akcelerometru] integruje zrychlení
 *     (stovky Hz) a opravuje ho rychlostí z GPS (Doppler, 1–10 Hz). GPS fixy mají
 *     vlastní časovou značku, takže se zpoždění GPS řeší přepočtem historie od startu.
 *  6. Průjezd 100 km/h se interpoluje mezi vzorky. Po cíli se ještě 3 s sbírají GPS fixy
 *     a výsledek se zpřesní.
 *
 * Všechny metody volej z jednoho vlákna (senzorové vlákno). snapshot() je bezpečný i z UI.
 * Časy jsou v nanosekundách na časové ose SystemClock.elapsedRealtimeNanos().
 */
final class FusionEngine {

    static final int IDLE = 0, WAITING = 1, ARMED = 2, MEASURING = 3, FINISHED = 4, ABORTED = 5;
    static final double TARGET = 100.0 / 3.6;           // 27,78 m/s

    // ---- ladicí konstanty ----
    private static final double TRIGGER_ACC = 0.8;      // m/s², práh pro start (~0,08 g)
    private static final long TRIGGER_HOLD = 60_000_000L; // 60 ms nad prahem
    private static final double QUIET_ACC = 0.12;       // m/s², „ještě stojí“
    private static final double STILL_ACC = 0.35;       // m/s², stání (volnoběh vibruje)
    private static final double STILL_GPS = 1.0;        // m/s
    private static final long STILL_TIME = 1_000_000_000L;
    private static final long GPS_FRESH = 2_500_000_000L;
    private static final double MAX_GPS_HACC = 30.0;    // m
    private static final long DIR_LOCK = 400_000_000L;  // učení směru jízdy
    private static final long REFINE_TIME = 3_000_000_000L;
    private static final long MAX_RUN = 40_000_000_000L;
    private static final double Q_ACC = 0.35;           // šum zrychlení (m/s²)²/s
    private static final double Q_BIAS = 0.01;          // drift biasu
    private static final double R_MIN = 0.2;            // min. nejistota GPS rychlosti, m/s
    private static final double R_DEFAULT = 0.5;

    // ---- stav ----
    private int state = IDLE;
    private int runId = 0;
    private String msg = "Zastav auto a stiskni PŘIPRAVIT.";

    // gravitace a gyroskop
    private final double[] g = new double[3];
    private boolean gInit;
    private double wx, wy, wz;
    private long lastAccT = -1;
    private double hf;                // filtrovaná velikost vodorovného zrychlení
    private long stillSince = -1;
    private long aboveSince = -1;

    // statistika senzorů
    private long rateWindowStart = -1;
    private int rateCount;
    private double sensorHz;
    private final long[] gpsTimes = new long[8];
    private int gpsTimesN;

    // GPS
    private long lastGpsT = -1;
    private double gpsSpeed = 0, gpsHAcc = 999;
    private boolean hasGpsSpeed;

    // kruhový buffer před startem (~4 s při 1 kHz)
    private static final int RING = 4096;
    private final long[] rt = new long[RING];
    private final float[] rx = new float[RING], ry = new float[RING], rz = new float[RING], rh = new float[RING];
    private int rHead, rCount;

    // data měření
    private long[] mt = new long[16384];
    private float[] mx = new float[16384], my = new float[16384], mz = new float[16384];
    private int mN;
    private long[] gt = new long[256];
    private double[] gv = new double[256], gr = new double[256];
    private int gN;
    private long startT;
    private double fx, fy, fz, sumX, sumY, sumZ;
    private boolean fLocked;

    // Kalman
    private double kv, kb, p00, p01, p10, p11;
    private long kT;
    private int procIdx, gpsIdx;
    private long crossT = -1;
    private double maxV, peakAcc, accLp;
    private boolean refining;
    private double resultS = Double.NaN, gpsResultS = Double.NaN;
    private boolean gpsUsedInRun;

    // =================================================================== API

    synchronized void arm() {
        runId++;
        state = WAITING;
        resultS = Double.NaN;
        gpsResultS = Double.NaN;
        refining = false;
        stillSince = -1;
        aboveSince = -1;
        msg = "Čekám na GPS a stání vozu…";
    }

    synchronized void reset() {
        state = IDLE;
        refining = false;
        msg = "Zastav auto a stiskni PŘIPRAVIT.";
    }

    synchronized void onGyro(long t, float x, float y, float z) {
        wx = x; wy = y; wz = z;
    }

    synchronized void onGps(long t, double speed, double speedAcc, double hAcc) {
        if (t <= 0) return;
        if (gpsTimesN < gpsTimes.length) gpsTimes[gpsTimesN++] = t;
        else { System.arraycopy(gpsTimes, 1, gpsTimes, 0, gpsTimes.length - 1); gpsTimes[gpsTimes.length - 1] = t; }
        lastGpsT = t;
        gpsHAcc = hAcc;
        hasGpsSpeed = !Double.isNaN(speed);
        if (hasGpsSpeed) gpsSpeed = speed;

        if ((state == MEASURING || (state == FINISHED && refining)) && hasGpsSpeed && t > startT) {
            double r = Double.isNaN(speedAcc) ? R_DEFAULT : Math.max(R_MIN, speedAcc);
            if (gN == gt.length) {
                gt = java.util.Arrays.copyOf(gt, gN * 2);
                gv = java.util.Arrays.copyOf(gv, gN * 2);
                gr = java.util.Arrays.copyOf(gr, gN * 2);
            }
            gt[gN] = t; gv[gN] = speed; gr[gN] = r * r; gN++;
            gpsUsedInRun = true;
            if (mN > 0 && t <= mt[mN - 1]) reprocess();   // fix přišel se zpožděním → přepočet historie
            computeGpsOnly();
            afterProcess(lastAccT);
        }
    }

    synchronized void onAccel(long t, float ax, float ay, float az) {
        // --- vzorkovací frekvence
        if (rateWindowStart < 0) rateWindowStart = t;
        rateCount++;
        if (t - rateWindowStart >= 1_000_000_000L) {
            sensorHz = rateCount * 1e9 / (t - rateWindowStart);
            rateCount = 0; rateWindowStart = t;
        }
        double dt = lastAccT < 0 ? 0 : (t - lastAccT) * 1e-9;
        if (dt < 0 || dt > 0.2) dt = 0;
        lastAccT = t;

        // --- gravitace: gyroskop natáčí vektor, ve stoji dolní propust
        if (!gInit) { g[0] = ax; g[1] = ay; g[2] = az; gInit = true; }
        else if (dt > 0) {
            // dg/dt = -ω × g
            double cx = wy * g[2] - wz * g[1];
            double cy = wz * g[0] - wx * g[2];
            double cz = wx * g[1] - wy * g[0];
            g[0] -= cx * dt; g[1] -= cy * dt; g[2] -= cz * dt;
            boolean measuring = state == MEASURING || (state == FINISHED && refining);
            if (!measuring) {
                double tau = isStill(t) ? 0.8 : 4.0;
                double a = Math.min(1.0, dt / tau);
                g[0] += (ax - g[0]) * a; g[1] += (ay - g[1]) * a; g[2] += (az - g[2]) * a;
            }
        }
        double gn = Math.sqrt(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]);
        if (gn < 1e-3) return;
        double ux = g[0] / gn, uy = g[1] / gn, uz = g[2] / gn;
        double lx = ax - g[0], ly = ay - g[1], lz = az - g[2];
        double vert = lx * ux + ly * uy + lz * uz;
        double hx = lx - vert * ux, hy = ly - vert * uy, hz = lz - vert * uz;
        double hmag = Math.sqrt(hx * hx + hy * hy + hz * hz);
        hf += (hmag - hf) * (dt > 0 ? Math.min(1.0, dt / 0.03) : 1.0);

        // --- stání
        boolean stillNow = hf < STILL_ACC && (!hasGpsSpeed || !gpsFresh(t) || gpsSpeed < STILL_GPS);
        if (stillNow) { if (stillSince < 0) stillSince = t; } else stillSince = -1;

        switch (state) {
            case WAITING:
                if (!gpsFresh(t) || gpsHAcc > MAX_GPS_HACC) msg = "Čekám na GPS signál (venku, výhled na oblohu)…";
                else if (!isStill(t)) msg = "Zastav vůz a nech telefon v klidu…";
                else { state = ARMED; rCount = 0; rHead = 0; aboveSince = -1; msg = "Připraveno. Rozjeď se, stopky startují samy."; }
                break;
            case ARMED:
                pushRing(t, hx, hy, hz, hf);
                if (!gpsFresh(t)) { state = WAITING; msg = "Ztracen GPS signál…"; break; }
                if (hf > TRIGGER_ACC) {
                    if (aboveSince < 0) aboveSince = t;
                    if (t - aboveSince >= TRIGGER_HOLD) startMeasuring(t);
                } else aboveSince = -1;
                if (state == ARMED && hasGpsSpeed && gpsSpeed > 2.5) {
                    state = WAITING; msg = "Rozjezd byl příliš pozvolný. Zastav a zkus to znovu.";
                }
                break;
            case MEASURING:
            case FINISHED:
                if (state == FINISHED && !refining) break;
                appendSample(t, hx, hy, hz);
                if (!fLocked) {
                    sumX += hx; sumY += hy; sumZ += hz;
                    updateDir();
                    if (t - startT >= DIR_LOCK) { fLocked = true; reprocess(); }
                    else processUpTo();
                } else processUpTo();
                afterProcess(t);
                break;
            default:
                break;
        }
    }

    /** Stav pro UI. */
    synchronized Snapshot snapshot() {
        Snapshot s = new Snapshot();
        s.state = state;
        s.runId = runId;
        s.msg = msg;
        boolean fused = state == MEASURING || (state == FINISHED && refining);
        boolean fresh = gpsFresh(lastAccT);
        s.speedKmh = fused ? Math.max(0, kv) * 3.6 : (fresh && hasGpsSpeed ? gpsSpeed * 3.6 : 0);
        s.elapsedS = state == MEASURING ? Math.max(0, (lastAccT - startT) * 1e-9) : 0;
        s.resultS = resultS;
        s.gpsResultS = gpsResultS;
        s.refining = refining;
        s.gpsFresh = fresh;
        s.gpsHAcc = gpsHAcc;
        s.sensorHz = sensorHz;
        s.peakG = peakAcc / 9.80665;
        s.gpsUsed = gpsUsedInRun;
        if (gpsTimesN >= 2) {
            long span = gpsTimes[gpsTimesN - 1] - gpsTimes[0];
            s.gpsHz = span > 0 ? (gpsTimesN - 1) * 1e9 / span : 0;
        }
        return s;
    }

    static final class Snapshot {
        int state, runId;
        String msg;
        double speedKmh, elapsedS, resultS, gpsResultS, gpsHz, sensorHz, gpsHAcc, peakG;
        boolean refining, gpsFresh, gpsUsed;
    }

    // ============================================================ interní

    private boolean gpsFresh(long now) { return lastGpsT > 0 && now - lastGpsT < GPS_FRESH; }

    private boolean isStill(long t) { return stillSince >= 0 && t - stillSince >= STILL_TIME; }

    private void pushRing(long t, double x, double y, double z, double h) {
        rt[rHead] = t; rx[rHead] = (float) x; ry[rHead] = (float) y; rz[rHead] = (float) z; rh[rHead] = (float) h;
        rHead = (rHead + 1) % RING;
        if (rCount < RING) rCount++;
    }

    private void startMeasuring(long now) {
        // najdi poslední „klidný“ vzorek před překročením prahu
        int newest = (rHead - 1 + RING) % RING;
        int onset = -1;
        for (int k = 0; k < rCount; k++) {
            int idx = (newest - k + RING) % RING;
            if (rt[idx] < aboveSince && rh[idx] < QUIET_ACC) { onset = idx; break; }
        }
        int first;
        if (onset < 0) { first = (rHead - rCount + RING) % RING; startT = rt[first]; }
        else { startT = rt[onset]; first = (onset + 1) % RING; }

        mN = 0; gN = 0; sumX = sumY = sumZ = 0; fLocked = false;
        crossT = -1; maxV = 0; peakAcc = 0; gpsUsedInRun = false;
        resultS = Double.NaN; gpsResultS = Double.NaN; refining = false;
        for (int idx = first; ; idx = (idx + 1) % RING) {
            appendSample(rt[idx], rx[idx], ry[idx], rz[idx]);
            sumX += rx[idx]; sumY += ry[idx]; sumZ += rz[idx];
            if (idx == newest) break;
        }
        updateDir();
        state = MEASURING;
        msg = "Měřím…";
        reprocess();
    }

    private void appendSample(long t, double x, double y, double z) {
        if (mN == mt.length) {
            int n = mN * 2;
            mt = java.util.Arrays.copyOf(mt, n);
            mx = java.util.Arrays.copyOf(mx, n); my = java.util.Arrays.copyOf(my, n); mz = java.util.Arrays.copyOf(mz, n);
        }
        mt[mN] = t; mx[mN] = (float) x; my[mN] = (float) y; mz[mN] = (float) z; mN++;
    }

    private void updateDir() {
        double n = Math.sqrt(sumX * sumX + sumY * sumY + sumZ * sumZ);
        if (n > 1e-6) { fx = sumX / n; fy = sumY / n; fz = sumZ / n; }
    }

    private void reprocess() {
        kv = 0; kb = 0;
        p00 = 0.0004; p01 = 0; p10 = 0; p11 = 0.25;   // ve stoji v = 0 přesně, bias neznámý
        kT = startT; procIdx = 0; gpsIdx = 0; crossT = -1; maxV = 0; peakAcc = 0; accLp = 0;
        processUpTo();
    }

    private void processUpTo() {
        for (; procIdx < mN; procIdx++) {
            long ti = mt[procIdx];
            double a = mx[procIdx] * fx + my[procIdx] * fy + mz[procIdx] * fz;
            while (gpsIdx < gN && gt[gpsIdx] <= ti) {
                if (gt[gpsIdx] > kT) step(gt[gpsIdx], a);
                update(gv[gpsIdx], gr[gpsIdx]);
                gpsIdx++;
            }
            if (ti > kT) step(ti, a);
        }
    }

    private void step(long t, double a) {
        double dt = (t - kT) * 1e-9;
        double vPrev = kv;
        double acc = a - kb;
        kv += acc * dt;
        double np00 = p00 - dt * (p10 + p01) + dt * dt * p11 + Q_ACC * dt;
        double np01 = p01 - dt * p11;
        double np10 = p10 - dt * p11;
        double np11 = p11 + Q_BIAS * dt;
        p00 = np00; p01 = np01; p10 = np10; p11 = np11;
        accLp += (acc - accLp) * Math.min(1.0, dt / 0.1);
        if (accLp > peakAcc) peakAcc = accLp;
        if (crossT < 0 && kv >= TARGET && vPrev < TARGET) {
            crossT = kT + (long) ((TARGET - vPrev) / (kv - vPrev) * (t - kT));
        }
        if (kv > maxV) maxV = kv;
        kT = t;
    }

    private void update(double z, double r) {
        double vPrev = kv;
        double y = z - kv;
        double s = p00 + r;
        double k0 = p00 / s, k1 = p10 / s;
        kv += k0 * y; kb += k1 * y;
        double n00 = (1 - k0) * p00, n01 = (1 - k0) * p01;
        double n10 = p10 - k1 * p00, n11 = p11 - k1 * p01;
        p00 = n00; p01 = n01; p10 = n10; p11 = n11;
        if (crossT < 0 && kv >= TARGET && vPrev < TARGET) crossT = kT;
        if (kv > maxV) maxV = kv;
    }

    private void computeGpsOnly() {
        gpsResultS = Double.NaN;
        for (int i = 1; i < gN; i++) {
            if (gv[i - 1] < TARGET && gv[i] >= TARGET) {
                double f = (TARGET - gv[i - 1]) / (gv[i] - gv[i - 1]);
                gpsResultS = (gt[i - 1] + f * (gt[i] - gt[i - 1]) - startT) * 1e-9;
                return;
            }
        }
    }

    private void afterProcess(long now) {
        if (state == MEASURING) {
            if (crossT >= 0) {
                state = FINISHED; refining = true;
                resultS = (crossT - startT) * 1e-9;
                msg = "Cíl! Upřesňuji podle GPS…";
            } else if (now - startT > MAX_RUN) {
                state = ABORTED; msg = "Měření trvalo přes 40 s, zrušeno.";
            } else if (maxV > 5 && kv < maxV - 4) {
                state = ABORTED; msg = "Auto zpomalilo, měření zrušeno.";
            }
        } else if (state == FINISHED && refining) {
            if (crossT < 0) {   // GPS korekce ukázala, že 100 km/h ještě nebylo dosaženo
                state = MEASURING; refining = false; resultS = Double.NaN; msg = "Měřím…";
                return;
            }
            resultS = (crossT - startT) * 1e-9;
            if (crossT >= 0 && now - crossT > REFINE_TIME) {
                refining = false;
                msg = gpsUsedInRun ? "Hotovo. Čas zpřesněn podle GPS." : "Hotovo (bez GPS korekce, nižší přesnost).";
            }
        }
    }
}
