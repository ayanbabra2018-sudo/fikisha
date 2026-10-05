from flask import Flask, request, redirect, jsonify, Response, make_response
import json, os, requests, csv, io, threading, uuid, time
from datetime import datetime, date, timedelta
from urllib.parse import quote
try:
    from zoneinfo import ZoneInfo
    KAMPALA_TZ = ZoneInfo("Africa/Kampala")
except: KAMPALA_TZ = None

def kampala_now():
    try:
        if KAMPALA_TZ: return datetime.now(KAMPALA_TZ)
        return datetime.now() + timedelta(hours=3)
    except: return datetime.now()
def normalize_plate(p): return str(p).upper().strip().replace(" ","") if p else ""
def normalize_code(p): return str(p).upper().strip().replace(" ","") if p else ""

app = Flask(__name__)
SUPABASE_URL = os.environ.get("SUPABASE_URL","").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY","")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "fikisha2026")
TRAFFIC_COOLDOWN = {}
ACTION_COOLDOWN = {}

def h(): return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
def hr(): return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
def safe_get(url, **kw):
    for i in range(3):
        try: return requests.get(url, timeout=10, **kw)
        except: time.sleep(0.5*(i+1))
    return None
def safe_post(url, **kw):
    for i in range(2):
        try: return requests.post(url, timeout=12, **kw)
        except: time.sleep(0.5)
    return None
def safe_patch(url, **kw):
    for i in range(2):
        try: return requests.patch(url, timeout=12, **kw)
        except: time.sleep(0.5)
    return None
def safe_delete(url, **kw):
    try: return requests.delete(url, timeout=10, **kw)
    except: return None

def db_get_school(code):
    code=normalize_code(code)
    r=safe_get(f"{SUPABASE_URL}/rest/v1/schools?code=eq.{quote(code)}&select=*", headers=h())
    return r.json()[0] if r and r.status_code==200 and r.json() else None
def db_get_vans(code):
    code=normalize_code(code)
    r=safe_get(f"{SUPABASE_URL}/rest/v1/vans?school_code=eq.{quote(code)}&select=*", headers=h())
    return r.json() if r and r.status_code==200 else []
def db_get_kids(code, plate=None):
    code=normalize_code(code)
    url=f"{SUPABASE_URL}/rest/v1/kids?school_code=eq.{quote(code)}&select=*&order=name.asc"
    if plate: url+=f"&van_plate=eq.{quote(normalize_plate(plate))}"
    r=safe_get(url, headers=h())
    return r.json() if r and r.status_code==200 else []
def db_get_logs(code, limit=200):
    code=normalize_code(code)
    r=safe_get(f"{SUPABASE_URL}/rest/v1/attendance_log?school_code=eq.{quote(code)}&select=*&order=created_at.desc&limit={limit}", headers=h())
    return r.json() if r and r.status_code==200 else []
def load_db():
    if not SUPABASE_URL or not SUPABASE_KEY: return {"schools": {}}
    try:
        r=safe_get(f"{SUPABASE_URL}/rest/v1/schools?select=*", headers=h())
        schools=r.json() if r and r.status_code==200 else []
        result={"schools":{}}
        for s in schools:
            code=s['code']
            vans={v['plate']: {"plate": v['plate'], "driver_name": v.get('driver_name',''), "driver_phone": v.get('driver_phone','')} for v in db_get_vans(code)}
            kids={k['id']: k for k in db_get_kids(code)}
            result["schools"][code]={"name": s.get('name',''), "code": code, "director_phone": s.get('director_phone',''), "paid_until": s.get('paid_until','2026-12-31'), "vans": vans, "kids": kids}
        return result
    except: return {"schools": {}}
def to_wa(p):
    try:
        if not p: return ""
        c = str(p).replace("+","").replace(" ","").replace("-","").strip()
        if c.startswith("0"): c = "256" + c[1:]
        if len(c)==9: c = "256"+c
        return c
    except: return ""
def current_time_str(): return kampala_now().strftime("%I:%M %p")
def whatsapp_template(to, template_name, params=[]):
    token = os.environ.get("WHATSAPP_TOKEN"); phone_id = os.environ.get("WHATSAPP_PHONE_ID", "1327812003752192")
    if not token or not to: return False
    clean = to_wa(to)
    if len(clean) < 11: return False
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body_params = [{"type": "text", "text": str(p)[:100]} for p in (params or [])]
    data = {"messaging_product": "whatsapp","to": clean,"type": "template","template": {"name": template_name,"language": {"code": "en_US"},"components": [{"type": "body", "parameters": body_params}] if body_params else []}}
    try: r = requests.post(url, headers=headers, json=data, timeout=8); return r.status_code == 200
    except: return False
def whatsapp_async(to, template_name, params=[]): threading.Thread(target=whatsapp_template, args=(to, template_name, params), daemon=True).start()
def is_locked(school):
    try: return str(school.get('paid_until','')) < str(date.today())
    except: return True
def maybe_auto_reset(kid):
    try:
        ts = kid.get('dropped_home_ts')
        if not ts or 'dropped_home' not in (kid.get('times',{}) or {}): return False
        dropped_time = datetime.fromisoformat(ts); now = kampala_now()
        if dropped_time.tzinfo is None: now_cmp = now.replace(tzinfo=None) if now.tzinfo else now
        else:
            now_cmp = now
            if now_cmp.tzinfo is None and dropped_time.tzinfo: dropped_time = dropped_time.replace(tzinfo=None)
        if (now_cmp - dropped_time) > timedelta(hours=2):
            kid['times'] = {}; kid['status'] = "At Home - waiting for van"; kid['absent'] = False; kid.pop('dropped_home_ts', None); return True
    except:
        try: kid['times'] = {}; kid['status'] = "At Home - waiting for van"; kid['absent'] = False; kid.pop('dropped_home_ts', None); return True
        except: pass
    return False
def get_van_pin(van):
    try: phone = to_wa((van or {}).get('driver_phone','')); return phone[-4:] if len(phone)>=4 else "1234"
    except: return "1234"
def get_admin_pin(school):
    try: phone = to_wa((school or {}).get('director_phone','')); return phone[-4:] if len(phone)>=4 else "1234"
    except: return "1234"

CSS = """<style>
body{font-family:system-ui,Arial;background:#FFF8E1;margin:0;padding:15px;color:#1a1a1a;font-size:17px;line-height:1.5}
h2{color:#0D2A54;border-bottom:3px solid #FFC107;padding-bottom:8px;font-size:22px}
.card{background:white;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 4px 14px rgba(0,0,0,0.1);border-top:5px solid #FFC107}
input,select{padding:14px!important;border-radius:10px;border:2px solid #FFC107;margin:6px;width:90%;font-size:16px!important}
button{padding:14px 20px!important;border-radius:10px;border:none;background:#0D2A54;color:#FFC107;font-weight:bold;cursor:pointer;margin:5px;font-size:16px!important;min-height:48px;transition:opacity 0.2s}
button:disabled{opacity:0.4;cursor:not-allowed}
.btn-done{background:#0a7a2a!important;color:white!important}
.btn-red{background:#d32f2f;color:white}
.btn-orange{background:#ef6c00;color:white}
.btn-blue{background:#1565c0;color:white}
.btn-grey{background:#888;color:white}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:700px){.grid{grid-template-columns:1fr}}
.badge{padding:8px 14px;border-radius:20px;background:#0D2A54;color:#FFC107;font-size:15px}
.progress{height:10px;background:#eee;border-radius:10px;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,#0a7a2a,#FFC107)}
</style>"""

@app.route("/", methods=["GET", "POST"])
def home():
    try:
        if request.method == "POST":
            pwd = request.form.get('password','')
            if pwd == SUPER_ADMIN_PASSWORD:
                resp = redirect("/"); resp.set_cookie("super_auth", SUPER_ADMIN_PASSWORD, max_age=86400*7, httponly=True, samesite='Lax'); return resp
            else: return CSS + "<div class='card' style='background:#ffcccc'><h2>Wrong password</h2><a href='/'>Try again</a></div>"
        auth = request.cookies.get("super_auth")
        if auth!= SUPER_ADMIN_PASSWORD:
            return CSS + """<div class='card' style='max-width:400px;margin:80px auto;text-align:center'><h2>🔐 FIKISHA Super Admin</h2><form method="post" id="loginForm"><input name="password" type="password" placeholder="Password" required style="width:90%"><br><button style="width:95%" id="loginBtn">Unlock</button></form></div><script>document.getElementById('loginForm').addEventListener('submit',function(){document.getElementById('loginBtn').disabled=true;});</script>"""
        db = load_db()
        html = CSS + f"<h2>FIKISHA - Super Admin ({current_time_str()}) | FINAL Armor + Mass Pick ✅</h2>"
        html += """<div class="card"><h3>Create New School</h3><form method="post" action="/create_school" onsubmit="this.querySelector('button').disabled=true"><input name="school_name" placeholder="Bright Angels" required><input name="code" placeholder="Code BRIGHT123" required><input name="director" placeholder="Director WhatsApp 2567..." required><input name="paid_until" type="date" required><button>Add School +</button></form></div><hr>"""
        for code, s in (db.get("schools", {}) or {}).items():
            if not s: continue
            lock = "🔴 EXPIRED" if is_locked(s) else "🟢 Active"
            html += f"""<div class='card' style="{'border:3px solid red' if is_locked(s) else ''}"><b>{s.get('name','')}</b> ({code}) - {lock} - Paid: {s.get('paid_until','?')} - Director: {s.get('director_phone','')} PIN:{get_admin_pin(s)}<br><form method='post' action='/super/update_school/{code}' style='margin:10px 0;background:#FFF8E1;padding:10px;border-radius:10px' onsubmit="this.querySelector('button').disabled=true"><input name='school_name' value='{s.get('name','')}' required style='width:28%'><input name='director' value='{s.get('director_phone','')}' required style='width:28%'><input type='date' name='paid_until' value='{s.get('paid_until','')}' required style='width:28%'><button>Save Edit ✅</button></form><a href='/admin/{code}'>Manage (PIN)</a> | <a href='/report/{code}'>Daily</a> | <a href='/report/{code}/weekly'>Weekly</a> | <a href='/super/delete_school/{code}' onclick="return confirm('DELETE {code}?')" style='color:red'>Delete ❌</a><br><br><button onclick="shareAdmin('{code}','{s.get('name','')}','{s.get('director_phone','')}','{get_admin_pin(s)}')" class="btn-blue">📲 Share Admin + PIN</button><br>"""
            for vp, van in (s.get('vans',{}) or {}).items():
                html += f"Van <b>{vp}</b> - {(van or {}).get('driver_name','')} PIN:{get_van_pin(van)} - <a href='/driver/{code}/{vp}'>Driver</a><br>"
            html += "</div>"
        html += """<script>function toWa(phone){let c=(phone||'').replace(/[^0-9]/g,'').trim();if(c.startsWith('0'))c='256'+c.substring(1);if(c.length==9)c='256'+c;return c;} function shareAdmin(code,name,phone,pin){let link=window.location.origin+"/admin/"+code;let msg="FIKISHA Admin link for "+name+" ("+code+"): "+link+"\\nPIN: "+pin;let clean=toWa(phone);window.open("https://wa.me/"+clean+"?text="+encodeURIComponent(msg),"_blank");}</script>"""
        return html
    except Exception as e: return CSS + f"<div class='card'><h2>Error</h2><p>{e}</p></div>"

@app.route("/create_school", methods=["POST"])
def create_school():
    code = normalize_code(request.form.get('code',''))
    safe_post(f"{SUPABASE_URL}/rest/v1/schools", headers=hr(), json={"code": code, "name": request.form.get('school_name','School'), "director_phone": request.form.get('director',''), "paid_until": request.form.get('paid_until', str(date.today()))})
    return redirect("/")

@app.route("/super/update_school/<code>", methods=["POST"])
def update_school(code):
    code_n = normalize_code(code)
    safe_patch(f"{SUPABASE_URL}/rest/v1/schools?code=eq.{quote(code_n)}", headers=h(), json={"name": request.form.get('school_name'), "director_phone": request.form.get('director'), "paid_until": request.form.get('paid_until')})
    return redirect("/")

@app.route("/super/delete_school/<code>")
def delete_school(code):
    code_n = normalize_code(code)
    safe_delete(f"{SUPABASE_URL}/rest/v1/schools?code=eq.{quote(code_n)}", headers=h())
    safe_delete(f"{SUPABASE_URL}/rest/v1/vans?school_code=eq.{quote(code_n)}", headers=h())
    safe_delete(f"{SUPABASE_URL}/rest/v1/kids?school_code=eq.{quote(code_n)}", headers=h())
    safe_delete(f"{SUPABASE_URL}/rest/v1/attendance_log?school_code=eq.{quote(code_n)}", headers=h())
    return redirect("/")
ADMIN_LOGIN_HTML = CSS + """<div class='card' style='max-width:400px;margin:80px auto;text-align:center'><h2>🔐 Admin PIN for {{school_name}} ({{code}})</h2><form method="post" id="pinForm"><input name="pin" type="password" placeholder="4-digit PIN" required style="text-align:center;font-size:22px;letter-spacing:8px" maxlength="4"><br><button style="width:95%" id="pinBtn">Unlock Admin</button></form>{% if error %}<p style="color:red">{{error}}</p>{% endif %}</div><script>document.getElementById('pinForm').addEventListener('submit',function(){document.getElementById('pinBtn').disabled=true;});</script>"""

ADMIN_HTML = CSS + """ {% if locked %}<div class='card' style='background:#ffcccc;border:3px solid red;text-align:center'><h1>🚫 PAYMENT EXPIRED</h1></div>{% endif %} <h2>{{school_name}} Admin ({{code}}) - <a href="/admin/{{code}}/logout" style="font-size:12px">Logout PIN</a></h2> <p>{{kampala_time}} | FINAL ✅ | <a href="/report/{{code}}">Daily</a> | <a href="/report/{{code}}/weekly">Weekly 📊</a></p> <div class="card"><h3>Daily Control</h3>{% if locked %}<button disabled>🔒 Locked</button>{% else %}<form method="post" action="/api/{{code}}/reset_all" onsubmit="return confirmReset(this)"><button style="background:#0a7a2a;width:100%" id="resetBtn">RESET ALL FOR TOMORROW</button></form>{% endif %}</div> <div class="card"><h3>Add Van</h3>{% if locked %}<p>🔒 Locked</p>{% else %}<form method="post" action="/admin/{{code}}/add_van" class="anti-spam-form"><input name="plate" placeholder="Plate UAA123A" required><input name="driver_name" placeholder="Driver Name" required><input name="driver_phone" placeholder="Driver Phone 2567..." required><button>Add Van</button></form>{% endif %}</div> <div class="card"><h3>Add Kid (Unlimited)</h3>{% if locked %}<p>🔒 Locked</p>{% elif vans_count==0 %}<p>Add Van first!</p>{% else %}<form method="post" action="/admin/{{code}}/add_kid" class="anti-spam-form"><input name="kid_name" placeholder="Kid Name" required><input name="stage" placeholder="Stage" required><input name="parent_phone" placeholder="Parent WhatsApp 2567..." required>Van: <select name="van_plate" required>{% for vp in vans %}<option value="{{vp}}">{{vp}}</option>{% endfor %}</select><button>Add Kid</button></form>{% endif %}</div> <hr><h3>Vans & Kids ({{total_kids}}) - Only {{code}}</h3> {% for vp, van in vans_items %} <div class="card"><b>{{vp}} - {{van.driver_name}}</b> - {{van.driver_phone}} - PIN: {{van.pin}} - <a href="/driver/{{code}}/{{vp}}">Driver Page</a><br> {% if not locked %}<form method="post" action="/admin/{{code}}/edit_van/{{vp}}" class="anti-spam-form" style="background:#FFF8E1;padding:8px;border-radius:8px;margin:8px 0"><input name="driver_name" value="{{van.driver_name}}" required style="width:30%"> <input name="driver_phone" value="{{van.driver_phone}}" required style="width:35%"> <button>Save Van</button> | <a href="/admin/{{code}}/delete_van/{{vp}}" onclick="return confirm('Delete van {{vp}}?')" style="color:red">Delete Van ❌</a></form>{% endif %} {% for kid in kids_list if kid.van_plate==vp %} <div style="margin:8px 0;padding:10px;background:#FFF8E1;border-radius:10px"><div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px"><span>👦 <b>{{kid.name}}</b> ({{kid.stage}}) - {{kid.status_short}}<br>Parent: {{kid.parent_phone}} | <a href="/p/{{kid.id}}" target="_blank">Parent Link 👁️</a></span><div><button onclick="shareParent('{{kid.name}}','{{kid.id}}','{{kid.parent_phone}}')" class="btn-blue" style="padding:6px 10px;font-size:11px">📲 Share Parent</button>{% if not locked %}<a href="#" onclick="document.getElementById('edit-{{kid.id}}').style.display='block';return false;">✏️ Edit</a> | <a href="/admin/{{code}}/delete_kid/{{kid.id}}" onclick="return confirm('Delete {{kid.name}}?')" style="color:red;font-size:11px">❌ Delete</a>{% endif %}</div></div> {% if not locked %}<div id="edit-{{kid.id}}" style="display:none;background:white;padding:8px;border-radius:8px;margin-top:8px"><form method="post" action="/admin/{{code}}/edit_kid/{{kid.id}}" class="anti-spam-form"><input name="kid_name" value="{{kid.name}}" required style="width:22%"> <input name="stage" value="{{kid.stage}}" style="width:18%"> <input name="parent_phone" value="{{kid.parent_phone}}" required style="width:28%"><select name="van_plate" style="width:18%">{% for vvp in vans %}<option value="{{vvp}}" {% if vvp==kid.van_plate %}selected{% endif %}>{{vvp}}</option>{% endfor %}</select><button>Save Kid</button></form></div>{% endif %}</div> {% endfor %}</div>{% endfor %} <script>function toWa(phone){let c=(phone||'').replace(/[^0-9]/g,'').trim();if(c.startsWith('0'))c='256'+c.substring(1);if(c.length==9)c='256'+c;return c;} function shareParent(name,kidId,phone){let clean=toWa(phone);let link=window.location.origin+"/p/"+kidId;let msg="Hello, track "+name+" live: "+link;window.open("https://wa.me/"+clean+"?text="+encodeURIComponent(msg),"_blank");} function confirmReset(form){if(!confirm('RESET ALL?'))return false;form.querySelector('button').disabled=true;return true;} document.querySelectorAll('.anti-spam-form').forEach(f=>{f.addEventListener('submit',function(){let btn=this.querySelector('button');if(btn.disabled)return false;btn.disabled=true;btn.innerText='Saving...';setTimeout(()=>{btn.disabled=false;},3000);});});</script> """

@app.route("/admin/<code>", methods=["GET", "POST"])
def admin(code):
    try:
        code_norm = normalize_code(code)
        school = db_get_school(code_norm)
        if not school: return CSS + "<div class='card'><h2>🚫 School Deleted</h2><a href='/'>Home</a></div>"
        real_pin = get_admin_pin(school); super_auth = request.cookies.get("super_auth"); admin_cookie = request.cookies.get(f"admin_pin_{code_norm}")
        if request.method == "POST":
            entered = (request.form.get('pin','') or '').strip()
            if entered == real_pin or entered == SUPER_ADMIN_PASSWORD:
                resp = make_response(redirect(f"/admin/{code_norm}")); resp.set_cookie(f"admin_pin_{code_norm}", real_pin, max_age=86400*30, httponly=True, samesite='Lax'); return resp
            else:
                from jinja2 import Template; return Template(ADMIN_LOGIN_HTML).render(school_name=school.get('name',''), code=code_norm, error="Wrong PIN")
        if admin_cookie!= real_pin and super_auth!= SUPER_ADMIN_PASSWORD:
            from jinja2 import Template; return Template(ADMIN_LOGIN_HTML).render(school_name=school.get('name',''), code=code_norm, error=None)
        vans_raw = db_get_vans(code_norm)
        vans = {v['plate']: v for v in vans_raw}
        for v in vans.values():
            if v: v['pin']=get_van_pin(v)
        kids_raw = db_get_kids(code_norm)
        for k in kids_raw:
            if k and maybe_auto_reset(k):
                safe_patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(k['id'])}&school_code=eq.{quote(code_norm)}", headers=h(), json={"times": k['times'], "status": k['status'], "absent": k['absent']})
        vans_items = list(vans.items())
        kids_list = []
        for kid in kids_raw:
            if not kid: continue
            kid['status_short'] = (kid.get('status','') or '')[:30]
            kids_list.append(kid)
        from jinja2 import Template
        return Template(ADMIN_HTML).render(school_name=school.get('name',''), school_paid_until=school.get('paid_until',''), code=code_norm, locked=is_locked(school), kampala_time=current_time_str(), total_kids=len(kids_list), vans=vans.keys(), vans_items=vans_items, kids_list=kids_list, vans_count=len(vans))
    except Exception as e: return CSS + f"<div class='card'><h2>Admin Error</h2><p>{e}</p></div>"

@app.route("/admin/<code>/logout")
def admin_logout(code):
    code_norm = normalize_code(code); resp = redirect(f"/admin/{code_norm}"); resp.set_cookie(f"admin_pin_{code_norm}", "", max_age=0); return resp

@app.route("/admin/<code>/add_van", methods=["POST"])
def add_van(code):
    try:
        code_norm = normalize_code(code); plate = normalize_plate(request.form.get('plate',''))
        if not plate: return redirect(f"/admin/{code_norm}")
        school = db_get_school(code_norm)
        if not school or is_locked(school): return redirect(f"/admin/{code_norm}")
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{code_norm}")
        safe_post(f"{SUPABASE_URL}/rest/v1/vans", headers=hr(), json={"school_code": code_norm, "plate": plate, "driver_name": request.form.get('driver_name','Driver'), "driver_phone": request.form.get('driver_phone','')})
    except: pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/edit_van/<plate>", methods=["POST"])
def edit_van(code, plate):
    try:
        c=normalize_code(code); p=normalize_plate(plate); school = db_get_school(c)
        if school and not is_locked(school):
            if request.cookies.get(f"admin_pin_{c}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{c}")
            safe_patch(f"{SUPABASE_URL}/rest/v1/vans?school_code=eq.{quote(c)}&plate=eq.{quote(p)}", headers=h(), json={"driver_name": request.form.get('driver_name','Driver'), "driver_phone": request.form.get('driver_phone','')})
    except: pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/delete_van/<plate>")
def delete_van_route(code, plate):
    try:
        c=normalize_code(code); p=normalize_plate(plate); school = db_get_school(c)
        if school and not is_locked(school):
            if request.cookies.get(f"admin_pin_{c}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{c}")
            safe_delete(f"{SUPABASE_URL}/rest/v1/vans?school_code=eq.{quote(c)}&plate=eq.{quote(p)}", headers=h())
            safe_delete(f"{SUPABASE_URL}/rest/v1/kids?school_code=eq.{quote(c)}&van_plate=eq.{quote(p)}", headers=h())
    except: pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/delete_kid/<kid_id>")
def delete_kid_route(code, kid_id):
    try:
        c=normalize_code(code); school = db_get_school(c)
        if school and not is_locked(school):
            if request.cookies.get(f"admin_pin_{c}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{c}")
            safe_delete(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid_id)}&school_code=eq.{quote(c)}", headers=h())
    except: pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/add_kid", methods=["POST"])
def add_kid_route(code):
    try:
        kid_id = str(uuid.uuid4())[:8].upper(); code_norm = normalize_code(code); school = db_get_school(code_norm)
        if not school or is_locked(school): return redirect(f"/admin/{code_norm}")
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{code_norm}")
        van_plate = normalize_plate(request.form.get('van_plate',''))
        safe_post(f"{SUPABASE_URL}/rest/v1/kids", headers=h(), json={"id": kid_id, "school_code": code_norm, "name": request.form.get('kid_name','Kid'), "stage": request.form.get('stage',''), "parent_phone": request.form.get('parent_phone',''), "van_plate": van_plate, "status": "At Home - waiting for van", "times": {}, "absent": False})
    except: pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/edit_kid/<kid_id>", methods=["POST"])
def edit_kid_route(code, kid_id):
    try:
        c=normalize_code(code); school = db_get_school(c)
        if school and not is_locked(school):
            if request.cookies.get(f"admin_pin_{c}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{c}")
            new_van = normalize_plate(request.form.get('van_plate',''))
            safe_patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid_id)}&school_code=eq.{quote(c)}", headers=h(), json={"name": request.form.get('kid_name'), "stage": request.form.get('stage'), "parent_phone": request.form.get('parent_phone'), "van_plate": new_van})
    except: pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/api/<code>/reset_all", methods=["POST"])
def reset_all(code):
    try:
        code_norm = normalize_code(code); school = db_get_school(code_norm)
        if not school or is_locked(school): return CSS + "<div class='card'><h2>🚫 EXPIRED</h2></div>"
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{code_norm}")
        kids = db_get_kids(code_norm)
        for kid in kids:
            safe_patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid['id'])}&school_code=eq.{quote(code_norm)}", headers=h(), json={"times": {}, "status": "At Home - waiting for van", "absent": False, "dropped_home_ts": None})
    except: pass
    return redirect(f"/admin/{normalize_code(code)}")
DRIVER_PIN_HTML = CSS + """<div class='card' style='max-width:400px;margin:80px auto;text-align:center'><h2>🔐 Driver PIN for Van {{plate}}</h2><form method="post" id="pinForm"><input name="pin" type="password" placeholder="4-digit PIN" required style="text-align:center;font-size:22px;letter-spacing:8px" maxlength="4"><br><button style="width:95%" id="pinBtn">Unlock</button></form></div><script>document.getElementById('pinForm').addEventListener('submit',function(){document.getElementById('pinBtn').disabled=true;});</script>"""

DRIVER_HTML = CSS + """<link rel="manifest" href="/manifest.json">{% if locked %}<div class='card' style='background:#ffcccc;border:3px solid red;text-align:center'><h1>🚫 PAYMENT EXPIRED</h1></div>{% endif %}<h2>Driver: {{van.driver_name}} - Van {{van.plate}} - {{school_name}} - <a href="/driver/{{code}}/{{van.plate}}/logout" style="font-size:12px">Logout PIN</a></h2><div id="netStatus" style="padding:8px;border-radius:8px;text-align:center;font-weight:bold">Checking...</div><p>Code: {{code}} | {{kampala_time}} | {{today}} | {{kids|length}} kids - FINAL ✅</p>
<div style="display:flex;gap:10px;justify-content:space-between;flex-wrap:nowrap">
<div class="card" style="flex:1;min-width:0;border:2px solid #0a7a2a;background:#e8f5e9;margin:0;padding:10px">
<h3 style="color:#0a7a2a;margin:0 0 8px 0;font-size:13px;text-align:center">🏫 Mass Actions</h3>
<div style="display:flex;gap:8px">
<button class="btn-done" onclick="massDrop('dropped_school')" {% if locked %}disabled{% endif %} id="massDropBtn" style="flex:1;font-size:11px;padding:12px 6px;border-radius:10px;line-height:1.2">🏫<br>DROP ALL<br><small>AT SCHOOL</small></button>
<button class="btn-blue" onclick="massDrop('picked_school')" {% if locked %}disabled{% endif %} id="massPickBtn" style="flex:1;font-size:11px;padding:12px 6px;border-radius:10px;line-height:1.2;background:#1565c0;color:white">🚐<br>PICK ALL<br><small>AT SCHOOL</small></button>
</div>
<small style="font-size:10px;color:#555;display:block;text-align:center;margin-top:6px">Status + Progress + WhatsApp updated like single button</small>
</div>
<div style="width:8px;flex-shrink:0"></div>
<div class="card" style="flex:1;min-width:0;border:2px solid #d32f2f;margin:0;padding:10px"><h3 style="color:#d32f2f;margin:0 0 8px 0;font-size:13px;text-align:center">🚨 Alert All Parents</h3><select id="trafficReason" {% if locked %}disabled{% endif %} style="width:100%;padding:8px;border:2px solid #d32f2f;font-size:11px"><option value="Heavy traffic - 15 mins late">Traffic - 15 mins late</option><option value="Heavy traffic - 30 mins late">Traffic - 30 mins late</option><option value="Tyre puncture - fixing, 20 mins delay">Puncture - 20 mins</option><option value="Fuel stop - 10 mins delay">Fuel - 10 mins</option><option value="custom">✏️ Custom message...</option></select><input id="trafficCustom" placeholder="Type custom e.g. Bridge closed - 25 mins delay" style="width:95%;display:none;margin-top:6px;border:2px solid #d32f2f" maxlength="100"><button class="btn-red" onclick="sendTraffic()" {% if locked %}disabled{% endif %} id="trafficBtn" style="width:100%;margin-top:8px;padding:10px;font-size:12px">🚨 SEND TO ALL PARENTS</button><small id="customHint" style="display:none;color:#d32f2f;font-size:10px">✏️ Custom active - type above (works!)</small></div></div>
<hr><div class="grid">{% for kid_id, kid in kids.items() %}<div class="card"><b>{{kid.name}}</b> - {{kid.stage}}<br>Status: <span class="badge">{{kid.status}}</span><div class="progress"><div class="progress-fill" style="width: {{kid.progress}}%"></div></div><small>{{kid.progress}}%</small><br><button onclick="action('{{kid.id}}','picked_home')" {% if locked %}disabled{% endif %} class="act-btn" data-kid="{{kid.id}}">PICKED HOME</button><button onclick="action('{{kid.id}}','dropped_school')" {% if locked %}disabled{% endif %} class="act-btn" data-kid="{{kid.id}}">DROPPED SCHOOL</button><button onclick="action('{{kid.id}}','picked_school')" {% if locked %}disabled{% endif %} class="act-btn" data-kid="{{kid.id}}">PICKED SCHOOL</button><button onclick="action('{{kid.id}}','dropped_home')" {% if locked %}disabled{% endif %} class="act-btn" data-kid="{{kid.id}}">DROPPED HOME</button><br><button class="btn-orange act-btn" onclick="action('{{kid.id}}','absent')" {% if locked %}disabled{% endif %} data-kid="{{kid.id}}">ABSENT</button><button class="btn-grey act-btn" onclick="action('{{kid.id}}','present')" {% if locked %}disabled{% endif %} data-kid="{{kid.id}}">BACK</button></div>{% endfor %}</div>
<script>
 if('serviceWorker' in navigator){ navigator.serviceWorker.register('/sw.js').catch(()=>{}); }
 let queue = JSON.parse(localStorage.getItem('fikisha_queue_{{van.plate}}')||'[]');
 if(queue.length>100){ queue=queue.slice(-100); localStorage.setItem('fikisha_queue_{{van.plate}}', JSON.stringify(queue)); }
 function updateNet(){let el=document.getElementById('netStatus');if(navigator.onLine){el.innerText='✅ ONLINE | Queued: '+queue.length;el.style.background='#e8f5e9';if(queue.length>0)syncQueue();}else{el.innerText='⚠️ OFFLINE - saved | Queued: '+queue.length;el.style.background='#fff3cd';}}
 window.addEventListener('online', updateNet); window.addEventListener('offline', updateNet); updateNet();
 function saveQueue(){if(queue.length>100)queue=queue.slice(-100);localStorage.setItem('fikisha_queue_{{van.plate}}', JSON.stringify(queue));updateNet();}
 let tapping = {};
 function action(kid_id, act){
  {% if locked %} return; {% endif %}
  let key = kid_id + '_' + act; if(tapping[key]) return; tapping[key]=true;
  let btns=document.querySelectorAll(`button[data-kid='${kid_id}']`); btns.forEach(b=>{b.disabled=true; b.style.opacity='0.4';});
  let targetBtn = document.querySelector(`button[onclick*="'${kid_id}','${act}'"]`); if(targetBtn) targetBtn.innerText='⏳...';
  queue.push({kid_id, action:act, time: new Date().toISOString()}); saveQueue();
  if(navigator.onLine){
    fetch('/api/{{code}}/{{van.plate}}/action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({kid_id, action:act})}).then(r=>r.json()).then(j=>{
      if(j.cooldown){ alert('Wait 3 sec!'); btns.forEach(b=>{b.disabled=false; b.style.opacity='1';}); tapping[key]=false; return;}
      queue=queue.filter(q=>!(q.kid_id==kid_id && q.action==act)); saveQueue(); location.reload();
    }).catch(()=>{ location.reload();});
  }else{ setTimeout(()=>location.reload(),500);}
  setTimeout(()=>{tapping[key]=false;},3000);
 }
 function syncQueue(){if(queue.length==0||!navigator.onLine)return;fetch('/api/{{code}}/{{van.plate}}/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({items: queue})}).then(r=>r.json()).then(j=>{if(j.ok){queue=[];saveQueue();}});}
 let massTapping=false;
 function massDrop(act){
  {% if locked %} return; {% endif %}
  if(massTapping) return;
  let label = act=='dropped_school'? 'DROP ALL kids at school? Status will become At School + 50% progress' : 'PICK ALL kids at school? Status will become On way Home + 75% progress';
  if(!confirm(label))return;
  if(!navigator.onLine){alert('Need internet for mass');return;}
  massTapping=true;
  let btnId = act=='dropped_school'? 'massDropBtn' : 'massPickBtn';
  let btn=document.getElementById(btnId);
  let otherId = act=='dropped_school'? 'massPickBtn' : 'massDropBtn';
  let otherBtn=document.getElementById(otherId);
  if(btn) {btn.disabled=true; btn.innerText='⏳...';}
  if(otherBtn) otherBtn.disabled=true;
  fetch('/api/{{code}}/{{van.plate}}/mass', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:act})}).then(r=>r.json()).then(j=>{
    if(j.cooldown){alert(j.error); if(btn) {btn.disabled=false; btn.innerText= act=='dropped_school'? '🏫 DROP ALL AT SCHOOL' : '🚐 PICK ALL AT SCHOOL';} if(otherBtn) otherBtn.disabled=false; massTapping=false; return;}
    if(j.ok){ alert('✅ Mass '+act+' done: '+j.count+' kids | Status + Progress updated!'); location.reload();} else {alert('Failed'); if(btn) btn.disabled=false; if(otherBtn) otherBtn.disabled=false; massTapping=false;}
  }).catch(()=>{if(btn) btn.disabled=false; if(otherBtn) otherBtn.disabled=false; massTapping=false;});
 }
 let trafficTapping=false;
 function sendTraffic(){
  {% if locked %} return; {% endif %}
  if(trafficTapping) return;
  let re=document.getElementById('trafficReason');let ce=document.getElementById('trafficCustom');let msg=re.value;
  if(msg=='custom'){msg=ce.value.trim(); if(msg.length<5){alert('Type custom message 5+ chars'); ce.focus(); return;}}
  if(!msg){return;}
  if(!navigator.onLine){alert('Need internet');return;}
  trafficTapping=true;
  let btn = document.getElementById('trafficBtn'); btn.innerText='Sending...'; btn.disabled=true;
  fetch('/api/{{code}}/{{van.plate}}/traffic', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})}).then(r=>r.json()).then(j=>{
    if(j.cooldown){ alert('Wait 5 mins - already sent!'); btn.innerText='Wait 5m'; setTimeout(()=>{btn.innerText='🚨 SEND TO ALL PARENTS'; btn.disabled=false; trafficTapping=false;},3000); return; }
    btn.innerText='✅ Sent to '+ (j.count||0);
    setTimeout(()=>{btn.innerText='🚨 SEND TO ALL PARENTS'; btn.disabled=false; trafficTapping=false;},2500);
  }).catch(()=>{btn.innerText='🚨 SEND TO ALL PARENTS'; btn.disabled=false; trafficTapping=false;});
 }
 document.getElementById('trafficReason').addEventListener('change', function(){
   let c=document.getElementById('trafficCustom');let h=document.getElementById('customHint');
   if(this.value=='custom'){c.style.display='block';h.style.display='block';c.focus();}else{c.style.display='none';h.style.display='none';}
 });
</script>"""

@app.route("/driver/<code>/<plate>", methods=["GET", "POST"])
def driver_page(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school: return CSS + "<div class='card'><h2>🚫 School Deleted</h2></div>"
        vans = db_get_vans(code_norm)
        van = next((v for v in vans if v['plate']==plate_norm), None)
        if not van: return CSS + f"<div class='card'><h2>Van {plate_norm} Deleted</h2><a href='/admin/{code_norm}'>Admin</a></div>"
        real_pin = get_van_pin(van); cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}")
        if request.method == "POST":
            entered = (request.form.get('pin','') or '').strip()
            if entered == real_pin:
                resp = make_response(redirect(f"/driver/{code_norm}/{plate_norm}")); resp.set_cookie(f"driver_pin_{plate_norm}", real_pin, max_age=86400*30, httponly=True, samesite='Lax'); return resp
            else:
                from jinja2 import Template; return Template(CSS + DRIVER_PIN_HTML + "<p style='color:red;text-align:center'>Wrong PIN!</p>").render(plate=plate_norm, driver_name=van.get('driver_name',''), driver_phone=van.get('driver_phone',''))
        if cookie_pin!= real_pin:
            from jinja2 import Template; return Template(CSS + DRIVER_PIN_HTML).render(plate=plate_norm, driver_name=van.get('driver_name',''), driver_phone=van.get('driver_phone',''))
        locked = is_locked(school)
        kids_raw = db_get_kids(code_norm, plate_norm)
        for k in kids_raw:
            if k and maybe_auto_reset(k):
                safe_patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(k['id'])}&school_code=eq.{quote(code_norm)}", headers=h(), json={"times": k['times'], "status": k['status'], "absent": k['absent'], "dropped_home_ts": None})
        kids = {k['id']: k for k in kids_raw}
        for kid in kids.values():
            cnt=0
            if 'picked_home' in (kid.get('times',{}) or {}): cnt=25
            if 'dropped_school' in (kid.get('times',{}) or {}): cnt=50
            if 'picked_school' in (kid.get('times',{}) or {}): cnt=75
            if 'dropped_home' in (kid.get('times',{}) or {}): cnt=100
            kid['progress']=cnt
        from jinja2 import Template
        return Template(DRIVER_HTML).render(school_name=school.get('name',''), van=van, code=code_norm, kids=kids, locked=locked, today=str(date.today()), kampala_time=current_time_str())
    except Exception as e: return CSS + f"<div class='card'><h2>Driver Error</h2><p>{e}</p></div>"

@app.route("/driver/<code>/<plate>/logout")
def driver_logout(code, plate):
    resp = redirect(f"/driver/{normalize_code(code)}/{normalize_plate(plate)}"); resp.set_cookie(f"driver_pin_{normalize_plate(plate)}", "", max_age=0); return resp

@app.route("/api/<code>/<plate>/action", methods=["POST"])
def driver_action(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school: return jsonify({"ok": False}), 404
        if is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); vans = db_get_vans(code_norm); van = next((v for v in vans if v['plate']==plate_norm), None)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False}), 401
        data = request.get_json() or {}; kid_id = data.get('kid_id'); act = data.get('action')
        if not kid_id or not act: return jsonify({"ok": False}), 400
        key = f"{code_norm}_{plate_norm}_{kid_id}_{act}"
        if key in ACTION_COOLDOWN and time.time()-ACTION_COOLDOWN[key] < 3:
            return jsonify({"ok": False, "cooldown": True}), 429
        ACTION_COOLDOWN[key]=time.time()
        r=safe_get(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid_id)}&school_code=eq.{quote(code_norm)}&select=*", headers=h())
        if not r or not r.json(): return jsonify({"ok": False}), 404
        kid = r.json()[0]
        times = kid.get('times',{}) or {}
        if act in times and act not in ['present','absent']: return jsonify({"ok": True, "skipped": True})
        short = kampala_now().strftime("%I:%M %p"); full = kampala_now().strftime("%I:%M %p %d %b")
        patch = {}
        if act == 'picked_home':
            patch["status"]=f"On way to School - picked at {short}"; times['picked_home']=full; patch["times"]=times; patch["absent"]=False
            whatsapp_async(kid.get('parent_phone',''), "picked_home", [kid.get('name',''), short, plate_norm])
        elif act == 'dropped_school':
            patch["status"]=f"At School - arrived at {short}"; times['dropped_school']=full; patch["times"]=times
            whatsapp_async(kid.get('parent_phone',''), "dropped_school", [kid.get('name',''), short])
        elif act == 'picked_school':
            patch["status"]=f"On way Home - left at {short}"; times['picked_school']=full; patch["times"]=times
            whatsapp_async(kid.get('parent_phone',''), "picked_school", [kid.get('name',''), short, plate_norm])
        elif act == 'dropped_home':
            patch["status"]=f"Home Safe - {short}"; times['dropped_home']=full; patch["times"]=times; patch["dropped_home_ts"]=kampala_now().isoformat()
            whatsapp_async(kid.get('parent_phone',''), "dropped_home", [kid.get('name',''), short])
        elif act == 'absent':
            patch["status"]="ABSENT Today"; patch["absent"]=True
            whatsapp_async(kid.get('parent_phone',''), "absent", [kid.get('name',''), str(date.today()), plate_norm])
        elif act == 'present':
            patch["absent"]=False; patch["status"]="At Home - waiting for van"; patch["times"]={}; patch["dropped_home_ts"]=None
        safe_patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid_id)}&school_code=eq.{quote(code_norm)}", headers=h(), json=patch)
        safe_post(f"{SUPABASE_URL}/rest/v1/attendance_log", headers=h(), json={"school_code": code_norm, "kid_name": kid.get('name',''), "kid_id": kid_id, "van_plate": plate_norm, "action": act, "log_date": str(date.today()), "log_time": full})
        return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/mass", methods=["POST"])
def mass_action(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school or is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); vans = db_get_vans(code_norm); van = next((v for v in vans if v['plate']==plate_norm), None)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False}), 401
        key=f"mass_{code_norm}_{plate_norm}"
        if key in ACTION_COOLDOWN and time.time()-ACTION_COOLDOWN[key] < 5:
            return jsonify({"ok": False, "cooldown": True, "error":"Wait 5 sec - anti-spam"}), 429
        ACTION_COOLDOWN[key]=time.time()
        data = request.get_json() or {}; act = data.get('action',''); short = kampala_now().strftime("%I:%M %p"); full = kampala_now().strftime("%I:%M %p %d %b")
        kids = db_get_kids(code_norm, plate_norm); count=0
        if act == 'dropped_school':
            for kid in kids:
                if not kid or kid.get('absent'): continue
                if 'dropped_school' in (kid.get('times',{}) or {}): continue
                times = kid.get('times',{}) or {}; times['dropped_school']=full
                safe_patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid['id'])}&school_code=eq.{quote(code_norm)}", headers=h(), json={"status": f"At School - arrived at {short}", "times": times})
                whatsapp_async(kid.get('parent_phone',''), "dropped_school", [kid.get('name',''), short])
                safe_post(f"{SUPABASE_URL}/rest/v1/attendance_log", headers=h(), json={"school_code": code_norm, "kid_name": kid.get('name',''), "kid_id": kid['id'], "van_plate": plate_norm, "action": "dropped_school (mass)", "log_date": str(date.today()), "log_time": full})
                count+=1
            return jsonify({"ok": True, "count": count})
        elif act == 'picked_school':
            for kid in kids:
                if not kid or kid.get('absent'): continue
                if 'picked_school' in (kid.get('times',{}) or {}): continue
                times = kid.get('times',{}) or {}; times['picked_school']=full
                safe_patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid['id'])}&school_code=eq.{quote(code_norm)}", headers=h(), json={"status": f"On way Home - left at {short}", "times": times})
                whatsapp_async(kid.get('parent_phone',''), "picked_school", [kid.get('name',''), short, plate_norm])
                safe_post(f"{SUPABASE_URL}/rest/v1/attendance_log", headers=h(), json={"school_code": code_norm, "kid_name": kid.get('name',''), "kid_id": kid['id'], "van_plate": plate_norm, "action": "picked_school (mass)", "log_date": str(date.today()), "log_time": full})
                count+=1
            return jsonify({"ok": True, "count": count})
        return jsonify({"ok": False}), 400
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/traffic", methods=["POST"])
def traffic(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school or is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); vans = db_get_vans(code_norm); van = next((v for v in vans if v['plate']==plate_norm), None)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False}), 401
        key=f"traffic_{code_norm}_{plate_norm}"
        if key in TRAFFIC_COOLDOWN and time.time()-TRAFFIC_COOLDOWN[key] < 300:
            return jsonify({"ok": False, "cooldown": True}), 429
        raw_msg = (request.get_json() or {}).get('message','').strip()
        if not raw_msg or len(raw_msg) < 5: return jsonify({"ok": False}), 400
        clean_msg = raw_msg[:100]
        TRAFFIC_COOLDOWN[key]=time.time()
        kids = db_get_kids(code_norm, plate_norm)
        count=0
        for kid in kids:
            if not kid: continue
            whatsapp_async(kid.get('parent_phone',''), "traffic_alert", [kid.get('name',''), clean_msg, plate_norm])
            count+=1
        safe_post(f"{SUPABASE_URL}/rest/v1/attendance_log", headers=h(), json={"school_code": code_norm, "kid_name": f"ALL {plate_norm}", "kid_id": "TRAFFIC", "van_plate": plate_norm, "action": f"traffic: {clean_msg}", "log_date": str(date.today()), "log_time": kampala_now().strftime("%I:%M %p %d %b")})
        return jsonify({"ok": True, "count": count})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/sync", methods=["POST"])
def sync(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school or is_locked(school): return jsonify({"ok": False}), 403
        data = request.get_json() or {}; items = data.get('items',[]) or []
        for it in items[:100]:
            kid_id = it.get('kid_id'); act = it.get('action')
            if not kid_id or not act: continue
            r=safe_get(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid_id)}&school_code=eq.{quote(code_norm)}&select=*", headers=h())
            if not r or not r.json(): continue
            kid = r.json()[0]; times = kid.get('times',{}) or {}
            if act in times and act not in ['present','absent']: continue
            short = kampala_now().strftime("%I:%M %p"); full = kampala_now().strftime("%I:%M %p %d %b")
            patch={}
            if act=='picked_home': patch={"status": f"On way to School - picked at {short}", "times": {**times, "picked_home": full}, "absent": False}
            elif act=='dropped_school': patch={"status": f"At School - arrived at {short}", "times": {**times, "dropped_school": full}}
            elif act=='picked_school': patch={"status": f"On way Home - left at {short}", "times": {**times, "picked_school": full}}
            elif act=='dropped_home': patch={"status": f"Home Safe - {short}", "times": {**times, "dropped_home": full}, "dropped_home_ts": kampala_now().isoformat()}
            elif act=='absent': patch={"status": "ABSENT Today", "absent": True}
            elif act=='present': patch={"status": "At Home - waiting for van", "times": {}, "absent": False, "dropped_home_ts": None}
            if patch: safe_patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid_id)}&school_code=eq.{quote(code_norm)}", headers=h(), json=patch)
        return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/p/<kid_id>")
def parent_page(kid_id):
    try:
        r=safe_get(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{quote(kid_id)}&select=*", headers=h())
        if not r or not r.json(): return CSS + "<div class='card'><h2>Kid not found</h2></div>"
        kid = r.json()[0]
        maybe_auto_reset(kid)
        cnt=0
        if 'picked_home' in (kid.get('times',{}) or {}): cnt=25
        if 'dropped_school' in (kid.get('times',{}) or {}): cnt=50
        if 'picked_school' in (kid.get('times',{}) or {}): cnt=75
        if 'dropped_home' in (kid.get('times',{}) or {}): cnt=100
        html = CSS + f"<h2>{kid.get('name','')} - {kid.get('status','')}</h2><div class='card'><div class='progress'><div class='progress-fill' style='width:{cnt}%'></div></div><p>{cnt}% complete</p><p>Status: {kid.get('status','')}</p>"
        for k,v in (kid.get('times',{}) or {}).items(): html+=f"<p>{k}: {v}</p>"
        html+="</div><script>setTimeout(()=>location.reload(),30000)</script>"
        return html
    except Exception as e: return CSS + f"<div class='card'><p>{e}</p></div>"

@app.route("/report/<code>")
def report_daily(code):
    try:
        code_norm = normalize_code(code); logs = db_get_logs(code_norm, 200)
        html = CSS + f"<h2>Daily Report {code_norm} - {date.today()}</h2><div class='card'><a href='/report/{code_norm}/weekly'>Weekly</a> | <a href='/admin/{code_norm}'>Admin</a><br><br><table border=1 cellpadding=8 style='width:100%;border-collapse:collapse'><tr><th>Time</th><th>Kid</th><th>Van</th><th>Action</th></tr>"
        for lg in logs: html+=f"<tr><td>{lg.get('log_time','')}</td><td>{lg.get('kid_name','')}</td><td>{lg.get('van_plate','')}</td><td>{lg.get('action','')}</td></tr>"
        html+="</table></div>"; return html
    except Exception as e: return CSS + f"<p>{e}</p>"

@app.route("/report/<code>/weekly")
def report_weekly(code):
    try:
        code_norm = normalize_code(code); logs = db_get_logs(code_norm, 1000)
        html = CSS + f"<h2>Weekly Report {code_norm}</h2><div class='card'><a href='/report/{code_norm}'>Daily</a> | <a href='/admin/{code_norm}'>Admin</a><br><br>"
        html+="<table border=1 cellpadding=8 style='width:100%;border-collapse:collapse'><tr><th>Date</th><th>Kid</th><th>Action</th><th>Van</th></tr>"
        for lg in logs: html+=f"<tr><td>{lg.get('log_date','')}</td><td>{lg.get('kid_name','')}</td><td>{lg.get('action','')}</td><td>{lg.get('van_plate','')}</td></tr>"
        html+="</table></div>"; return html
    except Exception as e: return CSS + f"<p>{e}</p>"

@app.route("/manifest.json")
def manifest():
    return jsonify({"name":"FIKISHA","short_name":"FIKISHA","start_url":"/","display":"standalone","background_color":"#FFF8E1","theme_color":"#0D2A54","icons":[{"src":"https://via.placeholder.com/192","sizes":"192x192","type":"image/png"}]})

@app.route("/sw.js")
def sw():
    js="self.addEventListener('fetch',e=>{e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))})"
    return Response(js, mimetype="application/javascript")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))