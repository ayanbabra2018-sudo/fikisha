from flask import Flask, request, redirect, jsonify, Response, make_response
import json, os, requests, csv, io, threading, shutil, uuid
from datetime import datetime, date, timedelta
try:
    from zoneinfo import ZoneInfo
    KAMPALA_TZ = ZoneInfo("Africa/Kampala")
except:
    KAMPALA_TZ = None

def kampala_now():
    try:
        if KAMPALA_TZ:
            return datetime.now(KAMPALA_TZ)
        return datetime.now() + timedelta(hours=3)
    except:
        return datetime.now()

def normalize_plate(p):
    try:
        return str(p).upper().strip().replace(" ", "") if p else ""
    except:
        return ""

def normalize_code(p):
    try:
        return str(p).upper().strip().replace(" ", "") if p else ""
    except:
        return ""

app = Flask(__name__)
DB_LOCK = threading.RLock()
SUPABASE_URL = os.environ.get("SUPABASE_URL","").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY","")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "fikisha2026")

def h(): return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
def hr(): return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}

# --- UNLIMITED HELPERS - ONLY IMPROVEMENT ---
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

def db_get_logs(code, limit=200):
    code=normalize_code(code)
    r=requests.get(f"{SUPABASE_URL}/rest/v1/attendance_log?school_code=eq.{code}&select=*&order=created_at.desc&limit={limit}", headers=h(), timeout=10)
    return r.json() if r.status_code==200 else []

def load_db():
    # OLD compatibility - loads unlimited tables into old structure for super admin page
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"schools": {}}
    try:
        r=requests.get(f"{SUPABASE_URL}/rest/v1/schools?select=*", headers=h(), timeout=15)
        schools=r.json() if r.status_code==200 else []
        result={"schools":{}}
        for s in schools:
            code=s['code']
            vans={v['plate']: {"plate": v['plate'], "driver_name": v.get('driver_name',''), "driver_phone": v.get('driver_phone','')} for v in db_get_vans(code)}
            kids={k['id']: k for k in db_get_kids(code)}
            result["schools"][code]={"name": s.get('name',''), "code": code, "director_phone": s.get('director_phone',''), "paid_until": s.get('paid_until','2026-12-31'), "vans": vans, "kids": kids, "attendance_log": db_get_logs(code), "last_traffic": {}}
        return result
    except:
        return {"schools": {}}

def save_db(db):
    return True # no-op for unlimited, we save per table

def to_wa(p):
    try:
        if not p: return ""
        c = str(p).replace("+","").replace(" ","").replace("-","").strip()
        if not c: return ""
        if c.startswith("0"): c = "256" + c[1:]
        if len(c)==9: c = "256"+c
        return c
    except:
        return ""

def current_time_str():
    try:
        return kampala_now().strftime("%I:%M %p")
    except:
        return ""

def whatsapp_template(to, template_name, params=[]):
    token = os.environ.get("WHATSAPP_TOKEN"); phone_id = os.environ.get("WHATSAPP_PHONE_ID", "1327812003752192")
    if not token or not to: return False, "no token"
    clean = to_wa(to)
    if len(clean) < 11: return False, f"bad phone {to} -> {clean}"
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body_params = [{"type": "text", "text": str(p)[:100]} for p in (params or [])]
    data = {"messaging_product": "whatsapp","to": clean,"type": "template","template": {"name": template_name,"language": {"code": "en_US"},"components": [{"type": "body", "parameters": body_params}] if body_params else []}}
    try:
        r = requests.post(url, headers=headers, json=data, timeout=10); return r.status_code == 200, r.text
    except Exception as e:
        return False, str(e)

def whatsapp_async(to, template_name, params=[]):
    threading.Thread(target=whatsapp_template, args=(to, template_name, params), daemon=True).start()

def is_locked(school):
    try:
        if not school or 'paid_until' not in school: return True
        return str(school.get('paid_until','')) < str(date.today())
    except:
        return True

def maybe_auto_reset(kid):
    try:
        ts = kid.get('dropped_home_ts')
        if not ts or 'dropped_home' not in (kid.get('times',{}) or {}): return False
        dropped_time = datetime.fromisoformat(ts); now = kampala_now()
        if dropped_time.tzinfo is None:
            now_cmp = now.replace(tzinfo=None) if now.tzinfo else now
        else:
            now_cmp = now
            if now_cmp.tzinfo is None and dropped_time.tzinfo:
                dropped_time = dropped_time.replace(tzinfo=None)
        if (now_cmp - dropped_time) > timedelta(hours=2):
            kid['times'] = {}; kid['status'] = "At Home - waiting for van"; kid['absent'] = False; kid.pop('dropped_home_ts', None); return True
    except:
        try:
            kid['times'] = {}; kid['status'] = "At Home - waiting for van"; kid['absent'] = False; kid.pop('dropped_home_ts', None); return True
        except:
            pass
    return False

def get_van_pin(van):
    try:
        phone = to_wa((van or {}).get('driver_phone','')); return phone[-4:] if len(phone)>=4 else "1234"
    except:
        return "1234"

def get_admin_pin(school):
    try:
        phone = to_wa((school or {}).get('director_phone','')); return phone[-4:] if len(phone)>=4 else "1234"
    except:
        return "1234"

CSS = """<style>
body{font-family:system-ui,Arial;background:#FFF8E1;margin:0;padding:15px;color:#1a1a1a;font-size:17px;line-height:1.5}
h2{color:#0D2A54;border-bottom:3px solid #FFC107;padding-bottom:8px;font-size:22px}
h3{font-size:19px}
.card{background:white;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 4px 14px rgba(0,0,0,0.1);border-top:5px solid #FFC107;font-size:17px}
input,select{padding:14px!important;border-radius:10px;border:2px solid #FFC107;margin:6px;width:90%;font-size:16px!important}
button{padding:14px 20px!important;border-radius:10px;border:none;background:#0D2A54;color:#FFC107;font-weight:bold;cursor:pointer;margin:5px;font-size:16px!important;min-height:48px}
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
a{color:#1565c0;font-weight:bold;text-decoration:none}
table{width:100%;border-collapse:collapse}
th,td{padding:10px;text-align:left;border-bottom:1px solid #eee;font-size:15px}
small,label{font-size:15px!important}
@media print{button,.no-print{display:none}}
</style>"""

@app.route("/", methods=["GET", "POST"])
def home():
    try:
        if request.method == "POST":
            pwd = request.form.get('password','')
            if pwd == SUPER_ADMIN_PASSWORD:
                resp = redirect("/"); resp.set_cookie("super_auth", SUPER_ADMIN_PASSWORD, max_age=86400*7, httponly=True, samesite='Lax'); return resp
            else:
                return CSS + "<div class='card' style='background:#ffcccc'><h2>Wrong password</h2><a href='/'>Try again</a></div>"
        auth = request.cookies.get("super_auth")
        if auth!= SUPER_ADMIN_PASSWORD:
            return CSS + """<div class='card' style='max-width:400px;margin:80px auto;text-align:center'><h2>🔐 FIKISHA Super Admin</h2><form method="post"><input name="password" type="password" placeholder="Password" required style="width:90%"><br><button style="width:95%;margin-top:10px">Unlock</button></form></div>"""
        db = load_db()
        html = CSS + f"<h2>FIKISHA - Super Admin (Kampala: {current_time_str()}) | Supabase Unlimited ✅</h2>"
        html += """<div class="card"><h3>Create New School</h3><form method="post" action="/create_school"><input name="school_name" placeholder="Bright Angels" required><input name="code" placeholder="Code BRIGHT123" required><input name="director" placeholder="Director WhatsApp 2567..." required><input name="paid_until" type="date" required><button>Add School +</button></form></div><hr>"""
        for code, s in (db.get("schools", {}) or {}).items():
            if not s: continue
            lock = "🔴 EXPIRED" if is_locked(s) else "🟢 Active"
            html += f"""<div class='card' style="{'border:3px solid red' if is_locked(s) else ''}"><b>{s.get('name','')}</b> ({code}) - {lock} - Paid: {s.get('paid_until','?')} - Director: {s.get('director_phone','')} PIN:{get_admin_pin(s)}<br> <form method='post' action='/super/update_school/{code}' style='margin:10px 0;background:#FFF8E1;padding:10px;border-radius:10px'> <b>Edit School:</b><br><input name='school_name' value='{s.get('name','')}' required style='width:28%'><input name='director' value='{s.get('director_phone','')}' required style='width:28%'><input type='date' name='paid_until' value='{s.get('paid_until','')}' required style='width:28%'><button style='padding:6px 12px'>Save Edit ✅</button></form> <a href='/admin/{code}'>Manage (PIN)</a> | <a href='/report/{code}'>Daily</a> | <a href='/report/{code}/weekly'>Weekly</a> | <a href='/super/delete_school/{code}' onclick="return confirm('DELETE {code}?')" style='color:red'>Delete ❌</a><br><br> <button onclick="shareAdmin('{code}','{s.get('name','')}','{s.get('director_phone','')}','{get_admin_pin(s)}')" class="btn-blue">📲 Share Admin + PIN</button><br>"""
            for vp, van in (s.get('vans',{}) or {}).items():
                html += f"Van <b>{vp}</b> - {(van or {}).get('driver_name','')} PIN:{get_van_pin(van)} - <a href='/driver/{code}/{vp}'>Driver</a><br>"
            html += "</div>"
        html += """<script>function toWa(phone){let c=(phone||'').replace(/[^0-9]/g,'').trim();if(c.startsWith('0'))c='256'+c.substring(1);if(c.length==9)c='256'+c;return c;} function shareAdmin(code,name,phone,pin){let link=window.location.origin+"/admin/"+code;let msg="FIKISHA Admin link for "+name+" ("+code+"): "+link+"\\nPIN: "+pin;let clean=toWa(phone);window.open("https://wa.me/"+clean+"?text="+encodeURIComponent(msg),"_blank");}</script>"""
        return html
    except Exception as e:
        return CSS + f"<div class='card'><h2>Super Admin Error</h2><p>{e}</p></div>"

@app.route("/create_school", methods=["POST"])
def create_school():
    try:
        code = normalize_code(request.form.get('code',''))
        if not code: return "Code required"
        requests.post(f"{SUPABASE_URL}/rest/v1/schools", headers=hr(), json={"code": code, "name": request.form.get('school_name','School'), "director_phone": request.form.get('director',''), "paid_until": request.form.get('paid_until', str(date.today()))}, timeout=10)
        return redirect("/")
    except Exception as e:
        return f"Create error {e} <a href='/'>back</a>"

@app.route("/super/update_school/<code>", methods=["POST"])
def update_school(code):
    try:
        code_n = normalize_code(code)
        requests.patch(f"{SUPABASE_URL}/rest/v1/schools?code=eq.{code_n}", headers=h(), json={"name": request.form.get('school_name'), "director_phone": request.form.get('director'), "paid_until": request.form.get('paid_until')}, timeout=10)
        return redirect("/")
    except:
        return redirect("/")

@app.route("/super/delete_school/<code>")
def delete_school(code):
    try:
        code_n = normalize_code(code)
        requests.delete(f"{SUPABASE_URL}/rest/v1/schools?code=eq.{code_n}", headers=h(), timeout=10)
        requests.delete(f"{SUPABASE_URL}/rest/v1/vans?school_code=eq.{code_n}", headers=h(), timeout=10)
        requests.delete(f"{SUPABASE_URL}/rest/v1/kids?school_code=eq.{code_n}", headers=h(), timeout=10)
        requests.delete(f"{SUPABASE_URL}/rest/v1/attendance_log?school_code=eq.{code_n}", headers=h(), timeout=10)
    except:
        pass
    return redirect("/")
ADMIN_LOGIN_HTML = CSS + """<div class='card' style='max-width:400px;margin:80px auto;text-align:center'><h2>🔐 Admin PIN for {{school_name}} ({{code}})</h2><p>Director: {{director_phone}}<br>Enter PIN</p><form method="post"><input name="pin" type="password" placeholder="4-digit PIN" required style="text-align:center;font-size:22px;letter-spacing:8px" maxlength="4"><br><button style="width:95%">Unlock Admin</button></form><p style="font-size:12px;color:#666">Super password also works</p>{% if error %}<p style="color:red">{{error}}</p>{% endif %}</div>"""

ADMIN_HTML = CSS + """ {% if locked %}<div class='card' style='background:#ffcccc;border:3px solid red;text-align:center'><h1>🚫 PAYMENT EXPIRED</h1><p>Paid until {{school_paid_until}}</p></div>{% endif %} <h2>{{school_name}} Admin ({{code}}) - <a href="/admin/{{code}}/logout" style="font-size:12px">Logout PIN</a></h2> <p>{{kampala_time}} | Unlimited ✅ | <a href="/report/{{code}}">Daily</a> | <a href="/report/{{code}}/weekly">Weekly 📊</a> | <a href="/">Super</a></p> <div class="card"><h3>Daily Control</h3>{% if locked %}<button disabled>🔒 Locked</button>{% else %}<form method="post" action="/api/{{code}}/reset_all"><button style="background:#0a7a2a;width:100%">RESET ALL FOR TOMORROW</button></form>{% endif %}</div> <div class="card"><h3>Add Van</h3>{% if locked %}<p>🔒 Locked</p>{% else %}<form method="post" action="/admin/{{code}}/add_van"><input name="plate" placeholder="Plate UAA123A" required><input name="driver_name" placeholder="Driver Name" required><input name="driver_phone" placeholder="Driver Phone 2567..." required><button>Add Van</button></form>{% endif %}</div> <div class="card"><h3>Add Kid to Van (Unlimited)</h3>{% if locked %}<p>🔒 Locked</p>{% elif vans_count==0 %}<p>Add Van first!</p>{% else %}<form method="post" action="/admin/{{code}}/add_kid"><input name="kid_name" placeholder="Kid Name" required><input name="stage" placeholder="Stage" required><input name="parent_phone" placeholder="Parent WhatsApp 2567..." required>Van: <select name="van_plate" required>{% for vp in vans %}<option value="{{vp}}">{{vp}}</option>{% endfor %}</select><button>Add Kid</button></form>{% endif %}</div> <hr><h3>Vans & Kids ({{total_kids}}) - Only {{code}}</h3> {% for vp, van in vans_items %} <div class="card"><b>{{vp}} - {{van.driver_name}}</b> - {{van.driver_phone}} - PIN: {{van.pin}} - <a href="/driver/{{code}}/{{vp}}">Driver Page</a><br> <div style="margin:8px 0"><button onclick="shareDriver('{{vp}}','{{van.driver_name}}','{{van.driver_phone}}','{{van.pin}}','{{code}}')" class="btn-done" style="padding:8px 14px">📲 Share Driver + PIN</button> <button onclick="copyLink(window.location.origin+'/driver/{{code}}/{{vp}}')" class="btn-blue" style="padding:8px 14px">🔗 Copy Driver Link</button></div> {% if not locked %}<form method="post" action="/admin/{{code}}/edit_van/{{vp}}" style="background:#FFF8E1;padding:8px;border-radius:8px;margin:8px 0"><input name="driver_name" value="{{van.driver_name}}" required style="width:30%"> <input name="driver_phone" value="{{van.driver_phone}}" required style="width:35%"> <button style="padding:6px 10px">Save Van</button> | <a href="/admin/{{code}}/delete_van/{{vp}}" onclick="return confirm('Delete van {{vp}}?')" style="color:red">Delete Van ❌</a></form>{% endif %} {% for kid in kids_list if kid.van_plate==vp %} <div style="margin:8px 0;padding:10px;background:#FFF8E1;border-radius:10px"><div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px"><span>👦 <b>{{kid.name}}</b> ({{kid.stage}}) - {{kid.status_short}}<br>Parent: {{kid.parent_phone}} | <a href="/p/{{kid.id}}" target="_blank">Parent Link 👁️</a></span><div><button onclick="shareParent('{{kid.name}}','{{kid.id}}','{{kid.parent_phone}}')" class="btn-blue" style="padding:6px 10px;font-size:11px">📲 Share Parent</button>{% if not locked %}<a href="#" onclick="document.getElementById('edit-{{kid.id}}').style.display='block';return false;" style="font-size:11px">✏️ Edit</a> | <a href="/admin/{{code}}/delete_kid/{{kid.id}}" onclick="return confirm('Delete {{kid.name}}?')" style="color:red;font-size:11px">❌ Delete</a>{% endif %}</div></div> {% if not locked %}<div id="edit-{{kid.id}}" style="display:none;background:white;padding:8px;border-radius:8px;margin-top:8px"><form method="post" action="/admin/{{code}}/edit_kid/{{kid.id}}"><input name="kid_name" value="{{kid.name}}" required style="width:22%"> <input name="stage" value="{{kid.stage}}" style="width:18%"> <input name="parent_phone" value="{{kid.parent_phone}}" required style="width:28%"><select name="van_plate" style="width:18%">{% for vvp in vans %}<option value="{{vvp}}" {% if vvp==kid.van_plate %}selected{% endif %}>{{vvp}}</option>{% endfor %}</select><button style="padding:6px 10px">Save Kid</button> <button type="button" onclick="document.getElementById('edit-{{kid.id}}').style.display='none'" class="btn-grey" style="padding:6px">Cancel</button></form></div>{% endif %}</div> {% endfor %}</div>{% endfor %} <script> function toWa(phone){let c=(phone||'').replace(/[^0-9]/g,'').trim();if(c.startsWith('0'))c='256'+c.substring(1);if(c.length==9)c='256'+c;return c;} function shareParent(name,kidId,phone){let clean=toWa(phone);let link=window.location.origin+"/p/"+kidId;let msg="Hello, track "+name+" live on FIKISHA: "+link;if(clean.length<11){alert('Phone wrong: '+phone);copyLink(link);return;}window.open("https://wa.me/"+clean+"?text="+encodeURIComponent(msg),"_blank");} function shareDriver(plate, driverName, driverPhone, pin, code){let clean=toWa(driverPhone);let link=window.location.origin+"/driver/"+code+"/"+plate;let msg="Hello "+driverName+", van "+plate+": "+link+"\\nPIN: "+pin;if(clean.length<11){alert('Driver phone wrong');copyLink(link);return;}window.open("https://wa.me/"+clean+"?text="+encodeURIComponent(msg),"_blank");} function copyLink(text){navigator.clipboard.writeText(text).then(()=>{alert('Copied: '+text);}).catch(()=>{prompt('Copy:', text);});} </script> """

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
                from jinja2 import Template; return Template(ADMIN_LOGIN_HTML).render(school_name=school.get('name',''), code=code_norm, director_phone=school.get('director_phone',''), error="Wrong PIN")
        if admin_cookie!= real_pin and super_auth!= SUPER_ADMIN_PASSWORD:
            from jinja2 import Template; return Template(ADMIN_LOGIN_HTML).render(school_name=school.get('name',''), code=code_norm, director_phone=school.get('director_phone',''), error=None)
        vans_raw = db_get_vans(code_norm)
        vans = {v['plate']: v for v in vans_raw}
        for v in vans.values():
            if v: v['pin']=get_van_pin(v)
        kids_raw = db_get_kids(code_norm)
        # auto-reset check
        for k in kids_raw:
            if k and maybe_auto_reset(k):
                requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{k['id']}&school_code=eq.{code_norm}", headers=h(), json={"times": k['times'], "status": k['status'], "absent": k['absent']}, timeout=10)
        vans_items = list(vans.items())
        kids_list = []
        for kid in kids_raw:
            if not kid: continue
            kid['status_short'] = (kid.get('status','') or '')[:30]
            kids_list.append(kid)
        from jinja2 import Template
        return Template(ADMIN_HTML).render(school_name=school.get('name',''), school_paid_until=school.get('paid_until',''), code=code_norm, locked=is_locked(school), kampala_time=current_time_str(), total_kids=len(kids_list), vans=vans.keys(), vans_items=vans_items, kids_list=kids_list, vans_count=len(vans))
    except Exception as e:
        print(f"Admin crash: {e}"); return CSS + f"<div class='card' style='background:#ffcccc'><h2>Admin Error</h2><p>{e}</p><a href='/'>Home</a></div>"

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
        requests.post(f"{SUPABASE_URL}/rest/v1/vans", headers=hr(), json={"school_code": code_norm, "plate": plate, "driver_name": request.form.get('driver_name','Driver'), "driver_phone": request.form.get('driver_phone','')}, timeout=10)
    except Exception as e:
        print(f"add_van crash {e}")
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/edit_van/<plate>", methods=["POST"])
def edit_van(code, plate):
    try:
        c=normalize_code(code); p=normalize_plate(plate); school = db_get_school(c)
        if school and not is_locked(school):
            if request.cookies.get(f"admin_pin_{c}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{c}")
            requests.patch(f"{SUPABASE_URL}/rest/v1/vans?school_code=eq.{c}&plate=eq.{p}", headers=h(), json={"driver_name": request.form.get('driver_name','Driver'), "driver_phone": request.form.get('driver_phone','')}, timeout=10)
    except:
        pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/delete_van/<plate>")
def delete_van_route(code, plate):
    try:
        c=normalize_code(code); p=normalize_plate(plate); school = db_get_school(c)
        if school and not is_locked(school):
            if request.cookies.get(f"admin_pin_{c}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{c}")
            requests.delete(f"{SUPABASE_URL}/rest/v1/vans?school_code=eq.{c}&plate=eq.{p}", headers=h(), timeout=10)
            # delete kids in that van - isolated
            requests.delete(f"{SUPABASE_URL}/rest/v1/kids?school_code=eq.{c}&van_plate=eq.{p}", headers=h(), timeout=10)
    except:
        pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/delete_kid/<kid_id>")
def delete_kid_route(code, kid_id):
    try:
        c=normalize_code(code); school = db_get_school(c)
        if school and not is_locked(school):
            if request.cookies.get(f"admin_pin_{c}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{c}")
            requests.delete(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{c}", headers=h(), timeout=10)
    except:
        pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/add_kid", methods=["POST"])
def add_kid_route(code):
    try:
        kid_id = str(uuid.uuid4())[:8].upper(); code_norm = normalize_code(code); school = db_get_school(code_norm)
        if not school or is_locked(school): return redirect(f"/admin/{code_norm}")
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{code_norm}")
        van_plate = normalize_plate(request.form.get('van_plate',''))
        requests.post(f"{SUPABASE_URL}/rest/v1/kids", headers=h(), json={"id": kid_id, "school_code": code_norm, "name": request.form.get('kid_name','Kid'), "stage": request.form.get('stage',''), "parent_phone": request.form.get('parent_phone',''), "van_plate": van_plate, "status": "At Home - waiting for van", "times": {}, "absent": False}, timeout=10)
    except Exception as e:
        print(f"add_kid crash {e}")
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/admin/<code>/edit_kid/<kid_id>", methods=["POST"])
def edit_kid_route(code, kid_id):
    try:
        c=normalize_code(code); school = db_get_school(c)
        if school and not is_locked(school):
            if request.cookies.get(f"admin_pin_{c}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{c}")
            new_van = normalize_plate(request.form.get('van_plate',''))
            requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{c}", headers=h(), json={"name": request.form.get('kid_name'), "stage": request.form.get('stage'), "parent_phone": request.form.get('parent_phone'), "van_plate": new_van}, timeout=10)
    except:
        pass
    return redirect(f"/admin/{normalize_code(code)}")

@app.route("/api/<code>/reset_all", methods=["POST"])
def reset_all(code):
    try:
        code_norm = normalize_code(code); school = db_get_school(code_norm)
        if not school or is_locked(school): return CSS + "<div class='card'><h2>🚫 EXPIRED</h2></div>"
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD: return redirect(f"/admin/{code_norm}")
        kids = db_get_kids(code_norm)
        for kid in kids:
            requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid['id']}&school_code=eq.{code_norm}", headers=h(), json={"times": {}, "status": "At Home - waiting for van", "absent": False, "dropped_home_ts": None}, timeout=10)
    except:
        pass
    return redirect(f"/admin/{normalize_code(code)}")
DRIVER_PIN_HTML = CSS + """<div class='card' style='max-width:400px;margin:80px auto;text-align:center'><h2>🔐 Driver PIN for Van {{plate}}</h2><p>Driver: {{driver_name}}<br>Enter PIN</p><form method="post"><input name="pin" type="password" placeholder="4-digit PIN" required style="text-align:center;font-size:22px;letter-spacing:8px" maxlength="4"><br><button style="width:95%">Unlock</button></form></div>"""

DRIVER_HTML = CSS + """<link rel="manifest" href="/manifest.json">{% if locked %}<div class='card' style='background:#ffcccc;border:3px solid red;text-align:center'><h1>🚫 PAYMENT EXPIRED</h1></div>{% endif %}<h2>Driver: {{van.driver_name}} - Van {{van.plate}} - {{school_name}} - <a href="/driver/{{code}}/{{van.plate}}/logout" style="font-size:12px">Logout PIN</a></h2><div id="netStatus" style="padding:8px;border-radius:8px;text-align:center;font-weight:bold">Checking...</div><p>Code: {{code}} | {{kampala_time}} | {{today}} | {{kids|length}} kids - Only {{code}}</p><div style="display:flex;gap:12px;justify-content:space-between;flex-wrap:nowrap"><div class="card" style="flex:1;min-width:0;border:2px solid #0a7a2a;background:#e8f5e9;margin:0"><h3 style="color:#0a7a2a;margin-top:0;font-size:13px;text-align:center">🏫 Quick Drop</h3><button class="btn-done" onclick="massDrop('dropped_school')" {% if locked %}disabled{% endif %} style="width:100%;font-size:13px;padding:12px;border-radius:12px">🏫 DROP ALL AT SCHOOL</button></div><div style="width:12px;flex-shrink:0"></div><div class="card" style="flex:1;min-width:0;border:2px solid #d32f2f;margin:0"><h3 style="color:#d32f2f;margin-top:0;font-size:13px;text-align:center">🚨 Alert All Parents</h3><select id="trafficReason" {% if locked %}disabled{% endif %} style="width:100%;padding:8px;border:2px solid #d32f2f;font-size:11px"><option value="Heavy traffic - 15 mins late">Traffic - 15 mins late</option><option value="Heavy traffic - 30 mins late">Traffic - 30 mins late</option><option value="Tyre puncture - fixing, 20 mins delay">Puncture - 20 mins</option><option value="Fuel stop - 10 mins delay">Fuel - 10 mins</option><option value="custom">✏️ Custom</option></select><input id="trafficCustom" placeholder="Custom 80 chars" style="width:95%;display:none;margin-top:6px" maxlength="80"><button class="btn-red" onclick="sendTraffic()" {% if locked %}disabled{% endif %} style="width:100%;margin-top:8px;padding:10px;font-size:12px">🚨 SEND TO ALL PARENTS</button></div></div><hr><div class="grid">{% for kid_id, kid in kids.items() %}<div class="card"><b>{{kid.name}}</b> - {{kid.stage}} - {{kid.parent_phone}}<br>Status: <span class="badge">{{kid.status}}</span><div class="progress"><div class="progress-fill" style="width: {{kid.progress}}%"></div></div><br><button onclick="action('{{kid.id}}','picked_home')" {% if locked %}disabled{% endif %}>PICKED HOME</button><button onclick="action('{{kid.id}}','dropped_school')" {% if locked %}disabled{% endif %}>DROPPED SCHOOL</button><button onclick="action('{{kid.id}}','picked_school')" {% if locked %}disabled{% endif %}>PICKED SCHOOL</button><button onclick="action('{{kid.id}}','dropped_home')" {% if locked %}disabled{% endif %}>DROPPED HOME</button><br><button class="btn-orange" onclick="action('{{kid.id}}','absent')" {% if locked %}disabled{% endif %}>ABSENT</button><button class="btn-grey" onclick="action('{{kid.id}}','present')" {% if locked %}disabled{% endif %}>BACK</button></div>{% endfor %}</div><script> if('serviceWorker' in navigator){ navigator.serviceWorker.register('/sw.js').catch(()=>{}); } let queue = JSON.parse(localStorage.getItem('fikisha_queue_{{van.plate}}')||'[]'); if(queue.length>100){ queue=queue.slice(-100); localStorage.setItem('fikisha_queue_{{van.plate}}', JSON.stringify(queue)); } function updateNet(){let el=document.getElementById('netStatus');if(navigator.onLine){el.innerText='✅ ONLINE | Queued: '+queue.length;el.style.background='#e8f5e9';if(queue.length>0)syncQueue();}else{el.innerText='⚠️ OFFLINE - saved | Queued: '+queue.length;el.style.background='#fff3cd';}} window.addEventListener('online', updateNet); window.addEventListener('offline', updateNet); updateNet(); function saveQueue(){if(queue.length>100)queue=queue.slice(-100);localStorage.setItem('fikisha_queue_{{van.plate}}', JSON.stringify(queue));updateNet();} let tapping = {}; function action(kid_id, act){ {% if locked %} return; {% endif %} let key = kid_id + '_' + act; if(tapping[key]) return; tapping[key]=true; let btns=document.querySelectorAll(`button[onclick*="'${kid_id}'"]`); btns.forEach(b=>{b.disabled=true; b.style.opacity='0.5'; b.innerText='✓';}); queue.push({kid_id, action:act, time: new Date().toISOString()}); saveQueue(); if(navigator.onLine){fetch('/api/{{code}}/{{van.plate}}/action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({kid_id, action:act})}).then(r=>r.json()).then(j=>{ queue=queue.filter(q=>!(q.kid_id==kid_id && q.action==act)); saveQueue(); location.reload();}).catch(()=>{ location.reload();});}else{ location.reload();}} function syncQueue(){if(queue.length==0||!navigator.onLine)return;fetch('/api/{{code}}/{{van.plate}}/sync', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({items: queue})}).then(r=>r.json()).then(j=>{if(j.ok){queue=[];saveQueue();}});} let massTapping=false; function massDrop(act){ {% if locked %} return; {% endif %} if(massTapping) return; if(!confirm('DROP ALL at school?'))return; if(!navigator.onLine){return;} massTapping=true; fetch('/api/{{code}}/{{van.plate}}/mass', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:act})}).then(r=>r.json()).then(j=>{ location.reload();});} let trafficTapping=false; function sendTraffic(){ {% if locked %} return; {% endif %} if(trafficTapping) return; let re=document.getElementById('trafficReason');let ce=document.getElementById('trafficCustom');let msg=re.value;if(msg=='custom'){msg=ce.value.trim();}if(!msg){return;}if(!navigator.onLine){return;} trafficTapping=true; let btn = document.querySelector(`button[onclick="sendTraffic()"]`); if(btn){btn.innerText='Sending...'; btn.disabled=true;} fetch('/api/{{code}}/{{van.plate}}/traffic', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})}).then(r=>r.json()).then(j=>{ trafficTapping=false; if(j.cooldown){ if(btn){btn.innerText='Wait 5m';} setTimeout(()=>{ if(btn){btn.innerText='🚨 SEND TO ALL PARENTS'; btn.disabled=false;}}, 2000); return; } if(btn){btn.innerText='✅ Sent';} setTimeout(()=>{ if(btn){btn.innerText='🚨 SEND TO ALL PARENTS'; btn.disabled=false;}}, 1200);}).catch(()=>{ trafficTapping=false; if(btn){btn.innerText='🚨 SEND TO ALL PARENTS'; btn.disabled=false;}});} document.getElementById('trafficReason').addEventListener('change', function(){let c=document.getElementById('trafficCustom');if(this.value=='custom'){c.style.display='block';}else{c.style.display='none';}}); </script>"""

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
                patch = {"times": k['times'], "status": k['status'], "absent": k['absent'], "dropped_home_ts": None}
                requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{k['id']}&school_code=eq.{code_norm}", headers=h(), json=patch, timeout=10)
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
    except Exception as e:
        print(f"Driver crash {e}"); return CSS + f"<div class='card'><h2>Driver Error</h2><p>{e}</p></div>"

@app.route("/driver/<code>/<plate>/logout")
def driver_logout(code, plate):
    resp = redirect(f"/driver/{normalize_code(code)}/{normalize_plate(plate)}"); resp.set_cookie(f"driver_pin_{normalize_plate(plate)}", "", max_age=0); return resp

@app.route("/api/<code>/<plate>/action", methods=["POST"])
def driver_action(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school: return jsonify({"ok": False, "error":"school deleted"}), 404
        if is_locked(school): return jsonify({"ok": False, "locked": True, "error":"Payment expired"}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); vans = db_get_vans(code_norm); van = next((v for v in vans if v['plate']==plate_norm), None)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False, "error":"PIN required"}), 401
        data = request.get_json() or {}; kid_id = data.get('kid_id'); act = data.get('action')
        if not kid_id or not act: return jsonify({"ok": False}), 400
        r=requests.get(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{code_norm}&select=*", headers=h(), timeout=10)
        if not r.json(): return jsonify({"ok": False, "error":"kid deleted"}), 404
        kid = r.json()[0]
        times = kid.get('times',{}) or {}
        if act in times and act not in ['present','absent']:
            return jsonify({"ok": True, "skipped": True})
        if act == 'absent' and kid.get('absent'):
            return jsonify({"ok": True, "skipped": True})
        if act == 'present' and not kid.get('absent') and not times:
            return jsonify({"ok": True, "skipped": True})
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

        requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{code_norm}", headers=h(), json=patch, timeout=10)
        # log isolated
        requests.post(f"{SUPABASE_URL}/rest/v1/attendance_log", headers=h(), json={"school_code": code_norm, "kid_name": kid.get('name',''), "kid_id": kid_id, "van_plate": plate_norm, "action": act, "log_date": str(date.today()), "log_time": full}, timeout=10)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/mass", methods=["POST"])
def mass_action(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school or is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); vans = db_get_vans(code_norm); van = next((v for v in vans if v['plate']==plate_norm), None)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False, "error":"PIN"}), 401
        data = request.get_json() or {}; act = data.get('action',''); short = kampala_now().strftime("%I:%M %p"); full = kampala_now().strftime("%I:%M %p %d %b")
        if act == 'dropped_school':
            kids = db_get_kids(code_norm, plate_norm)
            count=0
            for kid in kids:
                if not kid or kid.get('absent'): continue
                if 'dropped_school' in (kid.get('times',{}) or {}): continue
                times = kid.get('times',{}) or {}; times['dropped_school']=full
                requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid['id']}&school_code=eq.{code_norm}", headers=h(), json={"status": f"At School - arrived at {short}", "times": times}, timeout=10)
                whatsapp_async(kid.get('parent_phone',''), "dropped_school", [kid.get('name',''), short])
                requests.post(f"{SUPABASE_URL}/rest/v1/attendance_log", headers=h(), json={"school_code": code_norm, "kid_name": kid.get('name',''), "kid_id": kid['id'], "van_plate": plate_norm, "action": "dropped_school (mass)", "log_date": str(date.today()), "log_time": full}, timeout=10)
                count+=1
            return jsonify({"ok": True, "count": count})
        return jsonify({"ok": False}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/traffic", methods=["POST"])
def traffic(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school or is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); vans = db_get_vans(code_norm); van = next((v for v in vans if v['plate']==plate_norm), None)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False, "error":"PIN"}), 401
        raw_msg = (request.get_json() or {}).get('message','').strip()
        if not raw_msg or len(raw_msg) < 5: return jsonify({"ok": False}), 400
        clean_msg = raw_msg[:100].strip()
        kids = db_get_kids(code_norm, plate_norm)
        sent=0
        for kid in kids:
            if not kid or kid.get('absent'): continue
            whatsapp_async(kid.get('parent_phone',''), "traffic_alert", [plate_norm, clean_msg]); sent+=1
        return jsonify({"sent": True, "count": sent})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/<code>/<plate>/sync", methods=["POST"])
def bulk_sync(code, plate):
    try:
        code_norm = normalize_code(code); plate_norm = normalize_plate(plate)
        school = db_get_school(code_norm)
        if not school or is_locked(school): return jsonify({"locked": True}), 403
        cookie_pin = request.cookies.get(f"driver_pin_{plate_norm}"); vans = db_get_vans(code_norm); van = next((v for v in vans if v['plate']==plate_norm), None)
        if not van or cookie_pin!= get_van_pin(van): return jsonify({"ok": False, "error":"PIN"}), 401
        items = (request.get_json() or {}).get('items', []) or []
        if len(items)>100: items=items[-100:]
        short = kampala_now().strftime("%I:%M %p"); full = kampala_now().strftime("%I:%M %p %d %b"); count=0; skipped=0
        for it in items:
            kid_id=it.get('kid_id'); act=it.get('action')
            r=requests.get(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{code_norm}&select=*", headers=h(), timeout=10)
            if not r.json(): continue
            kid=r.json()[0]
            times = kid.get('times',{}) or {}
            if act in times and act not in ['present','absent']:
                skipped+=1; continue
            patch={}
            if act=='picked_home':
                patch["status"]=f"On way to School - picked at {short}"; times['picked_home']=full; patch["times"]=times; patch["absent"]=False
                whatsapp_async(kid.get('parent_phone',''), "picked_home", [kid.get('name',''), short, plate_norm])
            elif act=='dropped_school':
                patch["status"]=f"At School - arrived at {short}"; times['dropped_school']=full; patch["times"]=times
                whatsapp_async(kid.get('parent_phone',''), "dropped_school", [kid.get('name',''), short])
            elif act=='picked_school':
                patch["status"]=f"On way Home - left at {short}"; times['picked_school']=full; patch["times"]=times
                whatsapp_async(kid.get('parent_phone',''), "picked_school", [kid.get('name',''), short, plate_norm])
            elif act=='dropped_home':
                patch["status"]=f"Home Safe - {short}"; times['dropped_home']=full; patch["times"]=times; patch["dropped_home_ts"]=kampala_now().isoformat()
                whatsapp_async(kid.get('parent_phone',''), "dropped_home", [kid.get('name',''), short])
            elif act=='absent':
                patch["status"]="ABSENT Today"; patch["absent"]=True
                whatsapp_async(kid.get('parent_phone',''), "absent", [kid.get('name',''), str(date.today()), plate_norm])
            elif act=='present':
                patch["absent"]=False; patch["status"]="At Home - waiting for van"; patch["times"]={}; patch["dropped_home_ts"]=None
            if patch:
                requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{code_norm}", headers=h(), json=patch, timeout=10)
                requests.post(f"{SUPABASE_URL}/rest/v1/attendance_log", headers=h(), json={"school_code": code_norm, "kid_name": kid.get('name',''), "kid_id": kid_id, "van_plate": plate_norm, "action": act, "log_date": str(date.today()), "log_time": full}, timeout=10)
                count+=1
        return jsonify({"ok": True, "count": count, "skipped": skipped})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/p/<kid_id>")
def parent_view(kid_id):
    try:
        r=requests.get(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&select=*", headers=h(), timeout=10)
        if not r.json():
            return CSS + "<div class='card' style='max-width:500px;margin:50px auto;text-align:center'><h2>🔍 Kid not found</h2></div>"
        kid=r.json()[0]
        school=db_get_school(kid['school_code'])
        if is_locked(school):
            return CSS + f"<div class='card' style='background:#ffcccc;text-align:center;max-width:500px;margin:50px auto'><h2>🔒 Service Paused</h2></div>"
        vans=db_get_vans(kid['school_code']); van=next((v for v in vans if v['plate']==kid.get('van_plate','')), {}) or {}
        if maybe_auto_reset(kid):
            requests.patch(f"{SUPABASE_URL}/rest/v1/kids?id=eq.{kid_id}&school_code=eq.{kid['school_code']}", headers=h(), json={"times": kid['times'], "status": kid['status'], "absent": kid['absent'], "dropped_home_ts": None}, timeout=10)
        times = kid.get('times',{}) or {}
        progress = len(times) * 25
        timeline_html = ""
        steps = [("picked_home", "🏠 Picked Home", "On the road"), ("dropped_school", "🏫 Dropped School", "Safe at school"), ("picked_school", "🚐 Picked School", "Heading home"), ("dropped_home", "✅ Dropped Home", "Home safe")]
        for key, label, sub in steps:
            done = key in times; icon = "✅" if done else "⭕"; t = times.get(key, "— waiting")
            timeline_html += f"<div style='display:flex;gap:14px;margin:16px 0;opacity:{1 if done else 0.45};align-items:center'><div style='font-size:26px;width:32px;text-align:center'>{icon}</div><div><b>{label}</b><br><small style='color:#555'>{t} — {sub}</small></div></div>"
        status_color = "#0a7a2a" if "Home Safe" in (kid.get('status','') or '') else "#0D2A54"
        status_emoji = "✅" if progress>=100 else "👦"
        driver_wa = to_wa(van.get('driver_phone',''))
        return f"""{CSS}<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="30"> <div style="max-width:500px;margin:0 auto"><div style="text-align:center;padding:12px"><h2 style="margin:5px">🚐 FIKISHA</h2><small>{(school.get('name','') or '')} | Van {kid.get('van_plate','')} | {current_time_str()}</small></div> <div class="card" style="border-top:7px solid {status_color};text-align:center;border-radius:20px"><div style="font-size:56px">{status_emoji}</div><h2 style="border:none;margin:12px 0">{kid.get('name','')}</h2><small>Stage {kid.get('stage','')} | ID {kid.get('id','')}</small><br><br><span class="badge" style="background:{status_color};padding:10px 18px;border-radius:30px">{kid.get('status','')}</span><div class="progress" style="height:16px;margin:22px 0;border-radius:20px"><div class="progress-fill" style="width:{progress}%"></div></div><small><b>{progress}% Complete</b></small></div> <div class="card" style="border-radius:20px"><h3>🛣️ Live Journey</h3>{timeline_html}</div> <div class="card" style="display:flex;gap:10px;border-radius:16px"><a href="tel:{van.get('driver_phone','')}" style="flex:1;text-align:center;background:#0D2A54;color:#FFC107;padding:14px;border-radius:12px;text-decoration:none">📞 Call Driver<br><small>{van.get('driver_name','Driver')}</small></a><a href="https://wa.me/{driver_wa}?text=Hello {van.get('driver_name','')} about {kid.get('name','')}" style="flex:1;text-align:center;background:#25D366;color:white;padding:14px;border-radius:12px;text-decoration:none">💬 WhatsApp<br><small>Driver</small></a></div> <div class="card" style="background:#FFF8E1;text-align:center"><small>Van {kid.get('van_plate','')} | Share ID: <b>{kid_id}</b> | Only {kid['school_code']}</small></div></div>"""
    except Exception as e:
        return CSS + f"<div class='card'><h2>Error</h2><p>{e}</p></div>"

@app.route("/report/<code>")
def report_daily(code):
    try:
        code_norm = normalize_code(code); school = db_get_school(code_norm)
        if not school: return CSS + "<div class='card'><h2>No school</h2></div>"
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD:
            return redirect(f"/admin/{code_norm}")
        kids = db_get_kids(code_norm)
        total = len(kids); picked = sum(1 for k in kids if k and 'picked_home' in (k.get('times',{}) or {})); absent = sum(1 for k in kids if k and k.get('absent'))
        html = CSS + f"<div class='no-print'><a href='/admin/{code_norm}'><button>⬅️ Admin</button></a> <a href='/report/{code_norm}/weekly'><button class='btn-blue'>📊 Weekly</button></a> <a href='/report/{code_norm}/csv'><button class='btn-orange'>📥 CSV Daily</button></a></div><div class='card'><h2>Daily Report - {school.get('name','')} - {date.today()} - {current_time_str()} - Only {code_norm}</h2><p>Total:{total} Picked:{picked} Absent:{absent}</p></div><div class='card'><table><tr><th>Kid</th><th>Van</th><th>Status</th><th>Parent</th><th>Link</th></tr>"
        for k in kids:
            if not k: continue
            html += f"<tr><td>{k.get('name','')}</td><td>{k.get('van_plate','')}</td><td>{k.get('status','')}</td><td>{k.get('parent_phone','')}</td><td><a href='/p/{k.get('id','')}' target='_blank'>View</a></td></tr>"
        html += "</table></div>"; return html
    except Exception as e:
        return f"Daily error {e}"

@app.route("/report/<code>/weekly")
def weekly_report(code):
    try:
        code_norm = normalize_code(code); school = db_get_school(code_norm)
        if not school: return CSS + "<div class='card'><h2>No school</h2></div>"
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD:
            return redirect(f"/admin/{code_norm}")
        kids = db_get_kids(code_norm); total = len([k for k in kids if k]); absent_today = sum(1 for k in kids if k and k.get('absent')); not_picked = [k for k in kids if k and not (k.get('times',{}) or {}) and not k.get('absent')]; picked_home = sum(1 for k in kids if k and 'picked_home' in (k.get('times',{}) or {}))
        html = CSS + f"""<meta name="viewport" content="width=device-width, initial-scale=1"><div class="no-print" style="display:flex;gap:8px;flex-wrap:wrap"><a href="/admin/{code_norm}"><button>⬅️ Admin</button></a><a href="/report/{code_norm}"><button class="btn-grey">Daily</button></a><a href="/report/{code_norm}/weekly/csv"><button class="btn-blue">📥 Weekly CSV</button></a><a href="/report/{code_norm}/csv"><button class="btn-orange">📥 Daily CSV</button></a></div><h2>📊 {school.get('name','')} ({code_norm}) - {current_time_str()} - Unlimited</h2><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px"><div class="card" style="text-align:center"><h3>{total}</h3><small>Total - {code_norm} only</small></div><div class="card" style="text-align:center"><h3>{picked_home}</h3><small>Picked</small></div><div class="card" style="text-align:center"><h3>{absent_today}</h3><small>Absent</small></div></div><div class="card"><b>Not yet picked ({len(not_picked)}):</b> {', '.join([k.get('name','') for k in not_picked]) or 'All picked ✅'}</div><div class="card"><h3>All Kids - Isolated to {code_norm}</h3><table><tr><th>Kid</th><th>Van</th><th>Status</th><th>Times</th><th>Link</th></tr>"""
        for kid in kids:
            if not kid: continue
            times = kid.get('times',{}) or {}; times_str = "<br>".join([f"<small>{k}: {v}</small>" for k,v in times.items()]) or "<small>Not started</small>"
            html += f"<tr><td><b>{kid.get('name','')}</b></td><td>{kid.get('van_plate','')}</td><td>{'🚫 ABSENT' if kid.get('absent') else '✅'}<br><small>{(kid.get('status','') or '')[:30]}</small></td><td>{times_str}</td><td><a href='/p/{kid.get('id','')}' target='_blank'>View</a></td></tr>"
        html += "</table></div>"
        log = db_get_logs(code_norm, 200)
        if log:
            html += "<div class='card'><h3>Recent Log - Only {}</h3><table><tr><th>Date</th><th>Kid</th><th>Van</th><th>Action</th><th>Time</th></tr>".format(code_norm)
            for e in log:
                html += f"<tr><td>{e.get('log_date','')}</td><td>{e.get('kid_name','')}</td><td>{e.get('van_plate','')}</td><td>{e.get('action','')}</td><td><small>{e.get('log_time','')}</small></td></tr>"
            html += "</table></div>"
        return html
    except Exception as e:
        return f"Weekly error {e}"

@app.route("/report/<code>/csv")
def export_csv_daily(code):
    try:
        code_norm = normalize_code(code); school = db_get_school(code_norm)
        if not school: return "No school"
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD:
            return redirect(f"/admin/{code_norm}")
        kids = db_get_kids(code_norm)
        si = io.StringIO(); cw = csv.writer(si); cw.writerow(["Kid ID","Name","Stage","Van","Parent Phone","Status","Absent","Picked Home","Dropped School","Picked School","Dropped Home","Report Time","School Code"])
        for kid in kids:
            if not kid: continue
            t = kid.get('times',{}) or {}; cw.writerow([kid.get('id',''),kid.get('name',''),kid.get('stage',''),kid.get('van_plate',''),kid.get('parent_phone',''),kid.get('status',''),kid.get('absent',False),t.get('picked_home',''),t.get('dropped_school',''),t.get('picked_school',''),t.get('dropped_home',''),current_time_str(), code_norm])
        output = make_response(si.getvalue()); output.headers["Content-Disposition"] = f"attachment; filename=FIKISHA_DAILY_{code_norm}_{date.today()}.csv"; output.headers["Content-type"] = "text/csv"; return output
    except Exception as e:
        return f"CSV error {e}"

@app.route("/report/<code>/weekly/csv")
def export_csv_weekly(code):
    try:
        code_norm = normalize_code(code); school = db_get_school(code_norm)
        if not school: return "No school"
        if request.cookies.get(f"admin_pin_{code_norm}")!= get_admin_pin(school) and request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD:
            return redirect(f"/admin/{code_norm}")
        si = io.StringIO(); cw = csv.writer(si); cw.writerow(["Date","Time","Kid Name","Van","Action","School"])
        for e in db_get_logs(code_norm, 500):
            cw.writerow([e.get('log_date',''), e.get('log_time',''), e.get('kid_name',''), e.get('van_plate',''), e.get('action',''), code_norm])
        cw.writerow([]); cw.writerow(["--- SNAPSHOT ---"]); cw.writerow(["Kid ID","Name","Stage","Van","Parent Phone","Status","Absent"])
        for kid in db_get_kids(code_norm):
            if not kid: continue
            cw.writerow([kid.get('id',''),kid.get('name',''),kid.get('stage',''),kid.get('van_plate',''),kid.get('parent_phone',''),kid.get('status',''),kid.get('absent',False)])
        output = make_response(si.getvalue()); output.headers["Content-Disposition"] = f"attachment; filename=FIKISHA_WEEKLY_{code_norm}_{date.today()}.csv"; output.headers["Content-type"] = "text/csv"; return output
    except Exception as e:
        return f"CSV error {e}"

@app.route("/manifest.json")
def manifest():
    return jsonify({"name": "FIKISHA Driver","short_name": "FIKISHA","start_url": "/","display": "standalone","background_color": "#FFF8E1","theme_color": "#0D2A54"})

@app.route("/sw.js")
def sw():
    js = "self.addEventListener('install', e=>{ self.skipWaiting(); }); self.addEventListener('activate', e=>{ self.clients.claim(); }); self.addEventListener('fetch', e=>{ e.respondWith(fetch(e.request).catch(()=> caches.match(e.request))); });"
    return Response(js, mimetype='application/javascript')

@app.route("/health")
def health():
    try:
        r=requests.get(f"{SUPABASE_URL}/rest/v1/schools?select=code", headers=h(), timeout=5)
        return jsonify({"ok": True, "schools": len(r.json()) if r.status_code==200 else 0, "mode": "unlimited-isolated"})
    except:
        return jsonify({"ok": True, "mode": "unlimited"})

@app.route("/migrate_once")
def migrate_once():
    if request.cookies.get("super_auth")!= SUPER_ADMIN_PASSWORD:
        return "Login at / as super admin first"
    try:
        r=requests.get(f"{SUPABASE_URL}/rest/v1/fikisha_store?id=eq.1&select=data", headers=h(), timeout=10)
        old = r.json()[0]['data'] if r.json() and r.json()[0].get('data') else {"schools":{}}
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
            requests.post(f"{SUPABASE_URL}/rest/v1/kids", headers=hr(), json={"id": kid_id.upper(), "school_code": normalize_code(code), "name": kid.get('name'), "stage": kid.get('stage',''), "parent_phone": kid.get('parent_phone',''), "van_plate": normalize_plate(kid.get('van_plate')), "status": kid.get('status','At Home - waiting for van'), "times": kid.get('times',{}), "absent": kid.get('absent', False), "dropped_home_ts": kid.get('dropped_home_ts')}, timeout=10)
            count+=1
    return f"Migrated {count} kids to unlimited tables. Keep old store for backup."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))