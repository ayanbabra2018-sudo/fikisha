from flask import Flask, request, redirect, jsonify, Response, make_response
import json, os, requests, csv, io, threading, shutil, uuid
from datetime import datetime, date, timedelta
try:
    from zoneinfo import ZoneInfo
    KAMPALA_TZ = ZoneInfo("Africa/Kampala")
except:
    KAMPALA_TZ = None

def kampala_now():
    if KAMPALA_TZ: return datetime.now(KAMPALA_TZ)
    return datetime.now() + timedelta(hours=3)
def normalize_plate(p): return str(p).upper().strip().replace(" ", "") if p else ""
def normalize_code(p): return str(p).upper().strip().replace(" ", "") if p else ""

app = Flask(__name__)
DB_LOCK = threading.Lock()
DB_FILE = "/data/fikisha_db.json" if os.path.exists("/data") else "fikisha_db.json"
BACKUP_FILE = DB_FILE + ".backup"
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "fikisha2026")

def load_db():
    with DB_LOCK:
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                url = f"{SUPABASE_URL}/rest/v1/fikisha_store?id=eq.1&select=data"
                headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    j = r.json()
                    if j and len(j)>0 and 'data' in j[0] and j[0]['data'] and "schools" in j[0]['data']:
                        return j[0]['data']
            except Exception as e: print(f"Supabase load failed: {e}")
        for path in [DB_FILE, BACKUP_FILE]:
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "schools" in data: return data
                except: pass
    return {"schools": {}}

def save_db(db):
    with DB_LOCK:
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                url = f"{SUPABASE_URL}/rest/v1/fikisha_store?id=eq.1"
                headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"}
                r = requests.patch(url, headers=headers, json={"data": db}, timeout=15)
                if r.status_code in [200,204]:
                    try:
                        with open(DB_FILE, 'w') as f: json.dump(db, f, indent=2)
                    except: pass
                    return True
            except Exception as e: print(f"Supabase save failed: {e}")
        try:
            tmp = DB_FILE + ".tmp"
            with open(tmp, 'w') as f: json.dump(db, f, indent=2)
            shutil.copyfile(tmp, BACKUP_FILE); os.replace(tmp, DB_FILE); return True
        except Exception as e: print(f"Local save failed: {e}"); return False

def to_wa(p):
    try:
        c = str(p).replace("+","").replace(" ","").replace("-","").strip()
        if c.startswith("0"): c = "256" + c[1:]
        return c
    except: return ""
def current_time_str(): return kampala_now().strftime("%I:%M %p")
def whatsapp_template(to, template_name, params=[]):
    token = os.environ.get("WHATSAPP_TOKEN"); phone_id = os.environ.get("WHATSAPP_PHONE_ID", "1327812003752192")
    if not token or not to: return False, "no token"
    clean = to_wa(to)
    if len(clean) < 10: return False, "bad phone"
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body_params = [{"type": "text", "text": str(p)[:100]} for p in params]
    data = {"messaging_product": "whatsapp","to": clean,"type": "template","template": {"name": template_name,"language": {"code": "en_US"},"components": [{"type": "body", "parameters": body_params}] if body_params else []}}
    try:
        r = requests.post(url, headers=headers, json=data, timeout=10); return r.status_code == 200, r.text
    except Exception as e: return False, str(e)
def whatsapp_async(to, template_name, params=[]): threading.Thread(target=whatsapp_template, args=(to, template_name, params), daemon=True).start()
def is_locked(school):
    try:
        if not school or 'paid_until' not in school: return True
        return str(school['paid_until']) < str(date.today())
    except: return True
def maybe_auto_reset(kid):
    try:
        ts = kid.get('dropped_home_ts')
        if not ts or 'dropped_home' not in kid.get('times', {}): return False
        dropped_time = datetime.fromisoformat(ts); now = kampala_now()
        if dropped_time.tzinfo is None: now_cmp = now.replace(tzinfo=None) if now.tzinfo else now
        else: now_cmp = now if now.tzinfo else now;
        if now_cmp.tzinfo is None and dropped_time.tzinfo: dropped_time = dropped_time.replace(tzinfo=None)
        diff = now_cmp - dropped_time
        if diff > timedelta(hours=2):
            kid['times'] = {}; kid['status'] = "At Home - waiting for van"; kid['absent'] = False; kid.pop('dropped_home_ts', None); return True
    except:
        try: kid['times'] = {}; kid['status'] = "At Home - waiting for van"; kid['absent'] = False; kid.pop('dropped_home_ts', None); return True
        except: pass
    return False
def get_van_pin(van):
    phone = to_wa(van.get('driver_phone',''));
    return phone[-4:] if len(phone)>=4 else "1234"

CSS = """<style>body{font-family:system-ui,Arial;background:#FFF8E1;margin:0;padding:15px;color:#1a1a1a}h2{color:#0D2A54;border-bottom:3px solid #FFC107;padding-bottom:8px}.card{background:white;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 4px 14px rgba(0,0,0,0.1);border-top:5px solid #FFC107}input,select{padding:11px;border-radius:10px;border:2px solid #FFC107;margin:6px;width:90%}button{padding:11px 20px;border-radius:10px;border:none;background:#0D2A54;color:#FFC107;font-weight:bold;cursor:pointer;margin:5px}.btn-done{background:#0a7a2a!important;color:white!important}.btn-red{background:#d32f2f;color:white}.btn-orange{background:#ef6c00;color:white}.btn-blue{background:#1565c0;color:white}.btn-grey{background:#888;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:700px){.grid{grid-template-columns:1fr}}.badge{padding:6px 12px;border-radius:20px;background:#0D2A54;color:#FFC107}.progress{height:10px;background:#eee;border-radius:10px;overflow:hidden}.progress-fill{height:100%;background:linear-gradient(90deg,#0a7a2a,#FFC107)}a{color:#1565c0;font-weight:bold;text-decoration:none}table{width:100%;border-collapse:collapse}th,td{padding:8px;text-align:left;border-bottom:1px solid #eee}@media print{button,.no-print{display:none}}</style>"""

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        pwd = request.form.get('password','')
        if pwd == SUPER_ADMIN_PASSWORD:
            resp = redirect("/"); resp.set_cookie("super_auth", SUPER_ADMIN_PASSWORD, max_age=86400*7, httponly=True, samesite='Lax'); return resp
        else: return CSS + "<div class='card' style='background:#ffcccc'><h2>Wrong password</h2><a href='/'>Try again</a></div>"
    auth = request.cookies.get("super_auth")
    if auth!= SUPER_ADMIN_PASSWORD:
        return CSS + """<div class='card' style='max-width:400px;margin:80px auto;text-align:center'><h2>🔐 FIKISHA Super Admin</h2><form method="post"><input name="password" type="password" placeholder="Password" required style="width:90%"><br><button style="width:95%;margin-top:10px">Unlock</button></form></div>"""
    db = load_db()
    html = CSS + f"<h2>FIKISHA - Super Admin (Kampala: {current_time_str()}) | {'Supabase ✅' if SUPABASE_URL else 'Local'}</h2>"
    html += """<div class="card"><h3>Create New School</h3><form method="post" action="/create_school"><input name="school_name" placeholder="Bright Angels" required><input name="code" placeholder="Code BRIGHT123" required><input name="director" placeholder="Director WhatsApp 2567..." required><input name="paid_until" type="date" required><button>Add School +</button></form></div><hr>"""
    for code, s in db.get("schools", {}).items():
        lock = "🔴 EXPIRED" if is_locked(s) else "🟢 Active"
        html += f"""<div class='card' style="{'border:3px solid red' if is_locked(s) else ''}"><b>{s['name']}</b> ({code}) - {lock} - Paid: {s.get('paid_until','?')} - Director: {s.get('director_phone','')}<br>
        <form method='post' action='/super/update_school/{code}' style='margin:10px 0;background:#FFF8E1;padding:10px;border-radius:10px'>
        <b>Edit School:</b><br>
        <input name='school_name' value='{s['name']}' required style='width:28%'>
        <input name='director' value='{s.get('director_phone','')}' required style='width:28%'>
        <input type='date' name='paid_until' value='{s.get('paid_until','')}' required style='width:28%'>
        <button style='padding:6px 12px'>Save Edit ✅</button>
        </form>
        <a href='/admin/{code}'>Manage</a> | <a href='/report/{code}'>Daily</a> | <a href='/report/{code}/weekly'>Weekly Absent</a> | <a href='/super/delete_school/{code}' onclick="return confirm('DELETE {code}?')" style='color:red'>Delete ❌</a><br><br>
        <button onclick="shareAdmin('{code}','{s['name']}','{s.get('director_phone','')}')" class="btn-blue">📲 Share Admin Link</button><br>"""
        for vp, van in s.get('vans', {}).items(): html += f"Van <b>{vp}</b> - {van.get('driver_name','')} ({van.get('driver_phone','')}) PIN:{get_van_pin(van)} - <a href='/driver/{code}/{vp}'>Driver Page</a><br>"
        html += "</div>"
    html += """<script>function shareAdmin(code,name,phone){let link=window.location.origin+"/admin/"+code;let msg="FIKISHA Admin link for "+name+" ("+code+"): "+link;let clean=phone.replace(/[^0-9]/g,'');window.open("https://wa.me/"+clean+"?text="+encodeURIComponent(msg),"_blank");}</script>"""
    return html

@app.route("/create_school", methods=["POST"])
def create_school():
    db = load_db(); code = normalize_code(request.form.get('code',''))
    if not code: return "Code required <a href='/'>back</a>"
    db['schools'][code] = {"name": request.form.get('school_name','School'),"code": code,"director_phone": request.form.get('director',''),"paid_until": request.form.get('paid_until', str(date.today())),"vans": {},"kids": {}, "attendance_log":[]}
    save_db(db); return redirect("/")

@app.route("/super/update_school/<code>", methods=["POST"])
def update_school(code):
    db = load_db(); code = normalize_code(code)
    if code in db['schools']:
        s = db['schools'][code]
        s['name'] = request.form.get('school_name', s['name'])
        s['director_phone'] = request.form.get('director', s.get('director_phone',''))
        s['paid_until'] = request.form.get('paid_until', s.get('paid_until', str(date.today())))
        save_db(db)
    return redirect("/")

@app.route("/super/update_paid/<code>", methods=["POST"])
def update_paid(code):
    db = load_db(); code = normalize_code(code)
    if code in db['schools']:
        db['schools'][code]['paid_until'] = request.form.get('paid_until', str(date.today())); save_db(db)
    return redirect("/")

@app.route("/super/delete_school/<code>")
def delete_school(code):
    db = load_db(); db['schools'].pop(normalize_code(code), None); save_db(db); return redirect("/")

ADMIN_HTML = CSS + """
{% if locked %}<div class='card' style='background:#ffcccc;border:3px solid red;text-align:center'><h1>🚫 PAYMENT EXPIRED</h1><p>Paid until {{school.paid_until}} | Today {{today}}</p><p>Data safe: {{total_kids}} kids.</p></div>{% endif %}
<h2>{{school.name}} Admin ({{code}}) - {% if locked %}<span style="color:red">EXPIRED</span>{% else %}Active until {{school.paid_until}}{% endif %}</h2>
<p>Kampala: {{kampala_time}} | {{db_file}} | <a href="/report/{{code}}">Daily</a> | <a href="/report/{{code}}/weekly">Weekly Absent 📊</a> | <a href="/">Super Admin</a></p>
<div class="card"><h3>Daily Control</h3>{% if locked %}<button disabled style="background:#ccc;width:100%">🔒 Locked</button>{% else %}<form method="post" action="/api/{{code}}/reset_all"><button style="background:#0a7a2a;width:100%">RESET ALL FOR TOMORROW</button></form>{% endif %}</div>
<div class="card"><h3>Add Van</h3>{% if locked %}<p style="color:red">🔒 Locked</p>{% else %}<form method="post" action="/admin/{{code}}/add_van"><input name="plate" placeholder="Plate UAA123A" required><input name="driver_name" placeholder="Driver Name" required><input name="driver_phone" placeholder="Driver Phone 2567..." required><button>Add Van</button></form>{% endif %}</div>
<div class="card"><h3>Add Kid to Van</h3>{% if locked %}<p style="color:red">🔒 Locked</p>{% elif school.vans|length==0 %}<p style="color:red">Add a Van first!</p>{% else %}<form method="post" action="/admin/{{code}}/add_kid"><input name="kid_name" placeholder="Kid Name" required><input name="stage" placeholder="Stage" required><input name="parent_phone" placeholder="Parent WhatsApp 2567..." required>Van: <select name="van_plate" required>{% for vp in school.vans %}<option value="{{vp}}">{{vp}}</option>{% endfor %}</select><button>Add Kid</button></form>{% endif %}</div>
<hr><h3>Vans & Kids ({{total_kids}})</h3>
{% for vp, van in school.vans.items() %}
<div class="card"><b>{{vp}} - {{van.driver_name}}</b> - {{van.driver_phone}} - PIN: {{van.pin}} - <a href="/driver/{{code}}/{{vp}}">Driver Page</a><br>
{% if not locked %}
<form method="post" action="/admin/{{code}}/edit_van/{{vp}}" style="background:#FFF8E1;padding:8px;border-radius:8px;margin:8px 0">
<input name="driver_name" value="{{van.driver_name}}" required style="width:30%"> <input name="driver_phone" value="{{van.driver_phone}}" required style="width:35%"> <button style="padding:6px 10px">Save Van Edit</button> | <a href="/admin/{{code}}/delete_van/{{vp}}" onclick="return confirm('Delete van {{vp}}?')" style="color:red">Delete Van ❌</a>
</form>
{% endif %}
{% for kid_id, kid in school.kids.items() if kid.van_plate==vp %}
<div style="margin:8px 0;padding:10px;background:#FFF8E1;border-radius:10px">
<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px">
<span>👦 <b>{{kid.name}}</b> ({{kid.stage}}) - {{kid.status[:30]}}<br>Parent: {{kid.parent_phone}} | <a href="/p/{{kid.id}}" target="_blank">Parent Link 👁️</a></span>
<div>
<button onclick="shareParent('{{kid.name}}','{{kid.id}}','{{kid.parent_phone}}')" class="btn-blue" style="padding:6px 10px;font-size:11px">📲 Share Parent Link</button>
{% if not locked %}
<a href="#" onclick="document.getElementById('edit-{{kid.id}}').style.display='block';return false;" style="font-size:11px">✏️ Edit</a> |
<a href="/admin/{{code}}/delete_kid/{{kid.id}}" onclick="return confirm('Delete {{kid.name}}?')" style="color:red;font-size:11px">❌ Delete</a>
{% endif %}
</div>
</div>
{% if not locked %}
<div id="edit-{{kid.id}}" style="display:none;background:white;padding:8px;border-radius:8px;margin-top:8px">
<form method="post" action="/admin/{{code}}/edit_kid/{{kid.id}}">
<input name="kid_name" value="{{kid.name}}" required style="width:22%"> <input name="stage" value="{{kid.stage}}" style="width:18%"> <input name="parent_phone" value="{{kid.parent_phone}}" required style="width:28%">
<select name="van_plate" style="width:18%">{% for vvp in school.vans %}<option value="{{vvp}}" {% if vvp==kid.van_plate %}selected{% endif %}>{{vvp}}</option>{% endfor %}</select>
<button style="padding:6px 10px">Save Kid</button> <button type="button" onclick="document.getElementById('edit-{{kid.id}}').style.display='none'" class="btn-grey" style="padding:6px">Cancel</button>
</form>
</div>
{% endif %}
</div>
{% endfor %}
</div>
{% endfor %}
<script>
function shareParent(name,kidId,phone){
 let link=window.location.origin+"/p/"+kidId;
 let msg="Hello, track "+name+" live on FIKISHA: "+link+" - You will also get WhatsApp alerts.";
 let clean=phone.replace(/[^0-9]/g,'');
 window.open("https://wa.me/"+clean+"?text="+encodeURIComponent(msg),"_blank");
}
</script>
"""

@app.route("/admin/<code>")
def admin(code):
    db = load_db(); code = normalize_code(code); school = db['schools'].get(code)
    if not school: return CSS + "<div class='card' style='background:#ffcccc'><h2>🚫 School Deleted</h2><a href='/'>Home</a></div>"
    for van in school['vans'].values(): van['pin']=get_van_pin(van)
    changed=False
    for kid in school['kids'].values():
        if maybe_auto_reset(kid): changed=True
    if changed: save_db(db)
    from jinja2 import Template
    return Template(ADMIN_HTML).render(school=school, code=code, locked=is_locked(school), kampala_time=current_time_str(), today=str(date.today()), total_kids=len(school['kids']), db_file="Supabase ✅" if SUPABASE_URL else "Local")

@app.route("/admin/<code>/add_van", methods=["POST"])
def add_van(code):
    db = load_db(); code = normalize_code(code); plate = normalize_plate(request.form.get('plate',''))
    if code not in db['schools']: return "School deleted"
    if is_locked(db['schools'][code]): return CSS + "<div class='card'><h2>🚫 EXPIRED</h2></div>"
    if not plate: return redirect(f"/admin/{code}")
    db['schools'][code]['vans'][plate] = {"plate": plate,"driver_name": request.form.get('driver_name','Driver'),"driver_phone": request.form.get('driver_phone','')}
    save_db(db); return redirect(f"/admin/{code}")

@app.route("/admin/<code>/edit_van/<plate>", methods=["POST"])
def edit_van(code, plate):
    db = load_db(); c=normalize_code(code); p=normalize_plate(plate)
    if c in db['schools'] and not is_locked(db['schools'][c]) and p in db['schools'][c]['vans']:
        db['schools'][c]['vans'][p]['driver_name']=request.form.get('driver_name','Driver')
        db['schools'][c]['vans'][p]['driver_phone']=request.form.get('driver_phone','')
        save_db(db)
    return redirect(f"/admin/{c}")

@app.route("/admin/<code>/delete_van/<plate>")
def delete_van_route(code, plate):
    db = load_db(); c=normalize_code(code); p=normalize_plate(plate)
    if c in db['schools'] and not is_locked(db['schools'][c]):
        db['schools'][c]['vans'].pop(p, None)
        to_del = [kid_id for kid_id, kid in db['schools'][c]['kids'].items() if kid.get('van_plate')==p]
        for kid_id in to_del: db['schools'][c]['kids'].pop(kid_id, None)
        save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/delete_kid/<kid_id>")
def delete_kid_route(code, kid_id):
    db = load_db(); c=normalize_code(code)
    if c in db['schools'] and not is_locked(db['schools'][c]):
        db['schools'][c]['kids'].pop(kid_id, None); save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/add_kid", methods=["POST"])
def add_kid_route(code):
    db = load_db(); kid_id = str(uuid.uuid4())[:8].upper(); code = normalize_code(code)
    if code not in db['schools']: return "School deleted"
    if is_locked(db['schools'][code]): return CSS + "<div class='card'><h2>🚫 EXPIRED</h2></div>"
    van_plate = normalize_plate(request.form.get('van_plate',''))
    if van_plate not in db['schools'][code]['vans']: return f"Van not found <a href='/admin/{code}'>Back</a>"
    db['schools'][code]['kids'][kid_id] = {"id": kid_id,"name": request.form.get('kid_name','Kid'),"stage": request.form.get('stage',''),"parent_phone": request.form.get('parent_phone',''),"van_plate": van_plate,"status": "At Home - waiting for van","times": {},"absent": False}
    if 'attendance_log' not in db['schools'][code]: db['schools'][code]['attendance_log']=[]
    save_db(db); return redirect(f"/admin/{code}")

@app.route("/admin/<code>/edit_kid/<kid_id>", methods=["POST"])
def edit_kid_route(code, kid_id):
    db = load_db(); c=normalize_code(code)
    if c in db['schools'] and not is_locked(db['schools'][c]) and kid_id in db['schools'][c]['kids']:
        kid = db['schools'][c]['kids'][kid_id]
        kid['name']=request.form.get('kid_name',kid['name'])
        kid['stage']=request.form.get('stage',kid.get('stage',''))
        kid['parent_phone']=request.form.get('parent_phone',kid.get('parent_phone',''))
        new_van = normalize_plate(request.form.get('van_plate',''))
        if new_van in db['schools'][c]['vans']: kid['van_plate']=new_van
        save_db(db)
    return redirect(f"/admin/{c}")

@app.route("/api/<code>/reset_all", methods=["POST"])
def reset_all(code):
    db = load_db(); code = normalize_code(code)
    if code not in db['schools']: return redirect("/")
    if is_locked(db['schools'][code]): return CSS + "<div class='card'><h2>🚫 EXPIRED</h2></div>"
    for kid in db['schools'][code]['kids'].values(): kid['times']={}; kid['status']="At Home - waiting for van"; kid['absent']=False; kid.pop('dropped_home_ts',None)
    save_db(db); return redirect(f"/admin/{code}")

# ---- DRIVER PIN ----
DRIVER_PIN_HTML = CSS + """<div class='card' style='max-width:400px;margin:80px auto;text-align:center'><h2>🔐 Driver PIN for Van {{plate}}</h2><p>Driver: {{van.driver_name}}<br>Enter PIN (last 4 digits of {{van.driver_phone}})</p><form method="post"><input name="pin" type="password" placeholder="4-digit PIN" required style="text-align:center;font-size:22px;letter-spacing:8px" maxlength="4"><br><button style="width:95%">Unlock Driver App</button></form><p style="font-size:12px;color:#666">Ask director for PIN</p></div>"""

DRIVER_HTML = CSS + """<link rel="manifest" href="/manifest.json">
{% if locked %}<div class='card' style='background:#ffcccc;border:3px solid red;text-align:center'><h1>🚫 PAYMENT EXPIRED</h1><p>Paid until {{school.paid_until}}</p><p>Driver locked.</p></div>{% endif %}
<h2>Driver: {{van.driver_name}} - Van {{van.plate}} - {{school.name}} - <a href="/driver/{{code}}/{{van.plate}}/logout" style="font-size:12px">Logout PIN</a></h2>
<div id="netStatus" style="padding:8px;border-radius:8px;text-align:center;font-weight:bold">Checking...</div>
<p>Code: {{code}} | {{kampala_time}} | {{today}} | {{kids|length}} kids</p>
<div style="display:flex;gap:12px;justify-content:space-between;flex-wrap:nowrap">
  <div class="card" style="flex:1;min-width:0;border:2px solid #0a7a2a;background:#e8f5e9;margin:0">
    <h3 style="color:#0a7a2a;margin-top:0;font-size:13px;text-align:center">🏫 Quick Drop</h3>
    <button class="btn-done" onclick="massDrop('dropped_school')" {% if locked %}disabled{% endif %} style="width:100%;font-size:13px;padding:12px;border-radius:12px">🏫 DROP ALL AT SCHOOL</button>
  </div>
  <div style="width:12px;flex-shrink:0"></div>
  <div class="card" style="flex:1;min-width:0;border:2px solid #d32f2f;margin:0">
    <h3 style="color:#d32f2f;margin-top:0;font-size:13px;text-align:center">🚨 Alert All</h3>
    <select id="trafficReason" {% if locked %}disabled{% endif %} style="width:100%;padding:8px;border:2px solid #d32f2f;font-size:11px"><option value="Heavy traffic - 15 mins late">Traffic - 15 mins late</option><option value="Heavy traffic - 30 mins late">Traffic - 30 mins late</option><option value="Tyre puncture - fixing, 20 mins delay">Puncture - 20 mins</option><option value="Fuel stop - 10 mins delay">Fuel - 10 mins</option><option value="custom">✏️ Custom</option></select>
    <input id="trafficCustom" placeholder="Custom 80 chars" style="width:95%;display:none;margin-top:6px" maxlength="80">
    <button class="btn-red" onclick="sendTraffic()" {% if locked %}disabled{% endif %} style="width:100%;margin-top:8px;padding:10px;font-size:12px">🚨 SEND ALERT</button>
  </div>
</div>
<hr><div class="grid">{% for kid_id, kid in kids.items() %}<div class="card"><b>{{kid.name}}</b> - {{kid.stage}} - {{kid.parent_phone}}<br>Status: <span class="badge">{{kid.status}}</span><div class="progress"><div class="progress-fill" style="width: {{kid.progress}}%"></div></div><br><button onclick="action('{{kid.id}}','picked_home')" {% if locked %}disabled{% endif %}>PICKED HOME</button><button onclick="action('{{kid.id}}','dropped_school')" {% if locked %}disabled{% endif %}>DROPPED SCHOOL</button><button onclick="action('{{kid.id}}','picked_school')" {% if locked %}disabled{% endif %}>PICKED SCHOOL</button><button onclick="action('{{kid.id}}','dropped_home')" {% if locked %}disabled{% endif %}>DROPPED HOME</button><br><button class="btn-orange" onclick="action('{{kid.id}}','absent')" {% if locked %}disabled{% endif %}>ABSENT</button><button class="btn-grey" onclick="action('{{kid.id}}','present')" {% if locked %}disabled{% endif %}>BACK</button></div>{% endfor %}</div>
<script>
if('serviceWorker' in navigator){ navigator.serviceWorker.register('/sw.js').catch(()=>{}); }
let queue = JSON.parse(localStorage.getItem('fikisha_queue_{{van.plate}}')||'[]');
if(queue.length>100){ queue=queue.slice(-100); localStorage.setItem('fikisha_queue_{{van.plate}}', JSON.stringify(queue)); }
function updateNet(){
  let el=document.getElementById('netStatus');
  if(navigator.onLine){ el.innerText='✅ ONLINE | Queued: '+queue.length; el.style.background='#e8f5e9'; if(queue.length>0) syncQueue(); }
  else { el.innerText='⚠️ OFFLINE - saved | Queued: '+queue.length; el.style.background='#fff3cd'; }
}
window.addEventListener('online', updateNet); window.addEventListener('offline', updateNet); updateNet();
function saveQueue(){ if(queue.length>100) queue=queue.slice(-100); localStorage.setItem('fikisha_queue_{{van.plate}}', JSON.stringify(queue)); updateNet(); }
function action(kid_id, act){
  {% if locked %} alert('🚫 Payment expired'); return; {% endif %}
  queue.push({kid_id, action:act, time: new Date().toISOString()}); saveQueue();
  if(navigator.onLine){
    fetch('/api/{{code}}/{{van.plate}}/action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({kid_id, action:act})})
.then(r=>r.json()).then(j=>{ if(j.ok){ queue=queue.filter(q=>!(q.kid_id==kid_id && q.action==act)); saveQueue(); location.reload(); } else{ alert(j.error||'Locked'); location.reload(); } }).catch(()=>{ alert('Saved offline'); location.reload(); });
  } else { alert('⚠️ Offline saved!'); location.reload(); }
}
function syncQueue(){ if(queue.length==0 ||!navigator.onLine) return; fetch('/api/{{code}}/{{van.plate}}/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({items: queue})}).then(r=>r.json()).then(j=>{ if(j.ok){ queue=[]; saveQueue(); } }); }
function massDrop(act){ {% if locked %} alert('🚫 Locked'); return; {% endif %} if(!confirm('DROP ALL at school?')) return; if(!navigator.onLine){ alert('Mass drop needs online'); return; } fetch('/api/{{code}}/{{van.plate}}/mass', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:act})}).then(r=>r.json()).then(j=>{ alert('✅ Dropped '+j.count); location.reload(); }); }
function sendTraffic(){ {% if locked %} alert('🚫 Locked'); return; {% endif %} let re=document.getElementById('trafficReason');let ce=document.getElementById('trafficCustom');let msg=re.value; if(msg=='custom'){msg=ce.value.trim();}if(!msg){alert('Choose reason!');return;} if(!navigator.onLine){ alert('Alert needs network'); return; } fetch('/api/{{code}}/{{van.plate}}/traffic', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})}).then(r=>r.json()).then(j=>{ alert('✅ Sent '+j.count); }); }
document.getElementById('trafficReason').addEventListener('change', function(){ let c=document.getElementById('trafficCustom'); if(this.value=='custom'){c.style.display='block';}else{c.style.display='none';} });
</script>"""

@app.route("/driver/<code>/<plate>", methods=["GET", "POST"])
def driver_page(code, plate):
    try:
        db = load_db(); code = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db['schools'].get(code)
        if not school: return CSS + "<div class='card' style='background:#ffcccc'><h2>🚫 School Deleted</h2></div>"
        van = school['vans'].get(plate_norm)
        if not van: return CSS + f"<div class='card'><h2>Van {plate_norm} Deleted</h2><a href='/admin/{code}'>Admin</a></div>"
        real_pin = get_van_pin(van)
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}")
        if request.method == "POST":
            entered = request.form.get('pin','').strip()
            if entered == real_pin:
                resp = make_response(redirect(f"/driver/{code}/{plate_norm}"))
                resp.set_cookie(f"driver_pin_{plate_norm}", real_pin, max_age=86400*30, httponly=True, samesite='Lax')
                return resp
            else:
                from jinja2 import Template
                return Template(CSS + DRIVER_PIN_HTML + "<p style='color:red;text-align:center'>Wrong PIN!</p>").render(van=van, plate=plate_norm)
        if cookie_pin!= real_pin:
            from jinja2 import Template
            return Template(CSS + DRIVER_PIN_HTML).render(van=van, plate=plate_norm)
        locked = is_locked(school); changed=False
        for k in school['kids'].values():
            if k['van_plate']==plate_norm and maybe_auto_reset(k): changed=True
        if changed: save_db(db)
        kids = {k:v for k,v in school['kids'].items() if v['van_plate']==plate_norm}
        for kid in kids.values():
            cnt=0
            if 'picked_home' in kid.get('times',{}): cnt=25
            if 'dropped_school' in kid.get('times',{}): cnt=50
            if 'picked_school' in kid.get('times',{}): cnt=75
            if 'dropped_home' in kid.get('times',{}): cnt=100
            kid['progress']=cnt
        from jinja2 import Template
        return Template(DRIVER_HTML).render(school=school, van=van, code=code, kids=kids, locked=locked, today=str(date.today()), kampala_time=current_time_str())
    except Exception as e:
        return f"Driver error: {e} <a href='/'>home</a>"

@app.route("/driver/<code>/<plate>/logout")
def driver_logout(code, plate):
    resp = redirect(f"/driver/{code}/{plate}")
    resp.set_cookie(f"driver_pin_{normalize_plate(plate)}", "", max_age=0)
    return resp

@app.route("/api/<code>/<plate>/action", methods=["POST"])
def driver_action(code, plate):
    try:
        db = load_db(); code = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db['schools'].get(code)
        if not school: return jsonify({"ok": False, "error":"school deleted"}), 404
        if is_locked(school): return jsonify({"ok": False, "locked": True, "error":"Payment expired"}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); van = school['vans'].get(plate_norm)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False, "error":"PIN required"}), 401
        data = request.get_json() or {}; kid_id = data.get('kid_id'); act = data.get('action')
        if not kid_id or not act: return jsonify({"ok": False}), 400
        if kid_id not in school['kids']: return jsonify({"ok": False, "error":"kid deleted"}), 404
        kid = school['kids'][kid_id]; short = kampala_now().strftime("%I:%M %p"); full = kampala_now().strftime("%I:%M %p %d %b")
        log = school.get('attendance_log', [])
        if act == 'picked_home':
            kid['status']=f"On way to School - picked at {short}"; kid['times']['picked_home']=full; kid['absent']=False
            whatsapp_async(kid['parent_phone'], "picked_home", [kid['name'], short, plate_norm]); log.append({"date": str(date.today()), "kid": kid['name'], "action": "picked_home", "time": full, "van": plate_norm})
        elif act == 'dropped_school':
            kid['status']=f"At School - arrived at {short}"; kid['times']['dropped_school']=full
            whatsapp_async(kid['parent_phone'], "dropped_school", [kid['name'], short]); log.append({"date": str(date.today()), "kid": kid['name'], "action": "dropped_school", "time": full, "van": plate_norm})
        elif act == 'picked_school':
            kid['status']=f"On way Home - left at {short}"; kid['times']['picked_school']=full
            whatsapp_async(kid['parent_phone'], "picked_school", [kid['name'], short, plate_norm]); log.append({"date": str(date.today()), "kid": kid['name'], "action": "picked_school", "time": full, "van": plate_norm})
        elif act == 'dropped_home':
            kid['status']=f"Home Safe - {short}"; kid['times']['dropped_home']=full; kid['dropped_home_ts']=kampala_now().isoformat()
            whatsapp_async(kid['parent_phone'], "dropped_home", [kid['name'], short]); log.append({"date": str(date.today()), "kid": kid['name'], "action": "dropped_home", "time": full, "van": plate_norm})
        elif act == 'absent':
            kid['status']="ABSENT Today"; kid['absent']=True
            whatsapp_async(kid['parent_phone'], "absent", [kid['name'], str(date.today()), plate_norm]); log.append({"date": str(date.today()), "kid": kid['name'], "action": "ABSENT", "time": full, "van": plate_norm})
        elif act == 'present':
            kid['absent']=False; kid['status']="At Home - waiting for van"; kid['times']={}; kid.pop('dropped_home_ts',None); log.append({"date": str(date.today()), "kid": kid['name'], "action": "PRESENT", "time": full, "van": plate_norm})
        else: return jsonify({"ok": False}), 400
        school['attendance_log']=log[-500:]; save_db(db); return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/mass", methods=["POST"])
def mass_action(code, plate):
    try:
        db = load_db(); code = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db['schools'].get(code)
        if not school or is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); van = school['vans'].get(plate_norm)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False, "error":"PIN"}), 401
        data = request.get_json() or {}; act = data.get('action',''); short = kampala_now().strftime("%I:%M %p"); full = kampala_now().strftime("%I:%M %p %d %b"); count = 0; log=school.get('attendance_log',[])
        if act == 'dropped_school':
            for kid in [k for k in school['kids'].values() if k.get('van_plate')==plate_norm and not k.get('absent')]:
                if 'dropped_school' in kid.get('times',{}): continue
                kid['status']=f"At School - arrived at {short}"; kid['times']['dropped_school']=full
                whatsapp_async(kid['parent_phone'], "dropped_school", [kid['name'], short]); count+=1; log.append({"date": str(date.today()), "kid": kid['name'], "action": "dropped_school (mass)", "time": full, "van": plate_norm})
            school['attendance_log']=log[-500:]; save_db(db); return jsonify({"ok": True, "count": count})
        return jsonify({"ok": False}), 400
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/traffic", methods=["POST"])
def traffic(code, plate):
    try:
        db = load_db(); code = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db['schools'].get(code)
        if not school or is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); van = school['vans'].get(plate_norm)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False, "error":"PIN"}), 401
        raw_msg = (request.get_json() or {}).get('message','').strip()
        if not raw_msg or len(raw_msg) < 5: return jsonify({"ok": False}), 400
        clean_msg = raw_msg[:100].strip(); sent = 0
        for kid in [k for k in school['kids'].values() if k.get('van_plate')==plate_norm and not k.get('absent')]:
            whatsapp_async(kid['parent_phone'], "traffic_alert", [plate_norm, clean_msg]); sent+=1
        return jsonify({"sent": True, "count": sent})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/sync", methods=["POST"])
def bulk_sync(code, plate):
    try:
        db = load_db(); code = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db['schools'].get(code)
        if not school or is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); van = school['vans'].get(plate_norm)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False, "error":"PIN"}), 401
        items = (request.get_json() or {}).get('items', [])
        if len(items)>100: items=items[-100:]
        short = kampala_now().strftime("%I:%M %p"); full = kampala_now().strftime("%I:%M %p %d %b"); count=0; log=school.get('attendance_log',[])
        for it in items:
            kid_id=it.get('kid_id'); act=it.get('action')
            if not kid_id or kid_id not in school['kids']: continue
            kid=school['kids'][kid_id]
            if act=='picked_home':
                kid['status']=f"On way to School - picked at {short}"; kid['times']['picked_home']=full; kid['absent']=False
                whatsapp_async(kid['parent_phone'], "picked_home", [kid['name'], short, plate_norm]); count+=1; log.append({"date": str(date.today()), "kid": kid['name'], "action": "picked_home", "time": full, "van": plate_norm})
            elif act=='dropped_school':
                kid['status']=f"At School - arrived at {short}"; kid['times']['dropped_school']=full
                whatsapp_async(kid['parent_phone'], "dropped_school", [kid['name'], short]); count+=1; log.append({"date": str(date.today()), "kid": kid['name'], "action": "dropped_school", "time": full, "van": plate_norm})
            elif act=='picked_school':
                kid['status']=f"On way Home - left at {short}"; kid['times']['picked_school']=full
                whatsapp_async(kid['parent_phone'], "picked_school", [kid['name'], short, plate_norm]); count+=1; log.append({"date": str(date.today()), "kid": kid['name'], "action": "picked_school", "time": full, "van": plate_norm})
            elif act=='dropped_home':
                kid['status']=f"Home Safe - {short}"; kid['times']['dropped_home']=full; kid['dropped_home_ts']=kampala_now().isoformat()
                whatsapp_async(kid['parent_phone'], "dropped_home", [kid['name'], short]); count+=1; log.append({"date": str(date.today()), "kid": kid['name'], "action": "dropped_home", "time": full, "van": plate_norm})
            elif act=='absent':
                kid['status']="ABSENT Today"; kid['absent']=True
                whatsapp_async(kid['parent_phone'], "absent", [kid['name'], str(date.today()), plate_norm]); count+=1; log.append({"date": str(date.today()), "kid": kid['name'], "action": "ABSENT", "time": full, "van": plate_norm})
            elif act=='present':
                kid['absent']=False; kid['status']="At Home - waiting for van"; kid['times']={}; kid.pop('dropped_home_ts',None); count+=1; log.append({"date": str(date.today()), "kid": kid['name'], "action": "PRESENT", "time": full, "van": plate_norm})
        school['attendance_log']=log[-500:]; save_db(db)
        return jsonify({"ok": True, "count": count})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

# ========== BEAUTIFIED PARENT PAGE (YOUR CODE IMPROVED) ==========
@app.route("/p/<kid_id>")
def parent_view(kid_id):
    db = load_db()
    for code, school in db['schools'].items():
        if kid_id in school['kids']:
            if is_locked(school):
                return CSS + "<div class='card' style='background:#ffcccc;text-align:center;max-width:500px;margin:50px auto'><h2>🔒 Service Paused</h2><p>Contact school admin.<br>Subscription ended on "+school.get('paid_until','')+"</p></div>"
            kid = school['kids'][kid_id]
            van = school['vans'].get(kid['van_plate'], {})
            if maybe_auto_reset(kid): save_db(db)

            times = kid.get('times', {})
            steps = [
                ("picked_home", "🏠 Picked Home", "On the road to school"),
                ("dropped_school", "🏫 Dropped School", "Safe at school"),
                ("picked_school", "🚐 Picked School", "Heading home"),
                ("dropped_home", "✅ Dropped Home", "Home safe")
            ]
            progress = len(times) * 25
            timeline_html = ""
            for key, label, sub in steps:
                done = key in times
                icon = "✅" if done else "⭕"
                t = times.get(key, "— waiting")
                timeline_html += f"<div style='display:flex;gap:14px;margin:16px 0;opacity:{1 if done else 0.45};align-items:center'><div style='font-size:26px;width:32px;text-align:center'>{icon}</div><div><b style='font-size:15px'>{label}</b><br><small style='color:#555'>{t} — {sub}</small></div></div>"

            status_color = "#0a7a2a" if "Home Safe" in kid['status'] else "#0D2A54"
            status_emoji = "✅" if progress>=100 else "👦"

            return f"""
{CSS}
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<div style="max-width:500px;margin:0 auto">
  <div style="text-align:center;padding:12px">
    <h2 style="margin:5px;letter-spacing:1px">🚐 FIKISHA</h2>
    <small style="color:#666">{school['name']} | Van {kid['van_plate']} | {current_time_str()}</small>
  </div>
  <div class="card" style="border-top:7px solid {status_color};text-align:center;border-radius:20px">
    <div style="font-size:56px">{status_emoji}</div>
    <h2 style="border:none;margin:12px 0;font-size:22px">{kid['name']}</h2>
    <small style="color:#666">Stage {kid.get('stage','')} | ID {kid['id']}</small><br><br>
    <span class="badge" style="background:{status_color};font-size:14px;padding:10px 18px;border-radius:30px">{kid['status']}</span>
    <div class="progress" style="height:16px;margin:22px 0;border-radius:20px"><div class="progress-fill" style="width:{progress}%"></div></div>
    <small style="color:#333"><b>{progress}% Complete today</b> — Auto-updates every 30 sec</small>
  </div>

  <div class="card" style="border-radius:20px">
    <h3 style="margin-top:0">🛣️ Live Journey</h3>
    {timeline_html}
  </div>

  <div class="card" style="display:flex;gap:10px;border-radius:16px;padding:14px">
    <a href="tel:{van.get('driver_phone','')}" style="flex:1;text-align:center;background:#0D2A54;color:#FFC107;padding:14px;border-radius:12px;text-decoration:none;font-weight:bold">📞 Call Driver<br><small style="color:#FFC107">{van.get('driver_name','Driver')}</small></a>
    <a href="https://wa.me/{to_wa(van.get('driver_phone',''))}?text=Hello {van.get('driver_name','')} about {kid['name']} ({kid['van_plate']}) - {kid['status']}" style="flex:1;text-align:center;background:#25D366;color:white;padding:14px;border-radius:12px;text-decoration:none;font-weight:bold">💬 WhatsApp<br><small>Driver</small></a>
  </div>

  <div class="card" style="background:#FFF8E1;text-align:center;border-radius:16px">
    <small style="color:#555">Van {kid['van_plate']} | Driver {van.get('driver_name','')} | {van.get('driver_phone','')}<br>
    Share this live page: <b>{kid_id}</b> | <a href="https://wa.me/{to_wa(kid['parent_phone'],'')}?text=Track {kid['name']} live: https://YOUR-DOMAIN/p/{kid_id}">Share to Parent</a></small>
  </div>
</div>
"""
    return CSS + "<div class='card' style='max-width:500px;margin:50px auto;text-align:center'><h2>🔍 Kid not found</h2><p>Link may be old or kid was deleted by admin.</p><small>Ask school for new parent link</small></div>"

# ========== BEAUTIFIED WEEKLY REPORT WITH EXPORT ==========
@app.route("/report/<code>/weekly")
def weekly_report(code):
    try:
        db = load_db(); code = normalize_code(code); school = db['schools'].get(code)
        if not school: return CSS + "<div class='card'><h2>No school</h2></div>"
        kids = list(school['kids'].values())
        total = len(kids)
        absent_today = sum(1 for k in kids if k.get('absent'))
        not_picked = [k for k in kids if not k.get('times') and not k.get('absent')]
        picked_home = sum(1 for k in kids if 'picked_home' in k.get('times',{}))

        html = CSS + f"""
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <div class="no-print" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <a href="/admin/{code}"><button>⬅️ Back Admin</button></a>
        <a href="/report/{code}"><button class="btn-grey">📊 Daily Report</button></a>
        <a href="/report/{code}/weekly/csv"><button class="btn-blue">📥 Export Weekly CSV</button></a>
        <a href="/report/{code}/csv"><button class="btn-orange">📥 Export Daily CSV</button></a>
        </div>
        <h2>📊 Weekly & Daily Overview — {school['name']} ({code}) - {current_time_str()}</h2>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
          <div class="card" style="text-align:center;border-top-color:#0a7a2a"><h3 style="margin:0">{total}</h3><small>Total Kids</small></div>
          <div class="card" style="text-align:center;border-top-color:#0a7a2a"><h3 style="margin:0">{picked_home}</h3><small>Picked Today</small></div>
          <div class="card" style="text-align:center;border-top-color:#d32f2f"><h3 style="margin:0">{absent_today}</h3><small>Absent Today</small></div>
        </div>
        <div class="card"><b>⏳ Not yet picked today ({len(not_picked)}):</b> {', '.join([k['name'] for k in not_picked]) or 'All picked ✅'}</div>
        <div class="card">
        <h3>All Kids — Status Today</h3>
        <table><tr><th>Kid</th><th>Van</th><th>Status</th><th>Times</th><th>Parent Link</th></tr>
        """
        for kid in kids:
            absent_flag = "🚫 ABSENT" if kid.get('absent') else "✅ Present"
            times = kid.get('times',{})
            times_str = "<br>".join([f"<small>{k}: {v}</small>" for k,v in times.items()]) or "<small style='color:#999'>Not started</small>"
            html += f"<tr><td><b>{kid['name']}</b><br><small>{kid['stage']} | {kid['id']}</small></td><td>{kid['van_plate']}</td><td>{absent_flag}<br><small>{kid['status'][:30]}</small></td><td>{times_str}</td><td><a href='/p/{kid['id']}' target='_blank'>View</a></td></tr>"
        html += "</table></div>"

        # attendance log last 200
        log = school.get('attendance_log', [])[-200:][::-1]
        if log:
            html += "<div class='card'><h3>Recent Activity Log (last 200)</h3><table><tr><th>Date</th><th>Time</th><th>Kid</th><th>Van</th><th>Action</th></tr>"
            for e in log:
                html += f"<tr><td>{e.get('date','')}</td><td><small>{e.get('time','')}</small></td><td>{e.get('kid','')}</td><td>{e.get('van','')}</td><td>{e.get('action','')}</td></tr>"
            html += "</table></div>"
        return html
    except Exception as e:
        return f"Weekly report error: {e}"

@app.route("/report/<code>/csv")
def export_csv_daily(code):
    db = load_db(); code = normalize_code(code); school = db['schools'].get(code)
    if not school: return "No school"
    si = io.StringIO(); cw = csv.writer(si)
    cw.writerow(["Kid ID","Name","Stage","Van","Parent Phone","Status","Absent","Picked Home","Dropped School","Picked School","Dropped Home","Report Time"])
    for kid in school['kids'].values():
        t = kid.get('times',{})
        cw.writerow([kid['id'],kid['name'],kid['stage'],kid['van_plate'],kid['parent_phone'],kid['status'],kid.get('absent',False),t.get('picked_home',''),t.get('dropped_school',''),t.get('picked_school',''),t.get('dropped_home',''),current_time_str()])
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=FIKISHA_DAILY_{code}_{date.today()}.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/report/<code>/weekly/csv")
def export_csv_weekly(code):
    db = load_db(); code = normalize_code(code); school = db['schools'].get(code)
    if not school: return "No school"
    si = io.StringIO(); cw = csv.writer(si)
    cw.writerow(["Date","Time","Kid Name","Van","Action"])
    for e in school.get('attendance_log', []):
        cw.writerow([e.get('date',''), e.get('time',''), e.get('kid',''), e.get('van',''), e.get('action','')])
    # also add current snapshot
    cw.writerow([])
    cw.writerow(["--- CURRENT SNAPSHOT ---"])
    cw.writerow(["Kid ID","Name","Stage","Van","Parent Phone","Status","Absent"])
    for kid in school['kids'].values():
        cw.writerow([kid['id'],kid['name'],kid['stage'],kid['van_plate'],kid['parent_phone'],kid['status'],kid.get('absent',False)])
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=FIKISHA_WEEKLY_{code}_{date.today()}.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/manifest.json")
def manifest(): return jsonify({"name": "FIKISHA Driver","short_name": "FIKISHA","start_url": "/","display": "standalone","background_color": "#FFF8E1","theme_color": "#0D2A54"})
@app.route("/sw.js")
def sw():
    js = "self.addEventListener('install', e=>{ self.skipWaiting(); }); self.addEventListener('activate', e=>{ self.clients.claim(); }); self.addEventListener('fetch', e=>{ e.respondWith(fetch(e.request).catch(()=> caches.match(e.request))); });"
    return Response(js, mimetype='application/javascript')
@app.route("/health")
def health(): return jsonify({"ok": True, "schools": len(load_db().get("schools", {})), "supabase": bool(SUPABASE_URL)})

if __name__ == "__main__": app.run(host="0.0.0.0", port=5000)