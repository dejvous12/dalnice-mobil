"""
DÁLNICE MOBILE - Kivy verze pro Android
----------------------------------------
Dotykové ovládání: šipky vlevo/vpravo dole, brzda, SKOK.
Auto jede samo (cruise), ty uhýbáš provozu. Jedou coiny, garáž, online žebříček.
"""
import os
import io
import json
import math
import random
import colorsys
import threading
import urllib.request
import urllib.error
import datetime

from kivy.config import Config
Config.set("graphics", "resizable", False)

from kivy.app import App
from kivy.core.window import Window
from kivy.core.image import Image as CoreImage
from kivy.graphics import Color, Rectangle, Ellipse, Line, RoundedRectangle
from kivy.graphics.texture import Texture
from kivy.uix.widget import Widget
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.clock import Clock

try:
    from PIL import Image as PILImage
    import numpy as np
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")


def save_path():
    """Zapisovatelne misto na ulozeni profilu (na Androidu je slozka hry read-only)."""
    app = App.get_running_app()
    base = app.user_data_dir if app is not None else HERE
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        base = HERE
    return os.path.join(base, "dalnice_save.json")

# ---------------------------------------------------------------- herní konstanty
MAX_SPEED = 25.0
MIN_SPEED = 6.5
KMH_FACTOR = 12
FPS = 60.0
DT_MS = 1000.0 / FPS

JUMP_FRAMES = int(0.6 * FPS)
JUMP_HEIGHT = 46
JUMP_COOLDOWN = int(15 * FPS)

POINTS_PER_COIN = 1000
REBEL_EVERY = 50
REBEL_JITTER = 5

SUPABASE_URL = "https://roerrxyzrvcjpawmtzzm.supabase.co"
SUPABASE_KEY = "sb_publishable_pmTCJRuEPq897NhGDhzgNQ__bJb-r6R"
SUPABASE_TABLE = "scores"
NET_TIMEOUT = 4

CARS = [
    {"name": "Kompakt",    "price": 0,  "acc": 1.00, "brk": 1.00},
    {"name": "Sedan GT",   "price": 10, "acc": 1.30, "brk": 1.20},
    {"name": "Kupe S",     "price": 20, "acc": 1.60, "brk": 1.45},
    {"name": "Sport RS",   "price": 30, "acc": 1.95, "brk": 1.75},
    {"name": "Supersport", "price": 40, "acc": 2.35, "brk": 2.10},
    {"name": "Hypercar X", "price": 50, "acc": 2.80, "brk": 2.50},
]

# druh, sirka, delka, min rychlost, max rychlost, cetnost, nazev sprite souboru (bez pripony)
VEHICLES = [
    ("hatch", 36, 66, 3.6, 5.6, 20, "sedan_hatch"),
    ("sedan", 40, 76, 2.8, 5.4, 26, "sedan_std"),
    ("suv",   46, 84, 3.0, 5.0, 14, "wagon_suv"),
    ("sport", 40, 72, 5.2, 6.2, 9,  "sedan_sport"),
    ("taxi",  40, 76, 3.0, 4.6, 6,  "taxi"),
    ("van",   44, 104, 2.4, 3.8, 9, "wagon_van"),
    ("truck", 48, 130, 2.0, 3.3, 8, "truck_box"),
    ("bus",   50, 164, 2.0, 2.9, 3, "bus_flat"),
]
VSPEC = {v[0]: v for v in VEHICLES}
HEAVY = ("truck", "bus")

SPRITE_VARIANTS = {
    "hatch": ["sedan_hatch_0", "sedan_hatch_1", "sedan_hatch_2", "sedan_hatch_3", "sedan_hatch_4"],
    "sedan": ["sedan_std_0", "sedan_std_1", "sedan_std_2", "sedan_std_3", "sedan_std_4"],
    "sport": ["sedan_sport_0", "sedan_sport_1", "sedan_sport_2", "sedan_sport_3", "sedan_sport_4"],
    "suv":   ["wagon_suv_0", "wagon_suv_1", "wagon_suv_2", "wagon_suv_3"],
    "van":   ["wagon_van_0", "wagon_van_1", "wagon_van_2", "wagon_van_3"],
    "truck": ["truck_box_0", "truck_box_1", "truck_box_2"],
    "bus":   ["bus_flat_0", "bus_flat_1", "bus_flat_2"],
    "taxi":  ["taxi_0"],
    "rebel": ["rebel_0"],
}

PRESETS = ["#e63946", "#f77f00", "#ffd60a", "#2ecc71", "#00b4d8", "#3a86ff", "#9d4edd", "#f1f1f1"]

MENU_BG = (0.11, 0.11, 0.17, 1)
PANEL = (0.07, 0.08, 0.12, 1)
ACCENT = "#4cc9f0"
GOLD = "#ffd166"
GREEN = "#2ecc71"
ORANGE = "#ff9f1c"
WHITE = "#ffffff"


def hex_to_rgba(h, a=1.0):
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    return (r, g, b, a)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- textury (nacitani + prebarveni)
_texture_cache = {}
_player_base_rgba = None
_player_cache = {}


def load_texture(name):
    tex = _texture_cache.get(name)
    if tex is not None:
        return tex
    path = os.path.join(ASSETS, "cars", name + ".png")
    if not os.path.exists(path):
        return None
    tex = CoreImage(path).texture
    _texture_cache[name] = tex
    return tex


def tree_textures():
    out = []
    for i in (1, 2, 3):
        p = os.path.join(ASSETS, "trees", f"tree{i}.png")
        if os.path.exists(p):
            out.append(CoreImage(p).texture)
    return out


def kind_textures(kind):
    names = SPRITE_VARIANTS.get(kind, [])
    out = [load_texture(n) for n in names]
    return [t for t in out if t is not None]


def player_texture(hex_color):
    """Prebarvi sablonu hracova auta na danou barvu (HSV remap, zachova stiny). Cachuje podle barvy."""
    global _player_base_rgba
    tex = _player_cache.get(hex_color)
    if tex is not None:
        return tex
    if not HAVE_PIL:
        return load_texture("player_template")
    if _player_base_rgba is None:
        path = os.path.join(ASSETS, "cars", "player_template.png")
        im = PILImage.open(path).convert("RGBA")
        _player_base_rgba = np.array(im).astype(np.float32)
    arr = _player_base_rgba
    r, g, b, a = arr[..., 0] / 255, arr[..., 1] / 255, arr[..., 2] / 255, arr[..., 3]
    rt, gt, bt = (int(hex_color[1:3], 16) / 255, int(hex_color[3:5], 16) / 255, int(hex_color[5:7], 16) / 255)
    th, ts, _tv = colorsys.rgb_to_hsv(rt, gt, bt)
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    v = maxc
    s = np.where(maxc > 0, (maxc - minc) / np.where(maxc == 0, 1, maxc), 0)
    mask = s > 0.12
    ns = np.clip(ts * (0.55 + 0.6 * s), 0, 1)
    # HSV -> RGB pro novy odstin/sytost, zachovana hodnota v
    i = int(th * 6) % 6
    f = th * 6 - int(th * 6)
    p = v * (1 - ns)
    q = v * (1 - f * ns)
    t = v * (1 - (1 - f) * ns)
    nr = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [v, q, p, p, t, v])
    ng = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [t, v, v, q, p, p])
    nb = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [p, p, t, v, v, q])
    out = np.stack([np.where(mask, nr, r), np.where(mask, ng, g), np.where(mask, nb, b)], axis=-1)
    out = np.clip(out * 255, 0, 255).astype(np.uint8)
    rgba = np.dstack([out, a.astype(np.uint8)])
    img = PILImage.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="png")
    buf.seek(0)
    tex = CoreImage(buf, ext="png").texture
    _player_cache[hex_color] = tex
    return tex


# ---------------------------------------------------------------- ucet (jednoduchy, lokalni)
def new_profile():
    return {"points": 0, "total": 0, "coins": 0, "owned": [0], "car": 0,
            "color": PRESETS[0], "best": 0, "games": 0, "name": ""}


def load_profile():
    try:
        with open(save_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        p = new_profile()
        for k in ("points", "total", "coins", "best", "games"):
            p[k] = max(0, int(d.get(k, 0)))
        owned = {int(i) for i in d.get("owned", [0]) if 0 <= int(i) < len(CARS)} | {0}
        p["owned"] = sorted(owned)
        car = int(d.get("car", 0))
        p["car"] = car if car in owned else 0
        col = str(d.get("color", p["color"]))
        p["color"] = col if len(col) == 7 and col.startswith("#") else p["color"]
        p["name"] = str(d.get("name", ""))[:12]
        return p
    except (FileNotFoundError, ValueError, OSError, TypeError, KeyError):
        return new_profile()


def save_profile(p):
    try:
        sp = save_path()
        tmp = sp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(p, f)
        os.replace(tmp, sp)
    except OSError:
        pass


# ---------------------------------------------------------------- online zebricek
class Job:
    def __init__(self, fn, *args):
        self.done = False
        self.result = None

        def run():
            try:
                self.result = fn(*args)
            except Exception:
                self.result = None
            self.done = True

        threading.Thread(target=run, daemon=True).start()


class OnlineLeaderboard:
    MAX = 10

    def __init__(self):
        self.entries = []
        self.error = ""

    def _request(self, method, query="", body=None):
        url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{SUPABASE_TABLE}{query}"
        headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
        if SUPABASE_KEY.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Prefer"] = "return=minimal"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    @staticmethod
    def _fmt_date(iso):
        try:
            return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d.%m.%Y")
        except (ValueError, AttributeError):
            return ""

    def load(self):
        try:
            rows = self._request("GET", f"?select=name,score,created_at&order=score.desc,created_at.asc&limit={self.MAX}")
            self.entries = [{"name": str(r["name"])[:12], "score": int(r["score"]),
                             "date": self._fmt_date(r.get("created_at", ""))} for r in rows]
            self.error = ""
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
            self.error = f"offline ({e})"

    def submit(self, name, score):
        name = name[:12]
        self.load()
        if self.error:
            return None
        if len(self.entries) >= self.MAX and score <= self.entries[-1]["score"]:
            return None
        rank = sum(1 for r in self.entries if r["score"] >= score)
        try:
            self._request("POST", body={"name": name, "score": int(score)})
        except (urllib.error.URLError, OSError, ValueError):
            return None
        self.entries.insert(rank, {"name": name, "score": int(score), "date": "dnes"})
        self.entries = self.entries[:self.MAX]
        return rank if rank < self.MAX else None


# ---------------------------------------------------------------- auta
class Car:
    def __init__(self, x, y, color, w=40, h=76):
        self.x, self.y = x, y
        self.w, self.h = w, h
        self.color = color

    def rect(self, shrink=4):
        return (self.x - self.w / 2 + shrink, self.y - self.h / 2 + shrink,
                self.x + self.w / 2 - shrink, self.y + self.h / 2 - shrink)


def vehicle_color(kind):
    palette = ["#d62828", "#f77f00", "#fcbf49", "#2a9d8f", "#8338ec",
               "#e5e5e5", "#ff006e", "#3a86ff", "#06d6a0", "#adb5bd"]
    if kind == "truck":
        return random.choice(["#5c677d", "#6c757d", "#3d5a80"])
    if kind == "bus":
        return random.choice(["#e76f51", "#2a9d8f", "#e9c46a"])
    if kind == "van":
        return random.choice(["#e9ecef", "#adb5bd", "#f1faee", "#90be6d"])
    if kind == "taxi":
        return "#f4d35e"
    if kind == "rebel":
        return "#101014"
    return random.choice(palette)


class Traffic(Car):
    def __init__(self, lane, y, kind, road_l, lane_w, speed=None):
        spec = VSPEC.get(kind, ("sedan", 40, 76, 2.8, 5.4, 26, "sedan_std"))
        _, w, h, vmin, vmax, _freq, _sprite = spec
        x = road_l + lane * lane_w + lane_w / 2
        super().__init__(x, y, vehicle_color(kind), w, h)
        self.kind = kind
        self.heavy = kind in HEAVY
        self.rebel = kind == "rebel"
        texs = kind_textures("rebel" if self.rebel else kind)
        self.texture = random.choice(texs) if texs else None
        self.lane = lane
        if speed is None:
            speed = random.uniform(vmin, vmax) if vmax > 0 else 0
        self.pref_speed = speed
        self.speed = speed
        self.near_given = False
        self.target_x = x
        self.pending = None
        self.signal = 0
        self.change_cd = random.randint(5, 40) if random.random() < 0.4 else random.randint(150, 400)


# ---------------------------------------------------------------- herni logika (bez vykreslovani)
class GameCore:
    """Vsechno herni stavy a pravidla - nezavisle na UI frameworku."""

    def __init__(self, road_l, road_r, lanes, player_y, h):
        self.set_layout(road_l, road_r, lanes, player_y, h)
        self.profile = load_profile()
        self.car_color = self.profile["color"]
        self.model = self.profile["car"]
        self.board = OnlineLeaderboard()
        self.load_job = Job(self.board.load)
        self.submit_job = None
        self.keys = set()
        self.name = self.profile.get("name") or "Hrac"
        self.reset()

    def set_layout(self, road_l, road_r, lanes, player_y, h):
        self.ROAD_L, self.ROAD_R = road_l, road_r
        self.LANES = lanes
        self.LANE_W = (road_r - road_l) / lanes
        self.PLAYER_Y = player_y
        self.H = h

    @property
    def car_def(self):
        return CARS[self.model]

    def reset(self):
        self.player = Car((self.ROAD_L + self.ROAD_R) / 2, self.PLAYER_Y, self.car_color)
        self.vx = 0.0
        self.tilt = 0.0
        self.base_speed = 7.0
        self.speed = 7.0
        self.distance = 0.0
        self.bonus = 0
        self.near_text = 0
        self.near_msg = ""
        self.traffic = []
        self.particles = []
        self.spawn_cd = 30
        self.scroll = 0.0
        self.same_lane_time = 0
        self.last_lane = None
        self.game_over = False
        self.paused = False
        self.last_rank = None
        self.tick = 0
        self.shake = 0
        self.spawned = 0
        self.next_rebel = REBEL_EVERY + random.randint(-REBEL_JITTER, REBEL_JITTER)
        self.rebel_warn = None
        self.banked = False
        self.run_points = 0
        self.run_coins = 0
        self.coins_seen = 0
        self.coin_flash = 0
        self.jump_t = 0
        self.jump_cd = 0

    def bank_run(self):
        if self.banked:
            return
        sc = int(self.score())
        p = self.profile
        p["points"] += sc
        p["total"] += sc
        earned = p["points"] // POINTS_PER_COIN
        p["coins"] += earned
        p["points"] %= POINTS_PER_COIN
        p["games"] += 1
        p["best"] = max(p["best"], sc)
        self.run_points, self.run_coins = sc, earned
        self.banked = True
        save_profile(p)

    def pool_now(self):
        return self.profile["points"] + int(self.score())

    def score(self):
        return self.distance + self.bonus

    def try_jump(self):
        if self.jump_cd > 0 or self.jump_t > 0 or self.game_over:
            return
        self.jump_t = JUMP_FRAMES
        self.jump_cd = JUMP_COOLDOWN
        p = self.player
        for _ in range(14):
            self.particles.append([p.x + random.uniform(-p.w / 2, p.w / 2), p.y + p.h / 2,
                                   random.uniform(-1.5, 1.5), random.uniform(0.5, 2.5),
                                   random.randint(10, 22), "#cccccc"])

    def jump_lift(self):
        if self.jump_t <= 0:
            return 0
        prog = 1 - self.jump_t / JUMP_FRAMES
        return math.sin(prog * math.pi) * JUMP_HEIGHT

    def player_lane(self):
        return int(clamp((self.player.x - self.ROAD_L) // self.LANE_W, 0, self.LANES - 1))

    def pick_kind(self):
        self.spawned += 1
        if self.spawned >= self.next_rebel and self.bottom_free():
            self.spawned = 0
            self.next_rebel = REBEL_EVERY + random.randint(-REBEL_JITTER, REBEL_JITTER)
            self.rebel_warn = 55
            return "rebel", None
        kinds = [v[0] for v in VEHICLES]
        weights = [v[5] for v in VEHICLES]
        return random.choices(kinds, weights=weights)[0], None

    def bottom_free(self):
        for _ in range(40):
            lane = random.randrange(self.LANES)
            ok = True
            for t in self.traffic:
                if t.lane == lane and t.y > self.H - 160:
                    ok = False
                    break
            if ok:
                return True
        return False

    def spawn(self):
        self.spawn_cd -= 1
        if self.spawn_cd > 0:
            return
        kind, _ = self.pick_kind()
        lanes_busy = {t.lane for t in self.traffic if t.y < 140}
        free = [l for l in range(self.LANES) if l not in lanes_busy]
        lane = random.choice(free) if free else random.randrange(self.LANES)
        if kind == "rebel":
            for _ in range(40):
                lane = random.randrange(self.LANES)
                if all(not (t.lane == lane and t.y > self.H - 160) for t in self.traffic):
                    break
            tr = Traffic(lane, self.H + 60, "rebel", self.ROAD_L, self.LANE_W, speed=self.speed + 2.4)
        else:
            tr = Traffic(lane, -80, kind, self.ROAD_L, self.LANE_W)
        self.traffic.append(tr)
        self.spawn_cd = random.randint(16, 42)

    def update_rebel(self):
        if self.rebel_warn:
            self.rebel_warn -= 1

    def lane_free(self, t, lane):
        for o in self.traffic:
            if o is t or o.lane != lane:
                continue
            if abs(o.y - t.y) < (o.h + t.h) / 2 + 18:
                return False
        return True

    def plan_change(self, t, gap):
        opts = []
        if t.lane > 0:
            opts.append(t.lane - 1)
        if t.lane < self.LANES - 1:
            opts.append(t.lane + 1)
        random.shuffle(opts)
        for lane in opts:
            if self.lane_free(t, lane):
                t.pending = lane
                t.signal = gap
                return True
        return False

    def update_player(self):
        k = self.keys
        steer = (1 if "right" in k else 0) - (1 if "left" in k else 0)
        max_vx = 5.5 + self.speed * 0.15
        self.vx += (steer * max_vx - self.vx) * 0.18
        p = self.player
        p.x += self.vx
        left, right = self.ROAD_L + p.w / 2, self.ROAD_R - p.w / 2
        if p.x < left or p.x > right:
            p.x = clamp(p.x, left, right)
            self.vx = 0
            self.speed = max(MIN_SPEED, self.speed - 0.3)
            self.shake = 4
            side = self.ROAD_L if p.x <= left else self.ROAD_R
            for _ in range(2):
                self.particles.append([side, p.y + random.randint(-30, 30),
                                       random.uniform(-2, 2), random.uniform(-3, 0), 12, "#ffd166"])
        self.tilt = self.vx * 2.2

        lane = self.player_lane()
        if lane == self.last_lane:
            self.same_lane_time += 1
        else:
            self.same_lane_time = 0
            self.last_lane = lane

        self.base_speed = min(15, 7 + self.tick / 800)
        cruise = self.base_speed + 2.2 * math.sin(self.tick / 170) + 1.3 * math.sin(self.tick / 61 + 1.7)
        cruise = max(MIN_SPEED + 0.5, cruise)
        acc, brk = self.car_def["acc"], self.car_def["brk"]
        if "down" in k:
            self.speed -= 0.22 * brk
        else:
            self.speed += clamp(cruise - self.speed, -0.1, 0.07 * acc)
        self.speed = clamp(self.speed, MIN_SPEED, MAX_SPEED)
        self.distance += self.speed * 0.12

    def update_traffic(self):
        p = self.player
        for t in self.traffic:
            desired = t.pref_speed
            if (t.lane == self.player_lane() and 0 < t.y - p.y < 260 and self.speed > t.speed
                    and not t.heavy and t.y > -40):
                self.plan_change(t, 20)
            if t.rebel:
                t.speed = desired
            else:
                t.speed += clamp(desired - t.speed, -0.15, 0.1)
            t.y += self.speed - t.speed

            if t.pending is not None:
                t.signal -= 1
                if t.signal <= 0:
                    if self.lane_free(t, t.pending):
                        t.lane = t.pending
                        t.target_x = self.ROAD_L + t.lane * self.LANE_W + self.LANE_W / 2
                    t.pending = None
                    t.change_cd = random.randint(120, 300)
            else:
                t.change_cd -= 1
                if t.change_cd <= 0 and not t.heavy and -60 < t.y < self.H * 0.7:
                    t.change_cd = 120 if self.plan_change(t, 10 if t.rebel else 22) else 40
            v = 3.2 if t.rebel else 1.3 + abs(self.speed - t.speed) * 0.12
            t.x += clamp(t.target_x - t.x, -v, v)

            ax1, ay1, ax2, ay2 = p.rect(5)
            bx1, by1, bx2, by2 = t.rect(5)
            if ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1 and self.jump_t <= 0:
                self.crash()
                return
            if not t.near_given and abs(t.y - p.y) < 40 and abs(t.x - p.x) < t.w / 2 + p.w / 2 + 16:
                t.near_given = True
                gain = 150 if t.rebel else 50
                self.bonus += gain
                self.near_text = 45
                self.near_msg = f"REBEL! +{gain}" if t.rebel else f"TESNE! +{gain}"
        self.traffic = [t for t in self.traffic
                        if t.y < self.H + 160 and not (t.rebel and t.y < -220)]

    def crash(self):
        self.game_over = True
        self.shake = 20
        p = self.player
        for _ in range(50):
            a = random.uniform(0, math.tau)
            s = random.uniform(1, 8)
            self.particles.append([p.x, p.y, math.cos(a) * s, math.sin(a) * s, random.randint(20, 50),
                                   random.choice(["#ff6b35", "#ffd166", "#ef233c", "#555555"])])
        self.bank_run()
        sc = int(self.score())
        self.last_rank = None
        if sc > 0:
            self.submit_job = Job(self.board.submit, self.name, sc)

    def update_particles(self):
        out = []
        for q in self.particles:
            q[0] += q[2]
            q[1] += q[3]
            q[4] -= 1
            if q[4] > 0:
                out.append(q)
        self.particles = out
        self.shake = max(0, self.shake - 1)

    def update(self):
        if self.paused or self.game_over:
            return
        self.tick += 1
        self.update_player()
        self.scroll = (self.scroll + self.speed) % 80
        self.spawn()
        self.update_rebel()
        self.update_traffic()
        self.update_particles()
        self.near_text = max(0, self.near_text - 1)
        self.coin_flash = max(0, self.coin_flash - 1)
        self.jump_t = max(0, self.jump_t - 1)
        self.jump_cd = max(0, self.jump_cd - 1)
        n = self.pool_now() // POINTS_PER_COIN
        if n > self.coins_seen:
            self.coins_seen = n
            self.coin_flash = 70

    def poll_jobs(self):
        if self.submit_job is not None and self.submit_job.done:
            self.last_rank = self.submit_job.result
            self.submit_job = None


# ---------------------------------------------------------------- UI (Kivy)
W0, H0 = 600, 720                 # logicke rozliseni, stejne jako desktop verze
ROAD_L0, ROAD_R0 = 100, 500
LANES = 4
PLAYER_Y0 = H0 - 130


def kv_color(hex_or_name, a=1.0):
    return hex_to_rgba(hex_or_name if hex_or_name.startswith("#") else hex_or_name, a)


GOLD_RGB = (0.95, 0.75, 0.18, 1)


def _shade(c, f):
    return (c[0] * f, c[1] * f, c[2] * f, c[3] if len(c) > 3 else 1)


class GlossyButton(Button):
    """Lesklé 3D tlačítko (zaoblené, přechod + odlesk nahoře) - vlastní styl místo plochého Kivy tlačítka."""

    def __init__(self, base_color=None, **kw):
        self.base_color = base_color or GOLD_RGB
        kw.setdefault("color", (0.22, 0.12, 0.02, 1))
        kw.setdefault("bold", True)
        super().__init__(**kw)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        with self.canvas.before:
            Color(0, 0, 0, 0.35)
            self._shadow = RoundedRectangle(radius=[14])
            Color(*self.base_color)
            self._base = RoundedRectangle(radius=[14])
            Color(*_shade(self.base_color, 0.62))
            self._dark = RoundedRectangle(radius=[0, 0, 14, 14])
            Color(1, 1, 1, 0.32)
            self._shine = RoundedRectangle(radius=[12])
            Color(*_shade(self.base_color, 0.4))
            self._border = Line(width=1.6)
        self.bind(pos=self._redraw, size=self._redraw, state=self._redraw)
        self._redraw()

    def _redraw(self, *_a):
        pressed = self.state == "down"
        off = 3 if pressed else 0
        x, y = self.pos
        w, h = self.size
        self._shadow.pos = (x, y - 3)
        self._shadow.size = (w, h)
        self._base.pos = (x, y - off)
        self._base.size = (w, h)
        self._dark.pos = (x, y - off)
        self._dark.size = (w, h * 0.5)
        self._shine.pos = (x + w * 0.07, y - off + h * 0.54)
        self._shine.size = (w * 0.86, h * 0.3)
        r = min(14, w / 2, h / 2)
        self._border.rounded_rectangle = (x, y - off, w, h, r)


class CtrlButton(GlossyButton):
    pass


class DalniceRoot(FloatLayout):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.core = GameCore(ROAD_L0, ROAD_R0, LANES, PLAYER_Y0, H0)
        self.mode = "title"
        self.sel_model = self.core.model
        self.overlay = FloatLayout(size_hint=(1, 1))
        self.add_widget(self.overlay)
        self.hud = Label(text="", font_size="16sp", halign="left", valign="top",
                         size_hint=(None, None), pos=(10, 10), color=(1, 1, 1, 1))
        self.add_widget(self.hud)
        self._build_controls()
        self.bind(size=self._on_resize, pos=self._on_resize)
        Window.bind(size=lambda *a: self._on_resize())
        self._on_resize()
        if self.core.profile.get("name"):
            self._show_title()
        else:
            self._show_name_entry()
        Clock.schedule_interval(self._tick, 1.0 / FPS)

    def _show_name_entry(self):
        self.mode = "name"
        self._clear_overlay()
        box = FloatLayout(size_hint=(1, 1))
        box.add_widget(Label(text="DALNICE", font_size="34sp", bold=True,
                             pos_hint={"center_x": 0.5, "top": 0.5}, size_hint=(1, 0.1)))
        box.add_widget(Label(text="Jak se jmenujes (pro zebricek)?", font_size="15sp",
                             pos_hint={"center_x": 0.5, "top": 0.38}, size_hint=(1, 0.06)))
        ti = TextInput(text="", multiline=False, font_size="18sp", halign="center",
                      size_hint=(0.7, 0.07), pos_hint={"center_x": 0.5, "top": 0.32},
                      write_tab=False)
        box.add_widget(ti)
        btn = GlossyButton(text="POKRACOVAT", font_size="16sp", base_color=kv_color(ACCENT), color=(1, 1, 1, 1),
                    pos_hint={"center_x": 0.5, "top": 0.22}, size_hint=(0.6, 0.07))

        def confirm(*_a):
            name = (ti.text or "Hrac").strip()[:12] or "Hrac"
            self.core.profile["name"] = name
            self.core.name = name
            save_profile(self.core.profile)
            self._show_title()

        btn.bind(on_release=confirm)
        ti.bind(on_text_validate=confirm)
        box.add_widget(btn)
        self.overlay.add_widget(box)
        self._layout_controls()
        self.hud.opacity = 0

    # ---------- rozlozeni ----------
    def _on_resize(self, *a):
        W = Window.width
        self.scale = W / W0
        self.vp_h = H0 * self.scale
        self.ctrl_h = max(0, Window.height - self.vp_h)

    def sx(self, gx):
        return gx * self.scale

    def sy(self, gy):
        return Window.height - gy * self.scale

    # ---------- dotykove ovladani ----------
    def _build_controls(self):
        self.btn_left = CtrlButton(text="<", font_size="28sp", size_hint=(None, None),
                                   base_color=(0.75, 0.78, 0.85, 1))
        self.btn_right = CtrlButton(text=">", font_size="28sp", size_hint=(None, None),
                                    base_color=(0.75, 0.78, 0.85, 1))
        self.btn_brake = CtrlButton(text="BRZDA", font_size="16sp", size_hint=(None, None),
                                    base_color=(0.85, 0.25, 0.22, 1), color=(1, 1, 1, 1))
        self.btn_jump = CtrlButton(text="SKOK", font_size="18sp", bold=True, size_hint=(None, None),
                                   base_color=(0.25, 0.55, 0.85, 1), color=(1, 1, 1, 1))
        for b in (self.btn_left, self.btn_right, self.btn_brake, self.btn_jump):
            b.opacity = 0
            b.disabled = True
            self.add_widget(b)
        self.btn_left.bind(on_press=lambda *_: self.core.keys.add("left"),
                          on_release=lambda *_: self.core.keys.discard("left"))
        self.btn_right.bind(on_press=lambda *_: self.core.keys.add("right"),
                           on_release=lambda *_: self.core.keys.discard("right"))
        self.btn_brake.bind(on_press=lambda *_: self.core.keys.add("down"),
                           on_release=lambda *_: self.core.keys.discard("down"))
        self.btn_jump.bind(on_press=lambda *_: self.core.try_jump())

    def _layout_controls(self):
        active = self.mode == "play"
        for b in (self.btn_left, self.btn_right, self.btn_brake, self.btn_jump):
            b.opacity = 1 if active else 0
            b.disabled = not active
        if not active:
            return
        ch = max(90, self.ctrl_h)
        pad = 14
        bw, bh = (Window.width - pad * 3) / 3, ch - pad * 2
        bh = min(bh, 120)
        y = pad
        self.btn_left.size = (bw * 0.55, bh)
        self.btn_left.pos = (pad, y)
        self.btn_right.size = (bw * 0.55, bh)
        self.btn_right.pos = (pad + bw * 0.55 + 8, y)
        self.btn_brake.size = (bw * 0.9, bh)
        self.btn_brake.pos = (Window.width / 2 - bw * 0.45, y)
        self.btn_jump.size = (bw * 0.95, bh)
        self.btn_jump.pos = (Window.width - pad - bw * 0.95, y)

    # ---------- prepinani obrazovek ----------
    def _clear_overlay(self):
        self.overlay.clear_widgets()

    def _show_title(self):
        self.mode = "title"
        self._clear_overlay()
        p = self.core.profile
        box = FloatLayout(size_hint=(1, 1))
        box.add_widget(Label(text="DALNICE", font_size="40sp", bold=True,
                             pos_hint={"center_x": 0.5, "top": 0.95}, size_hint=(1, 0.12)))
        box.add_widget(Label(text=f"Rekord: {p['best']}    Coinu: {p['coins']}", font_size="16sp",
                             color=kv_color(GOLD), pos_hint={"center_x": 0.5, "top": 0.80}, size_hint=(1, 0.06)))
        btn_name = Button(text=f"Hrac: {p.get('name') or 'Hrac'}  (zmenit)", font_size="12sp",
                         background_color=(0, 0, 0, 0), color=kv_color("#aaaaaa"),
                         pos_hint={"center_x": 0.5, "top": 0.86}, size_hint=(0.7, 0.04))
        btn_name.bind(on_release=lambda *_: self._show_name_entry())
        box.add_widget(btn_name)
        btn_play = GlossyButton(text="JET!", font_size="22sp", bold=True,
                          base_color=kv_color(ACCENT), color=(1, 1, 1, 1),
                          pos_hint={"center_x": 0.5, "top": 0.68}, size_hint=(0.6, 0.09))
        btn_play.bind(on_release=lambda *_: self._show_play())
        box.add_widget(btn_play)
        btn_garage = GlossyButton(text="GARAZ", font_size="18sp",
                            pos_hint={"center_x": 0.5, "top": 0.56}, size_hint=(0.6, 0.08))
        btn_garage.bind(on_release=lambda *_: self._show_garage())
        box.add_widget(btn_garage)
        btn_board = GlossyButton(text="ZEBRICEK", font_size="18sp",
                           pos_hint={"center_x": 0.5, "top": 0.46}, size_hint=(0.6, 0.08))
        btn_board.bind(on_release=lambda *_: self._show_board())
        box.add_widget(btn_board)
        swatches = FloatLayout(size_hint=(0.92, 0.12), pos_hint={"center_x": 0.5, "top": 0.34})
        n = len(PRESETS)
        for i, hx in enumerate(PRESETS):
            btn = Button(background_normal="", background_color=kv_color(hx),
                        size_hint=(1.0 / n * 0.85, 1), pos_hint={"x": i / n + 0.01, "center_y": 0.5})
            btn.bind(on_release=lambda _b, h=hx: self._pick_color(h))
            swatches.add_widget(btn)
        box.add_widget(swatches)
        box.add_widget(Label(text=f"Auto: {CARS[self.core.model]['name']}", font_size="14sp",
                             color=kv_color("#aaaaaa"), pos_hint={"center_x": 0.5, "top": 0.20}, size_hint=(1, 0.05)))
        self.overlay.add_widget(box)
        self._layout_controls()
        self.hud.opacity = 0

    def _pick_color(self, hex_color):
        self.core.car_color = hex_color
        self.core.profile["color"] = hex_color
        save_profile(self.core.profile)
        self._show_title()

    def _show_garage(self):
        self.mode = "garage"
        self._clear_overlay()
        p = self.core.profile
        box = FloatLayout(size_hint=(1, 1))
        box.add_widget(Label(text="GARAZ", font_size="30sp", bold=True,
                             pos_hint={"center_x": 0.5, "top": 0.97}, size_hint=(1, 0.08)))
        box.add_widget(Label(text=f"Coinu: {p['coins']}", font_size="16sp", color=kv_color(GOLD),
                             pos_hint={"center_x": 0.5, "top": 0.89}, size_hint=(1, 0.05)))
        grid = GridLayout(cols=2, spacing=10, padding=10, size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        for i, car in enumerate(CARS):
            owned = i in p["owned"]
            active = p["car"] == i
            label = f"{car['name']}\nzrych x{car['acc']:.2g}  brzda x{car['brk']:.2g}\n"
            if active:
                label += "VYBRANO"
            elif owned:
                label += "VLASTNIS (klepni)"
            else:
                label += f"{car['price']} coinu"
            tile_color = (0.3, 0.75, 0.35, 1) if active else \
                         ((0.55, 0.58, 0.62, 1) if owned else (0.35, 0.33, 0.3, 1))
            b = GlossyButton(text=label, font_size="13sp", size_hint_y=None, height=110,
                      base_color=tile_color, color=(1, 1, 1, 1))
            b.bind(on_release=lambda _b, j=i: self._garage_action(j))
            grid.add_widget(b)
        sv = ScrollView(size_hint=(1, 0.72), pos_hint={"center_x": 0.5, "top": 0.82})
        sv.add_widget(grid)
        box.add_widget(sv)
        btn_back = GlossyButton(text="ZPET", font_size="16sp", pos_hint={"center_x": 0.5, "y": 0.02},
                          size_hint=(0.5, 0.07))
        btn_back.bind(on_release=lambda *_: self._show_title())
        box.add_widget(btn_back)
        self.overlay.add_widget(box)
        self._layout_controls()

    def _garage_action(self, i):
        p = self.core.profile
        if i in p["owned"]:
            p["car"] = i
            self.core.model = i
        elif p["coins"] >= CARS[i]["price"]:
            p["coins"] -= CARS[i]["price"]
            p["owned"].append(i)
            p["car"] = i
            self.core.model = i
        save_profile(p)
        self._show_garage()

    def _show_board(self):
        self.mode = "board"
        self._clear_overlay()
        box = FloatLayout(size_hint=(1, 1))
        box.add_widget(Label(text="ZEBRICEK", font_size="28sp", bold=True,
                             pos_hint={"center_x": 0.5, "top": 0.95}, size_hint=(1, 0.08)))
        b = self.core.board
        lines = []
        if b.error:
            lines.append(f"[offline: {b.error}]")
        for i, r in enumerate(b.entries):
            lines.append(f"{i + 1}. {r['name']} - {r['score']}  ({r['date']})")
        box.add_widget(Label(text="\n".join(lines) or "nacitam...", font_size="15sp",
                             pos_hint={"center_x": 0.5, "top": 0.85}, size_hint=(0.9, 0.6), halign="left"))
        btn_back = GlossyButton(text="ZPET", font_size="16sp", pos_hint={"center_x": 0.5, "y": 0.04},
                          size_hint=(0.5, 0.07))
        btn_back.bind(on_release=lambda *_: self._show_title())
        box.add_widget(btn_back)
        self.overlay.add_widget(box)
        self._layout_controls()
        Job(self.core.board.load)

    def _show_play(self):
        self.mode = "play"
        self._clear_overlay()
        self.core.reset()
        self.core.car_color = self.core.profile["color"]
        self.core.model = self.core.profile["car"]
        self.hud.opacity = 1
        self._layout_controls()

    def _show_gameover(self):
        self.mode = "gameover"
        self._clear_overlay()
        box = FloatLayout(size_hint=(1, 1))
        box.add_widget(Label(text="NEHODA!", font_size="34sp", bold=True, color=kv_color("#ef233c"),
                             pos_hint={"center_x": 0.5, "top": 0.9}, size_hint=(1, 0.1)))
        box.add_widget(Label(text=f"Skore: {int(self.core.score())}  (+{self.core.run_coins} coinu)",
                             font_size="18sp", pos_hint={"center_x": 0.5, "top": 0.78}, size_hint=(1, 0.08)))
        btn_again = GlossyButton(text="JET ZNOVU", font_size="18sp", base_color=kv_color(ACCENT), color=(1, 1, 1, 1),
                          pos_hint={"center_x": 0.5, "top": 0.62}, size_hint=(0.6, 0.09))
        btn_again.bind(on_release=lambda *_: self._show_play())
        box.add_widget(btn_again)
        btn_menu = GlossyButton(text="MENU", font_size="16sp", pos_hint={"center_x": 0.5, "top": 0.50},
                         size_hint=(0.6, 0.08))
        btn_menu.bind(on_release=lambda *_: self._show_title())
        box.add_widget(btn_menu)
        self.overlay.add_widget(box)
        self._layout_controls()
        self.hud.opacity = 0

    # ---------- hlavni smycka ----------
    def _tick(self, dt):
        if self.mode == "play":
            self.core.update()
            self.core.poll_jobs()
            if self.core.game_over:
                self._show_gameover()
                return
            self._update_hud()
        self._draw()

    def _update_hud(self):
        c = self.core
        kmh = min(300, int(c.speed * KMH_FACTOR))
        coins_now = c.pool_now() // POINTS_PER_COIN
        txt = f"Skore: {int(c.score())}\n{kmh} km/h\nCoinu: {coins_now}"
        if c.rebel_warn:
            txt += "\nPOZOR, REBEL!"
        if c.near_text:
            txt += f"\n{c.near_msg}"
        if c.jump_cd <= 0:
            txt += "\nSKOK pripraven"
        self.hud.text = txt
        self.hud.pos = (14, Window.height - 110)
        self.hud.size = (300, 110)

    def _draw(self):
        c = self.core
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*MENU_BG)
            Rectangle(pos=(0, 0), size=(Window.width, Window.height))
            if self.mode != "play":
                return
            Color(0.17, 0.42, 0.31, 1)
            Rectangle(pos=(0, self.sy(c.H)), size=(Window.width, self.vp_h))
            Color(0.23, 0.23, 0.25, 1)
            Rectangle(pos=(self.sx(c.ROAD_L), self.sy(c.H)), size=(self.sx(c.ROAD_R - c.ROAD_L), self.vp_h))
            Color(1, 1, 1, 1)
            Line(points=[self.sx(c.ROAD_L) + 3, self.sy(0), self.sx(c.ROAD_L) + 3, self.sy(c.H)], width=2)
            Line(points=[self.sx(c.ROAD_R) - 3, self.sy(0), self.sx(c.ROAD_R) - 3, self.sy(c.H)], width=2)
            for lane in range(1, c.LANES):
                x = c.ROAD_L + lane * c.LANE_W
                yy = -80 + c.scroll
                while yy < c.H:
                    Color(0.94, 0.94, 0.94, 1)
                    Rectangle(pos=(self.sx(x) - 2, self.sy(yy + 40)), size=(4 * self.scale, 40 * self.scale))
                    yy += 80
            for t in c.traffic:
                self._draw_car(t, t.x, t.y, t.w, t.h, 0, False, 0)
            if not c.game_over:
                self._draw_car(c.player, c.player.x, c.player.y, c.player.w, c.player.h,
                              c.tilt, True, c.jump_lift())
            for q in c.particles:
                Color(*hex_to_rgba(q[5]))
                Rectangle(pos=(self.sx(q[0]), self.sy(q[1])), size=(4 * self.scale, 4 * self.scale))

    def _draw_car(self, car, x, y, w, h, tilt, player, lift):
        c = self.core
        sx, sy = self.sx(x), self.sy(y - lift)
        sw, sh = w * self.scale, h * self.scale
        if player:
            Color(*hex_to_rgba(ACCENT, 0.9))
            Ellipse(pos=(sx - sw * 0.5, self.sy(y) - sh * 0.56), size=(sw, sh * 0.16))
        if lift > 0:
            shade = max(0.25, 1 - lift / JUMP_HEIGHT)
            Color(0, 0, 0, 0.35 * shade)
            Ellipse(pos=(sx - sw * 0.35, self.sy(y) - sh * 0.12), size=(sw * 0.7, sh * 0.2))
        tex = (player_texture(c.car_color) if player else getattr(car, "texture", None))
        if tex is not None:
            Color(1, 1, 1, 1)
            Rectangle(texture=tex, pos=(sx - sw / 2, sy - sh / 2), size=(sw, sh))
        else:
            Color(*hex_to_rgba(car.color))
            Rectangle(pos=(sx - sw / 2, sy - sh / 2), size=(sw, sh))
        if getattr(car, "rebel", False):
            Color(1, 0, 0, 1)
            Ellipse(pos=(sx - 5, sy + sh / 2 - 4), size=(10, 10))


class DalniceApp(App):
    def build(self):
        Window.clearcolor = (0.05, 0.05, 0.08, 1)
        return DalniceRoot()


if __name__ == "__main__":
    DalniceApp().run()
