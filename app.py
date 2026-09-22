from flask import Flask, request, redirect, jsonify
import json, os, requests
from datetime import datetime, date, timedelta

# --- KAMPALA TIMEZONE FIX ---
try:
    from zoneinfo import ZoneInfo
    KAMPALA_TZ = ZoneInfo("Africa/Kampala")
except:
    KAMPALA_TZ = None

def kampala_now():
    if KAMPALA_TZ:
        return datetime.now(KAMPALA_TZ)
    return datetime.now() + timedelta(hours=3)

def normalize_plate(p):
    return str(p).upper().strip().replace(" ", "") if p else ""

def normalize_code(p):
    return str(p).upper().strip().replace(" ", "") if p else ""

app = Flask(__name__)
DB_FILE = "fikisha_db.json"

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"schools": {}}
    return {"schools": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=2)

def to_wa(p):
    c = str(p).replace("+","").replace(" ","").replace("-","").strip()
    if c.startswith("0"):
        c = "256" + c[1:]
    return c

def current_time_str():
    return kampala_now().strftime("%I:%M %p")

def whatsapp_template(to, template_name, params=[]):
    token = os.environ.get("WHATSAPP_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID", "1327812003752192")
    if not token:
        return False, "no token"
    clean = to_wa(to)
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body_params = [{"type": "text", "text": str(p)} for p in params]
    data = {
        "messaging_product": "whatsapp",
        "to": clean,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en_US"},
            "components": [{"type": "body", "parameters": body_params}] if body_params else []
        }
    }
    try:
        r = requests.post(url, headers=headers, json=data, timeout=15)
        print(f"WA {template_name} to {clean}: {r.status_code}")
        return r.status_code == 200, r.text
    except Exception as e:
        return False, str(e)

def whatsapp_text(to, msg):
    token = os.environ.get("WHATSAPP_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID", "1327812003752192")
    if not token: return False, "no token"
    clean = to_wa(to)
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp","to": clean,"type": "text","text": {"body": msg}}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        return r.status_code == 200, r.text
    except Exception as e:
        return False, str(e)

def is_locked(school):
    try:
        return school['paid_until'] < str(date.today())
    except:
        return True

def maybe_auto_reset(kid):
    ts = kid.get('dropped_home_ts')
    if not ts: return False
    if 'dropped_home' not in kid.get('times', {}): return False
    try:
        dropped_time = datetime.fromisoformat(ts)
        now = kampala_now()
        diff = (datetime.now() - dropped_time) if dropped_time.tzinfo is None else (now - dropped_time)
        if diff > timedelta(hours=2):
            kid['times']={}; kid['status']="At Home - waiting for van"; kid['absent']=False; kid.pop('dropped_home_ts',None)
            return True
    except: pass
    return False

CSS = """<style>body{font-family:system-ui,Arial;background:#FFF8E1;margin:0;padding:15px;color:#1a1a1a}h2{color:#0D2A54;border-bottom:3px solid #FFC107;padding-bottom:8px}.card{background:white;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 4px 14px rgba(0,0,0,0.1);border-top:5px solid #FFC107}input,select{padding:11px;border-radius:10px;border:2px solid #FFC107;margin:6px;width:90%}button{padding:11px 20px;border-radius:10px;border:none;background:#0D2A54;color:#FFC107;font-weight:bold;cursor:pointer;margin:5px}.btn-done{background:#0a7a2a!important;color:white!important}.btn-red{background:#d32f2f;color:white}.btn-orange{background:#ef6c00;color:white}.btn-blue{background:#1565c0;color:white}.btn-grey{background:#888;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:700px){.grid{grid-template-columns:1fr}}.badge{padding:6px 12px;border-radius:20px;background:#0D2A54;color:#FFC107}.progress{height:10px;background:#eee;border-radius:10px;overflow:hidden}.progress-fill{height:100%;background:linear-gradient(90deg,#0a7a2a,#FFC107)}a{color:#1565c0;font-weight:bold;text-decoration:none}table{width:100%;border-collapse:collapse}th,td{padding:8px;text-align:left;border-bottom:1px solid #eee}</style>"""

@app.route("/")
def home():
    db = load_db()
    html = CSS + f"<h2>FIKISHA - Super Admin (Kampala: {current_time_str()})</h2>"
    html += """<div class="card"><h3>Create New School</h3><form method="post" action="/create_school"><input name="school_name" placeholder="Bright Angels" required><input name="code" placeholder="Code BRIGHT123" required><input name="director" placeholder="Director WhatsApp 2567..." required><input name="paid_until" type="date" required><button>Add School +</button></form></div><hr>"""
    for code, s in db.get("schools", {}).items():
        lock = "EXPIRED" if is_locked(s) else "Active"
        html += f"""<div class='card'><b>{s['name']}</b> ({code}) - {lock} - Paid: {s['paid_until']} | Director: {s['director_phone']}<br><form method='post' action='/super/update_paid/{code}' style='display:inline'><input type='date' name='paid_until' value='{s['paid_until']}' required><button style='padding:6px 12px;font-size:13px'>Update Paid</button></form> <a href='/admin/{code}'>Manage</a> | <a href='/report/{code}'>Report</a> | <a href='/super/delete_school/{code}' style='color:red'>Delete</a><br>"""
        for vp, van in s.get('vans', {}).items():
            html += f"Van <b>{vp}</b> - {van['driver_name']} - <a href='/driver/{code}/{vp}'>Driver Page</a><br>"
        html += "</div>"
    return html

@app.route("/create_school", methods=["POST"])
def create_school():
    db = load_db()
    code = normalize_code(request.form['code'])
    db['schools'][code] = {"name": request.form['school_name'],"code": code,"director_phone": request.form['director'],"paid_until": request.form['paid_until'],"vans": {},"kids": {}}
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/super/update_paid/<code>", methods=["POST"])
def update_paid(code):
    db = load_db()
    code = normalize_code(code)
    if code in db['schools']:
        db['schools'][code]['paid_until'] = request.form['paid_until']
        save_db(db)
    return redirect("/")

@app.route("/super/delete_school/<code>")
def delete_school(code):
    db = load_db()
    db['schools'].pop(normalize_code(code), None)
    save_db(db)
    return redirect("/")

ADMIN_HTML = CSS + """<h2>{{school.name}} Admin ({{code}}) - {% if locked %}EXPIRED {{school.paid_until}}{% else %}Paid until {{school.paid_until}}{% endif %}</h2><p>Kampala: {{kampala_time}}</p><a href="/">Super Admin</a> | <a href="/report/{{code}}">Daily Report</a><div class="card"><h3>Daily Control</h3><form method="post" action="/api/{{code}}/reset_all"><button style="background:#0a7a2a;width:100%">RESET ALL FOR TOMORROW</button></form></div><div class="card"><h3>Add Van</h3><form method="post" action="/admin/{{code}}/add_van"><input name="plate" placeholder="Plate UAA123A - spaces ok" required><input name="driver_name" placeholder="Driver Name" required><input name="driver_phone" placeholder="Driver Phone 2567..." required><button>Add Van</button></form></div><div class="card"><h3>Add Kid to Van</h3><form method="post" action="/admin/{{code}}/add_kid"><input name="kid_name" placeholder="Kid Name" required><input name="stage" placeholder="Stage" required><input name="parent_phone" placeholder="Parent WhatsApp 2567..." required>Van: <select name="van_plate" required>{% for vp in school.vans %}<option value="{{vp}}">{{vp}}</option>{% endfor %}</select><button>Add Kid</button></form></div><hr><h3>Vans & Kids ({{total_kids}})</h3>{% for vp, van in school.vans.items() %}<div class="card"><b>{{vp}} - {{van.driver_name}}</b> - <a href="/driver/{{code}}/{{vp}}">Driver Page</a> <a href="/admin/{{code}}/delete_van/{{vp}}" style="color:red;float:right">Delete Van</a><br>{% for kid_id, kid in school.kids.items() if kid.van_plate==vp %} - {{kid.name}} ({{kid.stage}}) - {{kid.status}} - {{kid.parent_phone}} - <a href="/p/{{kid_id}}">View</a> <a href="/admin/{{code}}/delete_kid/{{kid_id}}" style="color:red">Delete Kid</a><br>{% else %}<i>No kids in this van yet</i><br>{% endfor %}</div>{% endfor %}"""

@app.route("/admin/<code>")
def admin(code):
    db = load_db()
    code = normalize_code(code)
    school = db['schools'].get(code)
    if not school: return "School not found"
    changed=False
    for kid in school['kids'].values():
        if maybe_auto_reset(kid): changed=True
    if changed: save_db(db)
    from jinja2 import Template
    return Template(ADMIN_HTML).render(school=school, code=code, locked=is_locked(school), kampala_time=current_time_str(), total_kids=len(school['kids']))

@app.route("/admin/<code>/add_van", methods=["POST"])
def add_van(code):
    db = load_db()
    code = normalize_code(code)
    plate = normalize_plate(request.form['plate'])
    db['schools'][code]['vans'][plate] = {"plate": plate,"driver_name": request.form['driver_name'],"driver_phone": request.form['driver_phone']}
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/delete_van/<plate>")
def delete_van(code, plate):
    db = load_db()
    db['schools'][normalize_code(code)]['vans'].pop(normalize_plate(plate), None)
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/delete_kid/<kid_id>")
def delete_kid(code, kid_id):
    db = load_db()
    db['schools'][normalize_code(code)]['kids'].pop(kid_id, None)
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/add_kid", methods=["POST"])
def add_kid(code):
    db = load_db()
    import uuid
    code = normalize_code(code)
    kid_id = str(uuid.uuid4())[:8].upper()
    van_plate = normalize_plate(request.form['van_plate'])
    if van_plate not in db['schools'][code]['vans']:
        return f"Van {van_plate} not found! Available: {list(db['schools'][code]['vans'].keys())} <a href='/admin/{code}'>Back</a>"
    db['schools'][code]['kids'][kid_id] = {"id": kid_id,"name": request.form['kid_name'],"stage": request.form['stage'],"parent_phone": request.form['parent_phone'],"van_plate": van_plate,"status": "At Home - waiting for van","times": {},"absent": False}
    save_db(db)
    whatsapp_template(request.form['parent_phone'], "picked_home", [request.form['kid_name'], current_time_str(), van_plate])
    return redirect(f"/admin/{code}")

@app.route("/api/<code>/reset_all", methods=["POST"])
def reset_all(code):
    db = load_db()
    code = normalize_code(code)
    for kid in db['schools'][code]['kids'].values():
        kid['times']={}; kid['status']="At Home - waiting for van"; kid['absent']=False; kid.pop('dropped_home_ts',None)
    save_db(db)
    return redirect(f"/admin/{code}")

DRIVER_HTML = CSS + """<h2>Driver: {{van.driver_name}} - Van {{van.plate}} - {{school.name}}</h2>{% if locked %}<div class="card" style="background:#ffcccc"><h1>PAY TO UNLOCK</h1></div>{% endif %}<p>Code: {{code}} | Kampala: {{kampala_time}} | {{today}} | {{kids|length}} kids</p>
<div class="card" style="border:2px solid #d32f2f">
<h3 style="color:#d32f2f">🚨 ALERT to ALL Parents (Template Safe)</h3>
<label>Choose reason:</label><br>
<select id="trafficReason" style="width:95%;padding:12px;border:2px solid #d32f2f">
<option value="Heavy traffic - 15 mins late">Heavy traffic - 15 mins late</option>
<option value="Heavy traffic - 30 mins late">Heavy traffic - 30 mins late</option>
<option value="Tyre puncture - fixing, 20 mins delay">Tyre puncture - fixing, 20 mins delay</option>
<option value="Fuel stop - 10 mins delay">Fuel stop - 10 mins delay</option>
<option value="Small accident - van ok, 20 mins delay">Small accident - van ok, 20 mins delay</option>
<option value="Police check - 10 mins delay">Police check - 10 mins delay</option>
<option value="custom">✏️ Write custom (short)</option>
</select>
<input id="trafficCustom" placeholder="Type custom - max 80 chars" style="width:95%;display:none;margin-top:8px" maxlength="80">
<br><br><button class="btn-red" onclick="sendTraffic()" style="width:100%">🚨 SEND ALERT TO ALL PARENTS</button>
<p style="font-size:12px">Will send: ALERT from Van {{van.plate}}: [reason]</p>
</div>
<hr><div class="grid">{% for kid_id, kid in kids.items() %}<div class="card"><b>{{kid.name}}</b> - {{kid.stage}}<br>Status: <span class="badge">{{kid.status}}</span><div class="progress"><div class="progress-fill" style="width: {{kid.progress}}%"></div></div><br><button onclick="action('{{kid.id}}','picked_home')">PICKED HOME</button><button onclick="action('{{kid.id}}','dropped_school')">DROPPED SCHOOL</button><button onclick="action('{{kid.id}}','picked_school')">PICKED SCHOOL</button><button onclick="action('{{kid.id}}','dropped_home')">DROPPED HOME</button><br><button class="btn-orange" onclick="action('{{kid.id}}','absent')">ABSENT</button><button class="btn-grey" onclick="action('{{kid.id}}','present')">BACK</button></div>{% endfor %}</div>
<script>
document.getElementById('trafficReason').addEventListener('change', function(){
  let custom = document.getElementById('trafficCustom');
  if(this.value=='custom'){ custom.style.display='block'; custom.focus(); } else { custom.style.display='none'; }
});
function action(kid_id, act){fetch('/api/{{code}}/{{van.plate}}/action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({kid_id: kid_id, action: act})}).then(r=>r.json()).then(j=>{ location.reload(); })}
function sendTraffic(){
  let reasonEl = document.getElementById('trafficReason');
  let customEl = document.getElementById('trafficCustom');
  let msg = reasonEl.value;
  if(msg=='custom'){ msg = customEl.value.trim(); }
  if(!msg){ alert('Choose or type a reason!'); return; }
  if(msg.length>100){ alert('Too long! Max 100 chars'); return; }
  msg = msg.replace(/[^a-zA-Z0-9 \\-:,]/g, '').trim();
  if(msg.length<5){ alert('Too short'); return; }
  if(!confirm('Send to ALL parents?\\n\\n' + msg)){ return; }
  fetch('/api/{{code}}/{{van.plate}}/traffic', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})}).then(r=>r.json()).then(j=>{ alert('✅ Sent to ' + (j.count||'all') + ' parents!'); customEl.value=''; })
}
</script>"""

@app.route("/driver/<code>/<plate>")
def driver_page(code, plate):
    db = load_db()
    code = normalize_code(code)
    plate_norm = normalize_plate(plate)
    school = db['schools'].get(code)
    if not school: return "No school"
    van = school['vans'].get(plate_norm)
    if not van: return f"No van {plate_norm}. Available: {list(school['vans'].keys())}"
    locked = is_locked(school)
    changed=False
    for k in school['kids'].values():
        if k['van_plate']==plate_norm:
            if maybe_auto_reset(k): changed=True
    if changed: save_db(db)
    kids = {k:v for k,v in school['kids'].items() if v['van_plate']==plate_norm}
    for kid in kids.values():
        cnt=0
        if 'picked_home' in kid['times']: cnt=25
        if 'dropped_school' in kid['times']: cnt=50
        if 'picked_school' in kid['times']: cnt=75
        if 'dropped_home' in kid['times']: cnt=100
        kid['progress']=cnt
    from jinja2 import Template
    return Template(DRIVER_HTML).render(school=school, van=van, code=code, kids=kids, locked=locked, today=str(date.today()), kampala_time=current_time_str())

@app.route("/api/<code>/<plate>/action", methods=["POST"])
def driver_action(code, plate):
    db = load_db()
    code = normalize_code(code)
    plate_norm = normalize_plate(plate)
    school = db['schools'].get(code)
    if not school: return jsonify({"ok": False})
    if is_locked(school): return jsonify({"ok": False, "locked": True}), 403
    data = request.get_json()
    kid_id = data['kid_id']; act = data['action']
    kid = db['schools'][code]['kids'][kid_id]
    short = kampala_now().strftime("%I:%M %p")
    full = kampala_now().strftime("%I:%M %p %d %b")
    if act == 'picked_home':
        kid['status']=f"On way to School - picked at {short}"; kid['times']['picked_home']=full; kid['absent']=False
        whatsapp_template(kid['parent_phone'], "picked_home", [kid['name'], short, plate_norm])
    elif act == 'dropped_school':
        kid['status']=f"At School - arrived at {short}"; kid['times']['dropped_school']=full
        whatsapp_template(kid['parent_phone'], "dropped_school", [kid['name'], short])
    elif act == 'picked_school':
        kid['status']=f"On way Home - left at {short}"; kid['times']['picked_school']=full
        whatsapp_template(kid['parent_phone'], "picked_school", [kid['name'], short, plate_norm])
    elif act == 'dropped_home':
        kid['status']=f"Home Safe - {short}"; kid['times']['dropped_home']=full; kid['dropped_home_ts']=kampala_now().isoformat()
        whatsapp_template(kid['parent_phone'], "dropped_home", [kid['name'], short])
    elif act == 'absent':
        kid['status']="ABSENT Today"; kid['absent']=True
    elif act == 'present':
        kid['absent']=False; kid['status']="At Home - waiting for van"; kid['times']={}; kid.pop('dropped_home_ts',None)
    save_db(db)
    return jsonify({"ok": True})

@app.route("/api/<code>/<plate>/traffic", methods=["POST"])
def traffic(code, plate):
    db = load_db()
    code = normalize_code(code)
    plate_norm = normalize_plate(plate)
    school = db['schools'].get(code)
    if not school or is_locked(school):
        return jsonify({"locked": True}), 403
    raw_msg = (request.get_json() or {}).get('message','').strip()
    if not raw_msg or len(raw_msg) < 5:
        return jsonify({"ok": False, "error": "Empty"}), 400
    clean_msg = raw_msg[:100].strip()
    sent = 0
    for kid in [k for k in school['kids'].values() if k['van_plate']==plate_norm and not k['absent']]:
        ok, _ = whatsapp_template(kid['parent_phone'], "traffic_alert", [plate_norm, clean_msg])
        if ok: sent += 1
    return jsonify({"sent": True, "count": sent, "message": clean_msg})

@app.route("/p/<kid_id>")
def parent_view(kid_id):
    db = load_db()
    for code, school in db['schools'].items():
        if kid_id in school['kids']:
            kid = school['kids'][kid_id]
            if maybe_auto_reset(kid): save_db(db)
            van = school['vans'].get(kid['van_plate'], {"driver_name":"Unknown"})
            timeline = "<br>".join([f"- {k}: {v}" for k,v in kid['times'].items()]) or "Waiting..."
            return CSS + f"<div class='card'><h2>{kid['name']}</h2>Stage: {kid['stage']}<br>Van: {kid['van_plate']} Driver {van['driver_name']}<br><h3>Status: {kid['status']}</h3><b>Timeline (Kampala):</b><br>{timeline}<br><br><i>Live Fikisha {current_time_str()}</i></div>"
    return "Kid not found"

@app.route("/report/<code>")
def report(code):
    db = load_db()
    code = normalize_code(code)
    school = db['schools'].get(code)
    if not school: return "School not found"
    today = str(date.today())
    kids = list(school['kids'].values())
    total = len(kids)
    picked_home = sum(1 for k in kids if 'picked_home' in k['times'])
    dropped_school = sum(1 for k in kids if 'dropped_school' in k['times'])
    picked_school = sum(1 for k in kids if 'picked_school' in k['times'])
    dropped_home = sum(1 for k in kids if 'dropped_home' in k['times'])
    absent = sum(1 for k in kids if k.get('absent'))
    html = CSS + f"<div class='card'><h2>📊 Daily Report - {school['name']}</h2><p>Date: {today} | Kampala: {current_time_str()} | {kampala_now().strftime('%d %b %Y %I:%M %p')}</p><div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px'><div style='background:#e8f5e9;padding:12px;border-radius:10px;text-align:center'><b>{total}</b><br>Total Kids</div><div style='background:#fff3e0;padding:12px;border-radius:10px;text-align:center'><b>{picked_home}</b><br>Picked Home</div><div style='background:#e3f2fd;padding:12px;border-radius:10px;text-align:center'><b>{dropped_school}</b><br>At School</div><div style='background:#fce4ec;padding:12px;border-radius:10px;text-align:center'><b>{picked_school}</b><br>Picked School</div><div style='background:#e8f5e9;padding:12px;border-radius:10px;text-align:center'><b>{dropped_home}</b><br>Home Safe</div><div style='background:#ffebee;padding:12px;border-radius:10px;text-align:center'><b>{absent}</b><br>Absent</div></div></div>"
    for vp, van in school['vans'].items():
        van_kids = [k for k in kids if k['van_plate']==vp]
        html += f"<div class='card'><h3>Van {vp} - {van['driver_name']} ({len(van_kids)} kids)</h3><table><tr style='background:#0D2A54;color:#FFC107'><th>Kid</th><th>Stage</th><th>Status</th><th>Timeline</th><th>Parent</th></tr>"
        for k in van_kids:
            timeline = "<br>".join([f"{kk}: {vv}" for kk,vv in k['times'].items()]) or "Waiting"
            if k.get('absent'): timeline = "ABSENT"
            html += f"<tr><td>{k['name']}</td><td>{k['stage']}</td><td>{k['status']}</td><td style='font-size:11px'>{timeline}</td><td>{k['parent_phone']}</td></tr>"
        html += "</table></div>"
    html += f"<div class='card'><h3>📲 Send to Director</h3><button onclick='sendReport()' class='btn-blue'>Send Summary via WhatsApp to {school['director_phone']}</button><p id='reportStatus'></p></div><script>function sendReport(){{let text=`FIKISHA REPORT {school['name']} {today} Kampala {current_time_str()}%0ATotal:{total} Picked:{picked_home} Dropped:{dropped_home} Absent:{absent}%0AFull report: https://{request.host}/report/{code}`;fetch('/api/{code}/send_report',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{text: decodeURIComponent(text)}})}}).then(r=>r.json()).then(j=>{{document.getElementById('reportStatus').innerText=j.ok?'✅ Sent!':'❌ '+j.error}})}}<\/script>"
    return html

@app.route("/api/<code>/send_report", methods=["POST"])
def send_report(code):
    db = load_db()
    code = normalize_code(code)
    school = db['schools'].get(code)
    if not school: return jsonify({"ok": False, "error": "no school"})
    msg = request.get_json().get('text','')
    ok, resp = whatsapp_text(school['director_phone'], msg)
    return jsonify({"ok": ok, "resp": resp[:300]})

@app.route("/test_wa/<phone>")
def test_wa(phone):
    t = current_time_str()
    ok, r = whatsapp_template(phone, "picked_home", ["Musa", t, "UAA123"])
    ok2, r2 = whatsapp_template(phone, "traffic_alert", ["UAA123", "Heavy traffic - 15 mins late"])
    return jsonify({"kampala_time": t, "picked_home_ok": ok, "traffic_ok": ok2, "resp": r[:200]})

@app.route("/debug/<code>")
def debug(code):
    db = load_db()
    school = db['schools'].get(normalize_code(code))
    import json as js
    return f"<pre>Kampala now: {kampala_now()} ({current_time_str()})\n{js.dumps(school, indent=2)}</pre>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)