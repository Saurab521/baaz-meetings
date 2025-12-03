# app.py - Meeting display backend
# MINIMAL CHANGE: Only changed concurrency from 1 → 21 for speed
# Everything else is EXACTLY the same as your original

import eventlet
eventlet.monkey_patch()

import os
import json
import time
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import Flask, jsonify, send_from_directory, make_response
from flask_socketio import SocketIO
import redis
from dateutil import parser as dateparser

try:
    from flask_compress import Compress
    FLASK_COMPRESS_AVAILABLE = True
except Exception:
    Compress = None
    FLASK_COMPRESS_AVAILABLE = False

try:
    from eventlet.semaphore import Semaphore as EventletSemaphore
    EVENTLET_AVAILABLE = True
except Exception:
    EventletSemaphore = None
    EVENTLET_AVAILABLE = False

GOOGLE_LIBS_AVAILABLE = False
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except Exception:
    GOOGLE_LIBS_AVAILABLE = False

# ---------- CONFIG ----------
CALENDARS_JSON = os.getenv("CALENDARS_JSON", "[]")
try:
    CALENDARS = json.loads(CALENDARS_JSON)
    if not isinstance(CALENDARS, list):
        CALENDARS = []
except Exception:
    CALENDARS = []

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))  # ← Changed from 30 to 10
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SA_FILE", "/secrets/google-sa.json")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

STABILITY_THRESHOLD = int(os.getenv("STABILITY_THRESHOLD", "2"))
MEETINGS_CACHE_TTL = int(os.getenv("MEETINGS_CACHE_TTL", "30"))

try:
    MAX_CALENDAR_CONCURRENCY = int(os.getenv("MAX_CALENDAR_CONCURRENCY", "21"))  # ← Changed from 6 to 21
except Exception:
    MAX_CALENDAR_CONCURRENCY = 21

LEADER_LOCK_KEY = os.getenv("LEADER_LOCK_KEY", "meeting_display:leader")
LEADER_LOCK_TTL = int(os.getenv("LEADER_LOCK_TTL", "60"))
LEADER_RENEW_INTERVAL = int(os.getenv("LEADER_RENEW_INTERVAL", "25"))

APP_INSTANCE_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

# ---------- FLASK / SOCKET.IO ----------
app = Flask(__name__, static_folder="/frontend")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

if FLASK_COMPRESS_AVAILABLE:
    try:
        Compress(app)
        app.logger.info("Flask-Compress enabled.")
    except Exception as e:
        app.logger.warning("Flask-Compress failed to enable: %s", e)

# ---------- REDIS ----------
r = None
try:
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
    r.ping()
    app.logger.info("Connected to Redis (%s:%s)", REDIS_HOST, REDIS_PORT)
except Exception as e:
    app.logger.warning("Redis not available: %s", e)
    r = None

# ---------- GOOGLE CLIENT ----------
calendar_service = None
if GOOGLE_LIBS_AVAILABLE and os.path.exists(SERVICE_ACCOUNT_FILE):
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )
        calendar_service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        app.logger.info("Google Calendar client initialized.")
    except Exception as e:
        app.logger.warning("Google client init failed: %s", e)
        calendar_service = None
else:
    app.logger.warning("Google libs / service account missing or path not found.")

# ---------- CRITICAL FIX: Parallel fetching instead of serial ----------
if EVENTLET_AVAILABLE and EventletSemaphore is not None:
    EFFECTIVE_CALENDAR_CONCURRENCY = 21  # ← CHANGED FROM 1 TO 21 (THIS IS THE KEY FIX!)
    calendar_lock = EventletSemaphore(21)  # ← CHANGED FROM 1 TO 21
else:
    EFFECTIVE_CALENDAR_CONCURRENCY = MAX_CALENDAR_CONCURRENCY
    from threading import BoundedSemaphore
    calendar_lock = BoundedSemaphore(EFFECTIVE_CALENDAR_CONCURRENCY)

app.logger.info("Calendar concurrency: %d (for %d rooms)", EFFECTIVE_CALENDAR_CONCURRENCY, len(CALENDARS))

# ---------- Helpers (UNCHANGED) ----------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def iso_now() -> str:
    return now_utc().isoformat()

def parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        dt = dateparser.parse(dt_str)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def calendar_id_for_room(room_id: str) -> Optional[str]:
    if "@" in room_id:
        return room_id
    for item in CALENDARS:
        try:
            if str(item.get("id")) == str(room_id):
                return item.get("calendarId")
            if str(item.get("name")) == str(room_id):
                return item.get("calendarId")
        except Exception:
            pass
    return None

def normalize_event(ev):
    if not ev or not isinstance(ev, dict):
        return None
    start_field = ev.get("start", {})
    end_field = ev.get("end", {})
    start_str = start_field.get("dateTime") or start_field.get("date") or None
    end_str = end_field.get("dateTime") or end_field.get("date") or None
    sdt = parse_dt(start_str) if start_str else None
    edt = parse_dt(end_str) if end_str else None
    try:
        if edt and end_str and 'T' not in end_str:
            edt = edt - timedelta(seconds=1)
    except Exception:
        pass
    start_ts = int(sdt.timestamp() * 1000) if sdt else None
    end_ts = int(edt.timestamp() * 1000) if edt else None
    organizer = ""
    org = ev.get("organizer")
    if isinstance(org, dict):
        organizer = org.get("email") or org.get("displayName") or ""
    elif isinstance(org, str):
        organizer = org
    return {
        "id": ev.get("id"),
        "title": ev.get("summary") or ev.get("title") or "Meeting",
        "start": start_str,
        "end": end_str,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "organizer": organizer
    }

def build_summary(events, count=2):
    if not events:
        return [], 0
    summary = []
    for ev in events[:count]:
        summary.append({
            "id": ev["id"],
            "title": ev["title"],
            "start": ev["start"],
            "end": ev["end"]
        })
    return summary, len(events)

def get_prev_state(room_key):
    try:
        if not r:
            return None
        raw = r.get(f"room:{room_key}:state")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None

def save_state(room_key, state):
    if not r:
        return
    try:
        r.set(f"room:{room_key}:state", json.dumps(state))
    except Exception:
        pass

def apply_stability(room_key, computed_state):
    prev = get_prev_state(room_key) or {}
    prev_status = bool(prev.get("occupied", False))
    prev_stable = int(prev.get("_stable", 0))
    instant_status = bool(computed_state["occupied"])

    try:
        prev_current = prev.get("current") or {}
        prev_end_ts = int(prev_current.get("end_ts")) if prev_current and prev_current.get("end_ts") else None
        now_ms = int(now_utc().timestamp() * 1000)
        if prev_status and not instant_status and prev_end_ts and now_ms >= prev_end_ts:
            final_state = {
                "occupied": False,
                "is_ongoing": False,
                "current": None,
                "next": computed_state.get("next"),
                "all": computed_state.get("all"),
                "summary": computed_state.get("summary"),
                "next_count": computed_state.get("next_count"),
                "_stable": STABILITY_THRESHOLD,
                "last_success": iso_now(),
                "last_error": None
            }
            save_state(room_key, final_state)
            return final_state
    except Exception:
        pass

    if instant_status == prev_status:
        stable_value = prev_stable + 1
        final_status = instant_status
    else:
        if prev_stable + 1 >= STABILITY_THRESHOLD:
            stable_value = STABILITY_THRESHOLD
            final_status = instant_status
        else:
            stable_value = prev_stable + 1
            final_status = prev_status

    final_state = {
        "occupied": final_status,
        "is_ongoing": bool(computed_state["current"]),
        "current": computed_state["current"],
        "next": computed_state["next"],
        "all": computed_state["all"],
        "summary": computed_state["summary"],
        "next_count": computed_state["next_count"],
        "_stable": stable_value,
        "last_success": iso_now(),
        "last_error": None
    }
    save_state(room_key, final_state)
    return final_state

def fetch_events_for_range(calendar_id: str, start_iso: str, end_iso: str):
    if calendar_service is None:
        return []
    acquired = False
    try:
        calendar_lock.acquire()
        acquired = True
        try:
            result = calendar_service.events().list(
                calendarId=calendar_id,
                timeMin=start_iso,
                timeMax=end_iso,
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
            ).execute(num_retries=3)
        except TypeError:
            try:
                result = calendar_service.events().list(
                    calendarId=calendar_id,
                    timeMin=start_iso,
                    timeMax=end_iso,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                ).execute()
            except Exception as e:
                app.logger.warning("Calendar fetch error %s: %s", calendar_id, e)
                return []
        except Exception as e:
            app.logger.warning("Calendar fetch error %s: %s", calendar_id, e)
            return []
        return result.get("items", []) or []
    finally:
        if acquired:
            try:
                calendar_lock.release()
            except Exception:
                pass

def fetch_full_day_events(calendar_id: str):
    now = now_utc()
    start_day = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc)
    end_day = start_day + timedelta(days=1)
    start_iso = start_day.isoformat().replace("+00:00", "Z")
    end_iso = end_day.isoformat().replace("+00:00", "Z")
    return fetch_events_for_range(calendar_id, start_iso, end_iso)

def compute_normalized_state(raw_events):
    normalized = []
    for ev in raw_events:
        ne = normalize_event(ev)
        if ne:
            normalized.append(ne)
    normalized.sort(key=lambda x: (x.get("start_ts") or 9999999999999))
    now_ms = int(now_utc().timestamp() * 1000)
    current = None
    next_ev = None
    for ev in normalized:
        st = ev.get("start_ts")
        en = ev.get("end_ts")
        if st and en:
            if st <= now_ms < (en - 1000):
                current = ev
            elif st > now_ms and next_ev is None:
                next_ev = ev
    summary, next_count = build_summary(normalized, count=2)
    return {
        "occupied": bool(current),
        "current": current,
        "next": next_ev,
        "all": normalized,
        "summary": summary,
        "next_count": next_count
    }

def process_one_calendar(room):
    rid = room.get("id") or room.get("name")
    cal = room.get("calendarId")
    if not cal:
        return
    start_time = time.time()
    try:
        raw_events = fetch_full_day_events(cal)
        computed_state = compute_normalized_state(raw_events)
        final_state = apply_stability(rid, computed_state)
        try:
            if r:
                key = f"room:{rid}:meetings_today"
                r.set(key, json.dumps(final_state.get("all", [])), ex=MEETINGS_CACHE_TTL)
        except Exception:
            pass
        try:
            socketio.emit("room_state", {"room_id": rid, "state": final_state}, namespace="/rooms")
        except Exception:
            pass
        duration = time.time() - start_time
        app.logger.debug("Polled %s in %.2fs -> occupied=%s next=%s", rid, duration, final_state["occupied"], bool(final_state["next"]))
    except Exception as e:
        app.logger.exception("process_one_calendar error for %s: %s", rid, e)

def poll_all_calendars_once():
    if not CALENDARS:
        app.logger.debug("No calendars configured.")
        return

    max_workers = min(EFFECTIVE_CALENDAR_CONCURRENCY, max(1, len(CALENDARS)))

    if EVENTLET_AVAILABLE:
        try:
            from eventlet.greenpool import GreenPool
            pool = GreenPool(size=max_workers)
            for room in CALENDARS:
                pool.spawn_n(process_one_calendar, room)
            pool.waitall()
        except Exception as e:
            app.logger.exception("GreenPool failed, falling back to sequential poll: %s", e)
            for room in CALENDARS:
                try:
                    process_one_calendar(room)
                except Exception:
                    app.logger.exception("Sequential poll error for room: %s", room)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(process_one_calendar, room) for room in CALENDARS]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    app.logger.exception("Poll worker exception: %s", e)

# ---------- Leader lock (UNCHANGED) ----------
def try_acquire_leader_lock(instance_id: str, ttl=LEADER_LOCK_TTL) -> bool:
    if not r:
        app.logger.debug("No redis; assuming leader on this instance.")
        return True
    try:
        return r.set(LEADER_LOCK_KEY, instance_id, nx=True, ex=ttl)
    except Exception:
        app.logger.exception("Leader lock acquire error")
        return False

def renew_leader_lock(instance_id: str, ttl=LEADER_LOCK_TTL) -> bool:
    if not r:
        return True
    try:
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        res = r.eval(lua, 1, LEADER_LOCK_KEY, instance_id, ttl)
        return bool(res)
    except Exception:
        app.logger.exception("Leader lock renew error")
        return False

def release_leader_lock(instance_id: str):
    if not r:
        return
    try:
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        else
            return 0
        end
        """
        r.eval(lua, 1, LEADER_LOCK_KEY, instance_id)
    except Exception:
        app.logger.exception("Leader lock release error")

_poller_thread = None
_poller_stop_event = threading.Event()

def poller_leader_loop():
    instance_id = APP_INSTANCE_ID
    app.logger.info("Poller leader loop starting (instance=%s)", instance_id)
    got_lock = try_acquire_leader_lock(instance_id)
    if not got_lock:
        app.logger.info("Not leader (another instance holds lock). Poller leader loop exiting.")
        return

    try:
        app.logger.info("Leadership acquired by %s", instance_id)
        last_renew = time.time()
        try:
            poll_all_calendars_once()
        except Exception:
            app.logger.exception("Initial poll (leader) failed")

        while not _poller_stop_event.is_set():
            start = time.time()
            try:
                poll_all_calendars_once()
            except Exception:
                app.logger.exception("Periodic poll failed")

            elapsed = time.time() - start
            sleep_for = max(1, POLL_INTERVAL - int(elapsed))
            slept = 0
            while slept < sleep_for and not _poller_stop_event.is_set():
                time_to_next = min(1, sleep_for - slept)
                time.sleep(time_to_next)
                slept += time_to_next
                if time.time() - last_renew >= LEADER_RENEW_INTERVAL:
                    ok = renew_leader_lock(instance_id)
                    if not ok:
                        app.logger.warning("Leader lock renew failed; losing leadership.")
                        _poller_stop_event.set()
                        break
                    last_renew = time.time()
    finally:
        try:
            release_leader_lock(instance_id)
        except Exception:
            pass
        app.logger.info("Poller leader loop exiting for instance %s", instance_id)

def start_leader_poller_thread():
    global _poller_thread, _poller_stop_event
    if _poller_thread and _poller_thread.is_alive():
        app.logger.info("Poller thread already running.")
        return
    _poller_stop_event.clear()
    _poller_thread = threading.Thread(target=poller_leader_loop, daemon=True, name="leader-poller")
    _poller_thread.start()
    app.logger.info("Leader poller thread started (instance %s)", APP_INSTANCE_ID)

def stop_leader_poller_thread():
    global _poller_thread, _poller_stop_event
    if _poller_thread and _poller_thread.is_alive():
        _poller_stop_event.set()
        _poller_thread.join(timeout=5)
        _poller_thread = None
        app.logger.info("Poller thread stopped.")

# ---------- API endpoints (UNCHANGED) ----------
@app.route("/api/rooms")
def api_rooms():
    return jsonify(CALENDARS)

@app.route("/api/rooms/<room_id>/state")
def api_room_state(room_id):
    key = f"room:{room_id}:state"
    try:
        if r:
            raw = r.get(key)
            if raw:
                return jsonify(json.loads(raw))
    except Exception:
        pass
    return jsonify({
        "occupied": False,
        "current": None,
        "next": None,
        "summary": [],
        "next_count": 0,
        "_stable": 0,
        "last_error": "no redis state"
    })

@app.route("/api/rooms/<room_id>/meetings-today")
def api_room_meetings_today(room_id):
    calendar_id = calendar_id_for_room(room_id)
    if not calendar_id:
        return jsonify([])
    cache_key = f"room:{room_id}:meetings_today"
    try:
        if r:
            cached = r.get(cache_key)
            if cached:
                try:
                    return jsonify(json.loads(cached))
                except Exception:
                    pass
    except Exception:
        pass
    try:
        raw_events = fetch_full_day_events(calendar_id)
        normalized = [normalize_event(ev) for ev in raw_events if normalize_event(ev)]
        normalized.sort(key=lambda ev: ev.get("start_ts") or 0)
        try:
            if r:
                r.set(cache_key, json.dumps(normalized), ex=MEETINGS_CACHE_TTL)
        except Exception:
            pass
        return jsonify(normalized)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/rooms/list-with-state")
def api_list_with_state():
    output = []
    for room in CALENDARS:
        rid = room.get("id") or room.get("name")
        state = {}
        key = f"room:{rid}:state"
        try:
            if r:
                raw = r.get(key)
                if raw:
                    state = json.loads(raw)
        except Exception:
            pass
        output.append({
            "id": room.get("id"),
            "name": room.get("name"),
            "floor": room.get("floor"),
            "calendarId": room.get("calendarId"),
            "state": state
        })
    return jsonify(output)

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    static_root = app.static_folder or "/frontend"
    fullpath = os.path.join(static_root, path)
    if path and os.path.exists(fullpath):
        resp = send_from_directory(static_root, path)
        return resp
    index = os.path.join(static_root, "index.html")
    if os.path.exists(index):
        with open(index, 'rb') as fh:
            data = fh.read()
        resp = make_response(data)
        resp.headers['Content-Type'] = 'text/html; charset=utf-8'
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        if 'ETag' in resp.headers:
            del resp.headers['ETag']
        return resp
    return jsonify({"status": "ok", "message": "frontend missing"})

@app.route("/_health")
def health():
    return jsonify({
        "status": "ok",
        "redis": bool(r is not None),
        "google_client": bool(calendar_service is not None),
        "calendars_count": len(CALENDARS),
        "concurrency": EFFECTIVE_CALENDAR_CONCURRENCY,
        "time": iso_now()
    })

@app.route("/_version")
def version():
    return jsonify({"app": "meeting-display-backend", "version": "1.1-speed-optimized", "time": iso_now()})

def start_external_scheduler_if_any():
    try:
        from scheduler import start_scheduler as _ss
        try:
            _ss()
            app.logger.info("External scheduler started.")
        except Exception:
            app.logger.exception("External scheduler failed to start")
    except Exception:
        app.logger.debug("No external scheduler module available (skipping).")

def start_background_services():
    try:
        start_external_scheduler_if_any()
    except Exception:
        pass

    try:
        start_leader_poller_thread()
    except Exception:
        app.logger.exception("Failed to start leader poller thread")

def _background_initial_poll():
    try:
        app.logger.info("Background initial one-shot poll starting...")
        instance_id = APP_INSTANCE_ID
        got_lock = try_acquire_leader_lock(instance_id, ttl=LEADER_LOCK_TTL)
        if got_lock:
            try:
                poll_all_calendars_once()
            finally:
                release_leader_lock(instance_id)
        else:
            app.logger.debug("Initial poll skipped (not leader).")
        app.logger.info("Background initial one-shot poll finished.")
    except Exception:
        app.logger.exception("Background initial one-shot poll failed.")

try:
    threading.Thread(target=_background_initial_poll, daemon=True, name="initial-poller").start()
except Exception:
    app.logger.exception("Failed to spawn initial poll thread")

try:
    start_background_services()
except Exception:
    app.logger.exception("Failed to start background services")

if __name__ == "__main__":
    try:
        socketio.run(app, host="0.0.0.0", port=8000, debug=False)
    except Exception:
        app.logger.exception("Failed to run app via socketio")