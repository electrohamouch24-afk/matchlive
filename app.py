import os
import time
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    """يسمح لأي صفحة ويب (مثل واجهتك الأمامية) بجلب البيانات من هذا الـ API."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# ==============================
# إعدادات football-data.org
# ==============================
FOOTBALL_DATA_TOKEN = os.environ.get(
    "FOOTBALL_DATA_TOKEN", "a716c5d5b9614ff89f550e0642fef54d"
)
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"

# البطولات الـ 12 المتاحة على الخطة المجانية لـ football-data.org
# (رمز كل بطولة + اسمها بالعربية) — محفوظة كما هي
COMPETITIONS = {
    "WC": "كأس العالم",
    "CL": "دوري أبطال أوروبا",
    "EC": "بطولة أمم أوروبا",
    "PL": "الدوري الإنجليزي الممتاز",
    "PD": "الدوري الإسباني",
    "SA": "الدوري الإيطالي",
    "BL1": "الدوري الألماني",
    "FL1": "الدوري الفرنسي",
    "PPL": "الدوري البرتغالي",
    "DED": "الدوري الهولندي",
    "ELC": "التشامبيونشيب الإنجليزي",
    "BSA": "الدوري البرازيلي",
}
COMPETITIONS_PARAM = ",".join(COMPETITIONS.keys())

STATUS_MAP = {
    "SCHEDULED": "NS",
    "TIMED": "NS",
    "IN_PLAY": "LIVE",
    "PAUSED": "HT",
    "FINISHED": "FT",
    "POSTPONED": "PST",
    "SUSPENDED": "SUSP",
    "CANCELLED": "CANC",
    "AWARDED": "AWD",
}

STATUS_LABELS_AR = {
    "NS": "لم تبدأ بعد",
    "LIVE": "مباشر الآن",
    "HT": "استراحة",
    "FT": "انتهت المباراة",
    "PST": "مؤجلة",
    "SUSP": "معلقة",
    "CANC": "ملغاة",
    "AWD": "محسومة",
}

# ==============================
# كاش بسيط في الذاكرة (In-memory cache)
# ==============================
_matches_cache = {"data": None, "timestamp": 0}
CACHE_TTL_SECONDS = 300  # 5 دقائق


def parse_utc_timestamp(utc_date_str):
    """يحوّل صيغة ISO8601 إلى unix timestamp بشكل آمن."""
    if not utc_date_str:
        return 0
    dt = datetime.fromisoformat(utc_date_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_major_matches_3_days():
    """
    يجلب مباريات أمس + اليوم + غداً لكل البطولات الكبرى المتاحة على الخطة
    المجانية لـ football-data.org، بطلب واحد فقط لتوفير حصة الاستخدام اليومية.
    """
    today_utc = datetime.now(timezone.utc).date()
    date_from = today_utc - timedelta(days=1)  # أمس
    date_to = today_utc + timedelta(days=2)    # بعد غد (dateTo غير شامل في API)

    headers = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}
    params = {
        "dateFrom": date_from.strftime("%Y-%m-%d"),
        "dateTo": date_to.strftime("%Y-%m-%d"),
        "competitions": COMPETITIONS_PARAM,
    }

    resp = requests.get(
        f"{FOOTBALL_DATA_BASE_URL}/matches",
        headers=headers,
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()

    matches = []
    for m in payload.get("matches", []):
        competition = m.get("competition", {}) or {}
        comp_code = competition.get("code")
        league_name = COMPETITIONS.get(comp_code, competition.get("name", comp_code))

        status_raw = m.get("status", "SCHEDULED")
        status_short = STATUS_MAP.get(status_raw, "NS")

        score = m.get("score", {}) or {}
        full_time = score.get("fullTime", {}) or {}

        home_team = m.get("homeTeam", {}) or {}
        away_team = m.get("awayTeam", {}) or {}

        utc_str = m.get("utcDate")
        matches.append({
            "id": m.get("id"),
            "date": utc_str,
            "timestamp": parse_utc_timestamp(utc_str),
            "status_short": status_short,
            "status_long": STATUS_LABELS_AR.get(status_short, status_raw),
            "elapsed": m.get("minute"),
            "home_team": home_team.get("name") or home_team.get("shortName"),
            "away_team": away_team.get("name") or away_team.get("shortName"),
            "home_logo": home_team.get("crest"),
            "away_logo": away_team.get("crest"),
            "home_score": full_time.get("home"),
            "away_score": full_time.get("away"),
            "league_id": comp_code,
            "league_name": league_name,
            "league_logo": competition.get("emblem"),
            "round": f"الجولة {m.get('matchday')}" if m.get("matchday") else m.get("stage"),
        })

    matches.sort(key=lambda x: x["timestamp"])
    return matches


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "message": "Matches summary API is running (football-data.org)",
        "endpoints": ["/api/matches", "/api/competitions"],
    })


@app.route("/api/competitions")
def api_competitions():
    """يرجع قائمة الأقسام / البطولات المتاحة مع أسمائها العربية."""
    return jsonify({
        "success": True,
        "competitions": [
            {"id": code, "name": name} for code, name in COMPETITIONS.items()
        ],
    })


@app.route("/api/matches")
def api_matches():
    """
    يرجع مباريات أمس + اليوم + غداً للبطولات الكبرى فقط.
    يدعم اختيارياً:
    - ?refresh=1 لإجبار تحديث الكاش
    - ?league=PL لفلترة بطولة معينة
    """
    now = time.time()
    force_refresh = request.args.get("refresh") == "1"
    league_filter = request.args.get("league", "").strip().upper()

    cache_is_fresh = (
        _matches_cache["data"] is not None
        and (now - _matches_cache["timestamp"]) < CACHE_TTL_SECONDS
    )

    data_source = None
    stale = False
    error_msg = None

    if not force_refresh and cache_is_fresh:
        data_source = _matches_cache["data"]
        cached = True
    else:
        try:
            fresh_matches = fetch_major_matches_3_days()
            _matches_cache["data"] = fresh_matches
            _matches_cache["timestamp"] = now
            data_source = fresh_matches
            cached = False
            print(
                f"[matches] fetched {len(fresh_matches)} major-league fixtures (yesterday/today/tomorrow)",
                flush=True,
            )
        except requests.exceptions.RequestException as e:
            if _matches_cache["data"] is not None:
                data_source = _matches_cache["data"]
                cached = True
                stale = True
                error_msg = str(e)
            else:
                return jsonify({
                    "success": False,
                    "error": str(e),
                    "matches": [],
                }), 502

    filtered_matches = (
        [m for m in data_source if m.get("league_id") == league_filter]
        if league_filter
        else data_source
    )

    response_payload = {
        "success": True,
        "cached": cached,
        "cached_at": _matches_cache["timestamp"],
        "count": len(filtered_matches),
        "matches": filtered_matches,
    }
    if stale:
        response_payload["stale"] = True
        response_payload["error"] = error_msg

    return jsonify(response_payload)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
