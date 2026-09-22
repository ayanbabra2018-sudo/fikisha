from flask import Flask, request, redirect, jsonify
import json, os, requests
from datetime import datetime, date, timedelta

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
    return datetime.now().strftime("%I:%M %p")

def whatsapp_template(to, template_name, params=[]):
    token = os.environ.get("WHATSAPP_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID", "1327812003752192")
    if not token:
        print("!!! TOKEN MISSING!!!")
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
        print(f"WA TEMPLATE {template_name} to {clean} params={params}: {r.status_code} - {r.text[:500]}")
        return r.status_code == 200, r.text
    except Exception as e:
        print(f"WA Error: {e}")
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
    if ts:
        try:
            if datetime.now() - datetime.fromisoformat(ts) > timedelta(hours=2):
                kid['times']={}; kid['status']="At Home - waiting for van"; kid['absent']=False; kid.pop('dropped_home_ts',None)
                return True
        except: pass
    return False

CSS = """<style>body{font-family:system-ui,Arial;background:#FFF8E1;margin:0;padding:15px;color:#1a1a1a}h2{color:#0D2A54;border-bottom:3px solid #FFC107;padding-bottom:8px}.card{background:white;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 4px 14px rgba(0,0,0,0.1);border-top:5px solid #FFC107}input,select{padding:11px;border-radius:10px;border:2px solid #FFC107;margin:6px;width:90%}button{padding:11px 20px;border-radius:10px;border:none;background:#0D2A54;color:#FFC107;font-weight:bold;cursor:pointer;margin:5px}.btn-done{background:#0a7a2a!important;color:white!important}.btn-red{background:#d32f2f;color:white}.btn-orange{background:#ef6c00;color:white}.btn-blue{background:#1565c0;color:white}.btn-grey{background:#888;color:white}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:700px){.grid{grid-template-columns:1fr}}.badge{padding:6px 12px;border-radius:20px;background:#0D2A54;color:#FFC107}.progress{height:10px;background:#eee;border-radius:10px;overflow:hidden}.progress-fill{height:100%;background:linear-gradient(90deg,#0a7a2a,#FFC107)}a{color:#1565c0;font-weight:bold;text-decoration:none}</style>"""

@app.route("/")
def home():
    db = load_db()
    html = CSS + "<h2>FIKISHA - Super Admin</h2>"
    html += """<div class="card"><h3>Create New School</h3><form method="post" action="/create_school"><input name="school_name" placeholder="Bright Angels" required><input name="code" placeholder="Code BRIGHT123" required><input name="director" placeholder="Director WhatsApp 2567..." required><input name="paid_until" type="date" required><button>Add School +</button></form></div><hr>"""
    for code, s in db.get("schools", {}).items():
        lock = "EXPIRED" if is_locked(s) else "Active"
        html += f"""<div class='card'><b>{s['name']}</b> ({code}) - {lock} - Paid: {s['paid_until']} | Director: {s['director_phone']}<br><a href='/admin/{code}'>Manage</a> | <a href='/report/{code}'>Report</a> | <a href='/super/delete_school/{code}' style='color:red'>Delete School</a><br>"""
        for vp, van in s.get('vans', {}).items():
            html += f"Van <b>{vp}</b> - {van['driver_name']} - <a href='/driver/{code}/{vp}'>Driver Page</a><br>"
        html += "</div>"
    return html

@app.route("/create_school", methods=["POST"])
def create_school():
    db = load_db()
    code = request.form['code'].upper().strip()
    db['schools'][code] = {"name": request.form['school_name'],"code": code,"director_phone": request.form['director'],"paid_until": request.form['paid_until'],"vans": {},"kids": {}}
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/super/update_paid/<code>", methods=["POST"])
def update_paid(code):
    db = load_db()
    if code in db['schools']:
        db['schools'][code]['paid_until'] = request.form['paid_until']
        save_db(db)
    return redirect("/")

@app.route("/super/delete_school/<code>")
def delete_school(code):
    db = load_db()
    db['schools'].pop(code, None)
    save_db(db)
    return redirect("/")

ADMIN_HTML = CSS + """<h2>{{school.name}} Admin ({{code}}) - {% if locked %}EXPIRED {{school.paid_until}}{% else %}Paid until {{school.paid_until}}{% endif %}</h2><a href="/">Super Admin</a> | <a href="/report/{{code}}">Report</a><div class="card"><h3>Daily Control</h3><form method="post" action="/api/{{code}}/reset_all"><button style="background:#0a7a2a;width:100%">RESET ALL FOR TOMORROW</button></form></div><div class="card"><h3>Add Van</h3><form method="post" action="/admin/{{code}}/add_van"><input name="plate" placeholder="Plate UAA123" required><input name="driver_name" placeholder="Driver Name" required><input name="driver_phone" placeholder="Driver Phone 2567..." required><button>Add Van</button></form></div><div class="card"><h3>Add Kid</h3><form method="post" action="/admin/{{code}}/add_kid"><input name="kid_name" placeholder="Kid Name" required><input name="stage" placeholder="Stage" required><input name="parent_phone" placeholder="Parent WhatsApp 2567..." required>Van: <select name="van_plate" required>{% for vp in school.vans %}<option value="{{vp}}">{{vp}}</option>{% endfor %}</select><button>Add Kid</button></form></div><hr><h3>Vans & Kids</h3>{% for vp, van in school.vans.items() %}<div class="card"><b>{{vp}} - {{van.driver_name}}</b> - <a href="/driver/{{code}}/{{vp}}">Driver Page</a> <a href="/admin/{{code}}/delete_van/{{vp}}" style="color:red;float:right">Delete Van</a><br>{% for kid_id, kid in school.kids.items() if kid.van_plate==vp %} - {{kid.name}} ({{kid.stage}}) - {{kid.status}} - {{kid.parent_phone}} - <a href="/p/{{kid_id}}">View</a> <a href="/admin/{{code}}/delete_kid/{{kid_id}}" style="color:red">Delete Kid</a><br>{% endfor %}</div>{% endfor %}"""

@app.route("/admin/<code>")
def admin(code):
    db = load_db()
    school = db['schools'].get(code)
    if not school: return "School not found"
    from jinja2 import Template
    return Template(ADMIN_HTML).render(school=school, code=code, locked=is_locked(school))

@app.route("/admin/<code>/add_van", methods=["POST"])
def add_van(code):
    db = load_db()
    plate = request.form['plate'].upper().strip().replace(" ", "")
    db['schools'][code]['vans'][plate] = {"plate": plate,"driver_name": request.form['driver_name'],"driver_phone": request.form['driver_phone']}
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/delete_van/<plate>")
def delete_van(code, plate):
    db = load_db()
    db['schools'][code]['vans'].pop(plate.upper(), None)
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/delete_kid/<kid_id>")
def delete_kid(code, kid_id):
    db = load_db()
    db['schools'][code]['kids'].pop(kid_id, None)
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/add_kid", methods=["POST"])
def add_kid(code):
    db = load_db()
    import uuid
    kid_id = str(uuid.uuid4())[:8].upper()
    van_plate = request.form['van_plate'].upper().strip().replace(" ", "")
    db['schools'][code]['kids'][kid_id] = {"id": kid_id,"name": request.form['kid_name'],"stage": request.form['stage'],"parent_phone": request.form['parent_phone'],"van_plate": van_plate,"status": "At Home - waiting for van","times": {},"absent": False}
    save_db(db)
    t = current_time_str()
    whatsapp_template(request.form['parent_phone'], "picked_home", [request.form['kid_name'], t, van_plate])
    return redirect(f"/admin/{code}")

@app.route("/api/<code>/reset_all", methods=["POST"])
def reset_all(code):
    db = load_db()
    for kid in db['schools'][code]['kids'].values():
        kid['times']={}; kid['status']="At Home - waiting for van"; kid['absent']=False; kid.pop('dropped_home_ts',None)
    save_db(db)
    return redirect(f"/admin/{code}")

DRIVER_HTML = CSS + """<h2>Driver: {{van.driver_name}} - Van {{van.plate}} - {{school.name}}</h2>{% if locked %}<div class="card" style="background:#ffcccc"><h1>PAY TO UNLOCK</h1></div>{% endif %}<p>Code: {{code}} | {{today}} | {{kids|length}} kids</p><div class="card"><h3>Custom Alert</h3><input id="trafficMsg" placeholder="Tyre busted..." style="width:95%"><br><button class="btn-red" onclick="sendTraffic()">SEND ALERT</button></div><hr><div class="grid">{% for kid_id, kid in kids.items() %}<div class="card"><b>{{kid.name}}</b> - {{kid.stage}}<br>Status: <span class="badge">{{kid.status}}</span><div class="progress"><div class="progress-fill" style="width: {{kid.progress}}%"></div></div><br><button onclick="action('{{kid.id}}','picked_home')">PICKED HOME</button><button onclick="action('{{kid.id}}','dropped_school')">DROPPED SCHOOL</button><button onclick="action('{{kid.id}}','picked_school')">PICKED SCHOOL</button><button onclick="action('{{kid.id}}','dropped_home')">DROPPED HOME</button><br><button class="btn-orange" onclick="action('{{kid.id}}','absent')">ABSENT</button><button class="btn-grey" onclick="action('{{kid.id}}','present')">BACK</button></div>{% endfor %}</div><script>function action(kid_id, act){fetch('/api/{{code}}/{{van.plate}}/action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({kid_id: kid_id, action: act})}).then(r=>r.json()).then(j=>{ location.reload(); })}function sendTraffic(){let msg = document.getElementById('trafficMsg').value; if(!msg){ alert('Type reason!'); return; }fetch('/api/{{code}}/{{van.plate}}/traffic', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})}).then(r=>r.json()).then(j=>{ alert('Sent!'); document.getElementById('trafficMsg').value=''; })}</script>"""

@app.route("/driver/<code>/<plate>")
def driver_page(code, plate):
    db = load_db()
    school = db['schools'].get(code)
    if not school: return "No school"
    van = school['vans'].get(plate.upper().strip().replace(" ",""))
    if not van: return f"No van {plate}. Available: {list(school['vans'].keys())}"
    locked = is_locked(school)
    kids = {k:v for k,v in school['kids'].items() if v['van_plate']==plate.upper().strip().replace(" ","")}
    for kid in kids.values():
        cnt=0
        if 'picked_home' in kid['times']: cnt=25
        if 'dropped_school' in kid['times']: cnt=50
        if 'picked_school' in kid['times']: cnt=75
        if 'dropped_home' in kid['times']: cnt=100
        kid['progress']=cnt
    from jinja2 import Template
    return Template(DRIVER_HTML).render(school=school, van=van, code=code, kids=kids, locked=locked, today=str(date.today()))

@app.route("/api/<code>/<plate>/action", methods=["POST"])
def driver_action(code, plate):
    db = load_db()
    school = db['schools'].get(code)
    if not school: return jsonify({"ok": False})
    if is_locked(school):
        return jsonify({"ok": False, "locked": True}), 403
    data = request.get_json()
    kid_id = data['kid_id']; act = data['action']
    kid = db['schools'][code]['kids'][kid_id]
    now = datetime.now().strftime("%I:%M %p %d %b")
    short = datetime.now().strftime("%I:%M %p")
    plate_clean = plate.upper().strip().replace(" ","")

    if act == 'picked_home':
        kid['status']=f"On way to School - picked at {short}"; kid['times']['picked_home']=now
        whatsapp_template(kid['parent_phone'], "picked_home", [kid['name'], short, plate_clean])
    elif act == 'dropped_school':
        kid['status']=f"At School - arrived at {short}"; kid['times']['dropped_school']=now
        whatsapp_template(kid['parent_phone'], "dropped_school", [kid['name'], short])
    elif act == 'picked_school':
        kid['status']=f"On way Home - left at {short}"; kid['times']['picked_school']=now
        whatsapp_template(kid['parent_phone'], "picked_school", [kid['name'], short, plate_clean])
    elif act == 'dropped_home':
        kid['status']=f"Home Safe - {short}"; kid['times']['dropped_home']=now; kid['dropped_home_ts']=datetime.now().isoformat()
        whatsapp_template(kid['parent_phone'], "dropped_home", [kid['name'], short])
    elif act == 'absent':
        kid['status']="ABSENT Today"; kid['absent']=True
    elif act == 'present':
        kid['absent']=False; kid['status']="At Home - waiting for van"; kid['times']={}
    save_db(db)
    return jsonify({"ok": True})

@app.route("/api/<code>/<plate>/traffic", methods=["POST"])
def traffic(code, plate):
    db = load_db()
    school = db['schools'].get(code)
    if is_locked(school):
        return jsonify({"locked": True}), 403
    custom_msg = (request.get_json() or {}).get('message','').strip() or "Traffic ~15 mins late"
    plate_clean = plate.upper().strip().replace(" ","")
    for kid in [k for k in school['kids'].values() if k['van_plate']==plate_clean and not k['absent']]:
        whatsapp_template(kid['parent_phone'], "traffic_alert", [plate_clean, custom_msg])
    return jsonify({"sent": True})

@app.route("/p/<kid_id>")
def parent_view(kid_id):
    db = load_db()
    for code, school in db['schools'].items():
        if kid_id in school['kids']:
            kid = school['kids'][kid_id]
            van = school['vans'][kid['van_plate']]
            timeline = "<br>".join([f"- {k}: {v}" for k,v in kid['times'].items()]) or "Waiting..."
            return CSS + f"<div class='card'><h2>{kid['name']}</h2>Stage: {kid['stage']}<br>Van: {kid['van_plate']} Driver {van['driver_name']}<br><h3>Status: {kid['status']}</h3><b>Timeline:</b><br>{timeline}<br><br><i>Live Fikisha</i></div>"
    return "Kid not found"

@app.route("/report/<code>")
def report(code):
    db = load_db()
    school = db['schools'].get(code)
    total=len(school['kids']); picked=sum(1 for k in school['kids'].values() if 'picked_home' in k['times']); dropped=sum(1 for k in school['kids'].values() if 'dropped_home' in k['times'])
    return CSS + f"<div class='card'><pre>REPORT {school['name']} {date.today()} Total:{total} Picked:{picked} Dropped:{dropped}</pre></div>"

@app.route("/test_wa/<phone>")
def test_wa(phone):
    t = current_time_str()
    results = {}
    ok1, r1 = whatsapp_template(phone, "picked_home", ["Musa", t, "UAA123"])
    results["picked_home"] = {"ok": ok1, "resp": r1[:500]}
    ok2, r2 = whatsapp_template(phone, "dropped_school", ["Musa", t])
    results["dropped_school"] = {"ok": ok2, "resp": r2[:500]}
    ok3, r3 = whatsapp_template(phone, "picked_school", ["Musa", t, "UAA123"])
    results["picked_school"] = {"ok": ok3, "resp": r3[:500]}
    ok4, r4 = whatsapp_template(phone, "dropped_home", ["Musa", t])
    results["dropped_home"] = {"ok": ok4, "resp": r4[:500]}
    ok5, r5 = whatsapp_template(phone, "traffic_alert", ["UAA123", "Heavy traffic on Entebbe road, 20 mins delay"])
    results["traffic_alert"] = {"ok": ok5, "resp": r5[:500]}
    return jsonify(results)

@app.route("/debug/<code>")
def debug(code):
    db = load_db()
    school = db['schools'].get(code.upper())
    return f"<pre>{json.dumps(school, indent=2)}</pre>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)