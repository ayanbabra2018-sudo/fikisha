import os, uuid, json
from datetime import datetime, date, timedelta
from flask import Flask, request, render_template_string, redirect, make_response
import requests
from zoneinfo import ZoneInfo

app = Flask(__name__)
KAMPALA_TZ = ZoneInfo("Africa/Kampala")
def kampala_now(): return datetime.now(KAMPALA_TZ)
def today_str(): return kampala_now().strftime("%Y-%m-%d")
def time_str(): return kampala_now().strftime("%I:%M %p")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "admin123")

def h(): return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
def hr(): return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
def normalize_code(c): return (c or "").strip().upper()
def normalize_plate(p): return (p or "").strip().upper()

# --- DB HELPERS FOR UNLIMITED TABLES ---
def db_get_school(code):
    code=normalize_code(code)
    r=requests.get(f"{SUPABASE_URL}/rest/v1/schools?code=eq.{code}&select=*", headers=h(), timeout=10)
    return r.json()[0] if r.status_code==200 and r.json() else None

def db_get_vans(code):
    code=normalize_code(code)
    r=requests.get(f"{SUPABASE_URL}/rest/v1/vans?school_code=eq.{code}&select=*", headers=h(), timeout=10)
    return r.json() if r.status_code==200 else []

def db_get_kids(code, plate=None):
    code=normalize_code(code)
    url=f"{SUPABASE_URL}/rest/v1/kids?school_code=eq.{code}&select=*&order=name.asc"
    if plate: url+=f"&van_plate=eq.{normalize_plate(plate)}"
    r=requests.get(url, headers=h(), timeout=10)
    return r.json() if r.status_code==200 else []

def db_get_logs(code, plate=None, log_date=None, limit=100):
    code=normalize_code(code)
    url=f"{SUPABASE_URL}/rest/v1/attendance_log?school_code=eq.{code}&select=*&order=created_at.desc&limit={limit}"
    if plate: url+=f"&van_plate=eq.{normalize_plate(plate)}"
    if log_date: url+=f"&log_date=eq.{log_date}"
    r=requests.get(url, headers=h(), timeout=10)
    return r.json() if r.status_code==200 else []

#... PART 2 continues with routes
# --- MIGRATE ONCE ---
@app.route("/migrate_once")
def migrate_once():
    if request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD:
        return "Login at / as super admin first"
    # load old json from fikisha_store
    try:
        r=requests.get(f"{SUPABASE_URL}/rest/v1/fikisha_store?id=eq.1&select=data", headers=h(), timeout=10)
        old = r.json()[0]['data'] if r.json() else {"schools":{}}
    except: old={"schools":{}}
    count=0
    for code,s in (old.get("schools",{}) or {}).items():
        if not s: continue
        requests.post(f"{SUPABASE_URL}/rest/v1/schools", headers=hr(), json={"code": normalize_code(code), "name": s.get('name','School '+code), "director_phone": s.get('director_phone',''), "paid_until": s.get('paid_until','2026-12-31')}, timeout=10)
        for plate, van in (s.get('vans',{}) or {}).items():
            if not van: continue
            requests.post(f"{SUPABASE_URL}/rest/v1/vans", headers=hr(), json={"school_code": normalize_code(code), "plate": normalize_plate(plate), "driver_name": van.get('driver_name',''), "driver_phone": van.get('driver_phone','')}, timeout=10)
        for kid_id,kid in (s.get('kids',{}) or {}).items():
            if not kid: continue
            requests.post(f"{SUPABASE_URL}/rest/v1/kids", headers=hr(), json={"id": kid_id, "school_code": normalize_code(code), "name": kid.get('name'), "stage": kid.get('stage',''), "parent_phone": kid.get('parent_phone',''), "van_plate": normalize_plate(kid.get('van_plate')), "status": kid.get('status','At Home - waiting for van'), "times": kid.get('times',{}), "absent": kid.get('absent', False)}, timeout=10)
            count+=1
    return f"Migrated {count} kids. Now you can delete fikisha_store table."

@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        code=normalize_code(request.form.get("code"))
        role=request.form.get("role")
        plate=normalize_plate(request.form.get("plate",""))
        if role=="super" and request.form.get("password")==SUPER_ADMIN_PASSWORD:
            resp=make_response(redirect("/super"))
            resp.set_cookie("super_auth", SUPER_ADMIN_PASSWORD, max_age=86400*7)
            return resp
        school=db_get_school(code)
        if not school:
            return "Invalid School Code"
        if role=="admin":
            resp=make_response(redirect(f"/admin?code={code}"))
            resp.set_cookie("school_code", code, max_age=86400*7)
            resp.set_cookie("role", "admin", max_age=86400*7)
            return resp
        if role=="driver":
            vans=db_get_vans(code)
            if not any(v['plate']==plate for v in vans):
                return f"Van {plate} not found for {code}. Create it in admin first."
            resp=make_response(redirect(f"/driver?code={code}&plate={plate}"))
            resp.set_cookie("school_code", code, max_age=86400*7)
            resp.set_cookie("role", "driver", max_age=86400*7)
            resp.set_cookie("van_plate", plate, max_age=86400*7)
            return resp
    return render_template_string(LOGIN_HTML)

@app.route("/admin")
def admin_page():
    code=normalize_code(request.args.get("code") or request.cookies.get("school_code"))
    school=db_get_school(code)
    if not school: return redirect("/")
    vans=db_get_vans(code)
    kids=db_get_kids(code)
    logs=db_get_logs(code, limit=50)
    return render_template_string(ADMIN_HTML, school=school, code=code, vans=vans, kids=kids, logs=logs, today=today_str())

@app.route("/driver")
def driver_page():
    code=normalize_code(request.args.get("code") or request.cookies.get("school_code"))
    plate=normalize_plate(request.args.get("plate") or request.cookies.get("van_plate"))
    kids=db_get_kids(code, plate)
    logs=db_get_logs(code, plate, limit=30)
    return render_template_string(DRIVER_HTML, code=code, plate=plate, kids=kids, logs=logs)

@app.route("/super")
def super_page():
    if request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD:
        return redirect("/")
    r=requests.get(f"{SUPABASE_URL}/rest/v1/schools?select=*&order=created_at.desc", headers=h(), timeout=10)
    schools=r.json() if r.status_code==200 else []
    # count kids per school
    counts={}
    for s in schools:
        rc=requests.get(f"{SUPABASE_URL}/rest/v1/kids?school_code=eq.{s['code']}&select=id", headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Prefer":"count=exact"}, timeout=10)
        counts[s['code']] = rc.headers.get('Content-Range','').split('/')[-1] if 'Content-Range' in rc.headers else '?'
    return render_template_string(SUPER_HTML, schools=schools, counts=counts)
# --- API ACTIONS - UNLIMITED, ISOLATED BY school_code ---
@app.route("/api/add_van", methods=["POST"])
def add_van():
    code=normalize_code(request.form.get("code"))
    plate=normalize_plate(request.form.get("plate"))
    requests.post(f"{SUPABASE_URL}/rest/v1/vans", headers=hr(), json={"school_code": code, "plate": plate, "driver_name": request.form.get("driver_name"), "driver_phone": request.form.get("driver_phone")}, timeout=10)
    return redirect(f"/admin?code={code}")

@app.route("/api/add_kid", methods=["POST"])
def add_kid():
    code=normalize_code(request.form.get("code"))
    kid_id = str(uuid.uuid4())[:8].upper() # YOUR FIX 1 - UPPER
    requests.post(f"{SUPABASE_URL}/rest/v1/kids", headers=h(), json={"id": kid_id, "school_code": code, "name": request.form.get("name"), "stage": request.form.get("stage"), "parent_phone": request.form.get("parent_phone"), "van_plate": normalize_plate(request.form.get("van_plate")), "status": "At Home - waiting for van", "times": {}, "absent": False}, timeout=10)
    return redirect(f"/admin?code={code}")

@app.route("/api/action", methods=["POST"])
def action():
    kid_id=request.form.get("kid_id")
    act=request.form.get("act") # picked, dropped_school, dropped_home
    code=normalize_code(request.form.get("code"))
    r=requests.get(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{code}&select=*", headers=h(), timeout=10)
    if not r.json(): return "kid not found or wrong school", 403
    kid=r.json()[0]
    now=time_str()
    status_map={"picked":"In Van - going to school", "dropped_school":"At School", "dropped_home":"At Home - completed"}
    new_status=status_map.get(act, kid['status'])
    times=kid.get('times') or {}
    times[act]=now

    # YOUR FIX 2 - DON'T ERASE TIMESTAMP
    patch = {"status": new_status, "times": times}
    if act=="dropped_home":
        patch["dropped_home_ts"] = kampala_now().isoformat()

    requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}", headers=h(), json=patch, timeout=10)
    requests.post(f"{SUPABASE_URL}/rest/v1/attendance_log", headers=h(), json={"school_code": code, "log_date": today_str(), "kid_name": kid['name'], "kid_id": kid_id, "van_plate": kid['van_plate'], "action": act, "log_time": now}, timeout=10)

    plate=request.form.get("plate") or kid['van_plate']
    if request.form.get("from")=="admin":
        return redirect(f"/admin?code={code}")
    return redirect(f"/driver?code={code}&plate={plate}")

@app.route("/api/absent", methods=["POST"])
def absent():
    kid_id=request.form.get("kid_id"); code=normalize_code(request.form.get("code"))
    requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}", headers=h(), json={"absent": True, "status": "Absent today"}, timeout=10)
    return redirect(request.referrer or f"/admin?code={code}")

@app.route("/api/delete_kid", methods=["POST"])
def delete_kid():
    kid_id=request.form.get("kid_id"); code=normalize_code(request.form.get("code"))
    requests.delete(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{code}", headers=h(), timeout=10)
    return redirect(f"/admin?code={code}")

@app.route("/api/create_school", methods=["POST"])
def create_school():
    if request.cookies.get("super_auth")!=SUPER_ADMIN_PASSWORD: return "unauthorized", 403
    code=normalize_code(request.form.get("code"))
    requests.post(f"{SUPABASE_URL}/rest/v1/schools", headers=hr(), json={"code": code, "name": request.form.get("name"), "director_phone": request.form.get("director_phone"), "paid_until": request.form.get("paid_until")}, timeout=10)
    return redirect("/super")

@app.route("/logout")
def logout():
    resp=make_response(redirect("/"))
    resp.delete_cookie("school_code"); resp.delete_cookie("role"); resp.delete_cookie("van_plate"); resp.delete_cookie("super_auth")
    return resp

LOGIN_HTML="""<html><head><meta name=viewport content="width=device-width,initial-scale=1"><style>body{font-family:sans-serif;padding:20px;max-width:400px;margin:auto}input,select,button{width:100%;padding:12px;margin:6px 0;border-radius:8px;border:1px solid #ccc}button{background:#000;color:#fff}</style></head><body><h2>Fikisha Login</h2><form method=post><input name=code placeholder="School Code e.g. BRIGHT123" required><select name=role><option value=admin>Director/Admin</option><option value=driver>Driver</option><option value=super>Super Admin</option></select><input name=plate placeholder="Van Plate (driver only)"><input name=password type=password placeholder="Super password if super"><button>Login</button></form></body></html>"""

ADMIN_HTML="""<html><head><meta name=viewport content="width=device-width,initial-scale=1"><style>body{font-family:sans-serif;padding:12px}.card{border:1px solid #ddd;padding:12px;border-radius:10px;margin:8px 0}.kid{display:flex;justify-content:space-between} button{padding:8px 12px;border-radius:6px;border:none;background:#000;color:#fff}</style></head><body><h3>{{school.name}} ({{code}}) <a href=/logout>Logout</a></h3>
<h4>Add Van</h4><form action=/api/add_van method=post><input type=hidden name=code value={{code}}><input name=plate placeholder=UBA123X required><input name=driver_name placeholder="Driver Name"><input name=driver_phone placeholder=Driver Phone><button>Add Van</button></form>
<h4>Add Kid (Unlimited)</h4><form action=/api/add_kid method=post><input type=hidden name=code value={{code}}><input name=name placeholder="Kid Name" required><input name=stage placeholder=Class><input name=parent_phone placeholder=Parent Phone><select name=van_plate>{% for v in vans %}<option value={{v.plate}}>{{v.plate}} - {{v.driver_name}}</option>{% endfor %}</select><button>Add Kid</button></form>
<h4>Kids ({{kids|length}})</h4>{% for k in kids %}<div class=card><div class=kid><b>{{k.name}} ({{k.id}})</b> <span>{{k.van_plate}}</span></div><div>{{k.status}} {% if k.absent %}ABSENT{% endif %}</div><div><form action=/api/action method=post style=display:inline><input type=hidden name=kid_id value={{k.id}}><input type=hidden name=code value={{code}}><input type=hidden name=from value=admin><input type=hidden name=act value=dropped_home><button>Dropped Home</button></form> <form action=/api/delete_kid method=post style=display:inline><input type=hidden name=kid_id value={{k.id}}><input type=hidden name=code value={{code}}><button style=background:red>Delete</button></form></div></div>{% endfor %}
<h4>Today Logs - Only {{code}}</h4>{% for l in logs %}<div>{{l.log_time}} {{l.kid_name}} {{l.action}} {{l.van_plate}}</div>{% endfor %}</body></html>"""

DRIVER_HTML="""<html><head><meta name=viewport content="width=device-width,initial-scale=1"><style>body{font-family:sans-serif;padding:12px}.card{border:1px solid #ddd;padding:14px;border-radius:12px;margin:10px 0} button{width:100%;padding:14px;border-radius:10px;border:none;margin:4px 0}.pick{background:#000;color:#fff}.drop{background:#0a7}</style></head><body><h3>Driver {{plate}} - {{code}} <a href=/logout>Logout</a></h3>
{% for k in kids %}{% if not k.absent %}<div class=card><b>{{k.name}} ({{k.id}}) - {{k.stage}}</b><br>{{k.status}}<br>
<form action=/api/action method=post><input type=hidden name=kid_id value={{k.id}}><input type=hidden name=code value={{code}}><input type=hidden name=plate value={{plate}}><input type=hidden name=act value=picked><button class=pick>Picked</button></form>
<form action=/api/action method=post><input type=hidden name=kid_id value={{k.id}}><input type=hidden name=code value={{code}}><input type=hidden name=plate value={{plate}}><input type=hidden name=act value=dropped_school><button class=drop>Dropped at School</button></form>
<form action=/api/action method=post><input type=hidden name=kid_id value={{k.id}}><input type=hidden name=code value={{code}}><input type=hidden name=plate value={{plate}}><input type=hidden name=act value=dropped_home><button class=drop>Dropped Home</button></form>
<form action=/api/absent method=post><input type=hidden name=kid_id value={{k.id}}><input type=hidden name=code value={{code}}><button style=background:#eee>Absent Today</button></form>
</div>{% endif %}{% endfor %}
<h4>Log</h4>{% for l in logs %}<div>{{l.log_time}} {{l.kid_name}} {{l.action}}</div>{% endfor %}</body></html>"""

SUPER_HTML="""<html><head><meta name=viewport content="width=device-width,initial-scale=1"><style>body{font-family:sans-serif;padding:12px} table{width:100%;border-collapse:collapse} td,th{border:1px solid #ddd;padding:6px} </style></head><body><h3>Super Admin <a href=/logout>Logout</a></h3>
<form action=/api/create_school method=post><input name=code placeholder=CODE required><input name=name placeholder="School Name"><input name=director_phone placeholder=Director Phone><input name=paid_until type=date value=2026-12-31><button>Create School</button></form>
<table><tr><th>Code</th><th>Name</th><th>Kids</th><th>Paid Until</th></tr>{% for s in schools %}<tr><td>{{s.code}}</td><td>{{s.name}}</td><td>{{counts[s.code]}}</td><td>{{s.paid_until}}</td></tr>{% endfor %}</table>
<a href=/migrate_once><button>Migrate Old JSON Once</button></a>
</body></html>"""

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))