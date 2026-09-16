from flask import Flask, request, redirect, jsonify
import json, os
from datetime import datetime, date, timedelta

app = Flask(__name__)
DB_FILE = "fikisha_db.json"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {"schools": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=2)

def whatsapp(to, msg):
    print(f"\n=== WHATSAPP TO {to} ===\n{msg}\n========================\n")

def maybe_auto_reset(kid):
    ts = kid.get('dropped_home_ts')
    if ts:
        try:
            dropped_time = datetime.fromisoformat(ts)
            if datetime.now() - dropped_time > timedelta(hours=2):
                kid['times'] = {}
                kid['status'] = "At Home 🏠 — waiting for van"
                kid['absent'] = False
                kid.pop('dropped_home_ts', None)
                return True
        except: pass
    return False

CSS = """
<style>
body{font-family:system-ui,Arial;background:#FFF8E1;margin:0;padding:15px;color:#1a1a1a}
h2{color:#0D2A54;border-bottom:3px solid #FFC107;padding-bottom:8px}
h3{color:#0D2A54}
.card{background:white;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 4px 14px rgba(0,0,0,0.1);border-top:5px solid #FFC107}
input,select{padding:11px;border-radius:10px;border:2px solid #FFC107;margin:6px;width:90%;font-size:15px}
button{padding:11px 20px;border-radius:10px;border:none;background:#0D2A54;color:#FFC107;font-weight:bold;cursor:pointer;margin:5px;font-size:15px;transition:0.2s}
button:hover{transform:scale(1.03)}
.btn-done{background:#0a7a2a!important;color:white!important;box-shadow:0 0 0 2px #0a5c26 inset}
.btn-red{background:#d32f2f;color:white}.btn-orange{background:#ef6c00;color:white}.btn-blue{background:#1565c0;color:white}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px} @media(max-width:700px){.grid{grid-template-columns:1fr}}
.badge{padding:6px 12px;border-radius:20px;font-size:13px;font-weight:bold;background:#0D2A54;color:#FFC107}
.progress{height:10px;background:#eee;border-radius:10px;overflow:hidden;margin:10px 0}
.progress-fill{height:100%;background:linear-gradient(90deg,#0a7a2a,#FFC107);transition:width 0.5s}
a{color:#1565c0;font-weight:bold;text-decoration:none}
</style>
"""

@app.route("/")
def home():
    db_data = load_db()
    html = CSS + "<h2>🚐 FIKISHA — Super Admin (YOU)</h2>"
    html += """
    <div class="card"><h3>Create New School</h3>
    <form method="post" action="/create_school">
     <input name="school_name" placeholder="Bright Angels" required>
     <input name="code" placeholder="Code BRIGHT123" required>
     <input name="director" placeholder="Director WhatsApp 2567..." required>
     <input name="paid_until" type="date" required>
     <button>Add School +</button>
    </form></div><hr>
    """
    for code, s in db_data.get("schools", {}).items():
        html += f"<div class='card'><b>🏫 {s['name']}</b> ({code}) — Paid: {s['paid_until']}<br><a href='/admin/{code}'>⚙️ Manage</a> | <a href='/report/{code}'>📊 Report</a><br>"
        for vp, van in s.get('vans', {}).items():
            html += f"🚐 <b>{vp}</b> - {van['driver_name']} - <a href='/driver/{code}/{vp}'>🔗 Driver Page</a><br>"
        html += "</div>"
    return html

@app.route("/create_school", methods=["POST"])
def create_school():
    db = load_db()
    code = request.form['code'].upper().strip()
    db['schools'][code] = {"name": request.form['school_name'],"code": code,"director_phone": request.form['director'],"paid_until": request.form['paid_until'],"vans": {},"kids": {}}
    save_db(db)
    return redirect(f"/admin/{code}")

ADMIN_HTML = CSS + """
<h2>🏫 {{school.name}} Admin ({{code}}) — Paid until {{school.paid_until}}</h2>
<a href="/">⬅️ Super Admin</a> | <a href="/report/{{code}}">📊 Daily Report</a>
<div class="card" style="border-top:5px solid #0a7a2a">
<h3>🔄 Daily Control</h3>
<p>Auto-reset: 2hrs after DROPPED HOME, buttons become fresh.</p>
<form method="post" action="/api/{{code}}/reset_all" onsubmit="return confirm('Reset ALL kids for new day?')">
<button style="background:#0a7a2a;color:white;width:100%;padding:16px;font-size:16px">🔄 RESET ALL BUTTONS FOR TOMORROW</button>
</form>
</div>
<div class="card"><h3>🚐 Add Van</h3>
<form method="post" action="/admin/{{code}}/add_van">
 <input name="plate" placeholder="Plate UAA123" required>
 <input name="driver_name" placeholder="Driver Name" required>
 <input name="driver_phone" placeholder="Driver Phone 2567..." required>
 <button>Add Van</button>
</form></div>
<div class="card"><h3>👶 Add Kid</h3>
<form method="post" action="/admin/{{code}}/add_kid">
 <input name="kid_name" placeholder="Kid Name" required>
 <input name="stage" placeholder="Stage Kisaasi" required>
 <input name="parent_phone" placeholder="Parent WhatsApp 2567..." required>
 Van: <select name="van_plate" required>{% for vp in school.vans %}<option value="{{vp}}">{{vp}}</option>{% endfor %}</select>
 <button>Add Kid</button>
</form></div>
<hr><h3>Vans & Kids</h3>
{% for vp, van in school.vans.items() %}
<div class="card"><b>🚐 {{vp}} - {{van.driver_name}} ({{van.driver_phone}})</b> - <a href="/driver/{{code}}/{{vp}}">🔗 Driver Page</a><br>
{% for kid_id, kid in school.kids.items() if kid.van_plate==vp %}
&nbsp;&nbsp; • {{kid.name}} ({{kid.stage}}) — {{kid.status}} — <a href="/p/{{kid_id}}">Parent View</a><br>
{% endfor %}</div>
{% endfor %}
"""

@app.route("/admin/<code>")
def admin(code):
    db = load_db()
    school = db['schools'].get(code)
    if not school: return "School not found"
    from jinja2 import Template
    return Template(ADMIN_HTML).render(school=school, code=code)

@app.route("/admin/<code>/add_van", methods=["POST"])
def add_van(code):
    db = load_db()
    plate = request.form['plate'].upper().strip()
    db['schools'][code]['vans'][plate] = {"plate": plate,"driver_name": request.form['driver_name'],"driver_phone": request.form['driver_phone']}
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/add_kid", methods=["POST"])
def add_kid(code):
    db = load_db()
    import uuid
    kid_id = str(uuid.uuid4())[:8].upper()
    db['schools'][code]['kids'][kid_id] = {"id": kid_id,"name": request.form['kid_name'],"stage": request.form['stage'],"parent_phone": request.form['parent_phone'],"van_plate": request.form['van_plate'].upper(),"status": "At Home 🏠 — waiting for van","times": {},"absent": False}
    save_db(db)
    whatsapp(request.form['parent_phone'], f"Fikisha: {request.form['kid_name']} added to Van {request.form['van_plate']}. View: fikisha-18fx.onrender.com/p/{kid_id}")
    return redirect(f"/admin/{code}")

@app.route("/api/<code>/reset_all", methods=["POST"])
def reset_all(code):
    db = load_db()
    for kid in db['schools'][code]['kids'].values():
        kid['times'] = {}; kid['status'] = "At Home 🏠 — waiting for van"; kid['absent']=False; kid.pop('dropped_home_ts',None)
    save_db(db)
    return redirect(f"/admin/{code}")

DRIVER_HTML = CSS + """
<h2>🚐 Driver: {{van.driver_name}} — Van {{van.plate}} — {{school.name}}</h2>
{% if locked %}<div class="card" style="background:#ffcccc;border:2px solid red"><h1>🔒 PAY TO UNLOCK — Expired {{school.paid_until}}</h1></div>{% endif %}
<p>Code: {{code}} | {{today}} | {{kids|length}} kids</p>
<div class="card" style="border:2px solid #d32f2f;border-top:5px solid #d32f2f">
<h3 style="color:#d32f2f">🚨 Send Custom Alert to ALL Parents</h3>
<input id="trafficMsg" placeholder="Type reason... e.g. Tyre busted at Kireka..." style="width:95%;padding:14px;border:2px solid #d32f2f">
<br><br>
<button class="btn-red" onclick="sendTraffic()" style="width:100%;padding:14px">🚨 SEND ALERT TO ALL PARENTS</button>
</div><hr>
<div class="grid">
{% for kid_id, kid in kids.items() %}
<div class="card" style="background: {{'#f0f0f0' if kid.absent else 'white'}}; border-left:6px solid {{'grey' if kid.absent else '#FFC107'}}">
<b style="font-size:18px">{{kid.name}}</b> — {{kid.stage}}<br>
Parent: {{kid.parent_phone}} — <a href="/p/{{kid.id}}" target="_blank">👁️ Parent View</a><br>
Status: <span class="badge">{{kid.status}}</span>
<div class="progress"><div class="progress-fill" style="width: {{kid.progress}}%"></div></div>
<small>Times: {{kid.times}}</small><br><br>
{% if not locked %}
<button onclick="action('{{kid.id}}','picked_home')" class="{{'btn-done' if 'picked_home' in kid.times else ''}}">{{ '✅ PICKED HOME' if 'picked_home' in kid.times else '🔲 PICKED HOME' }}</button>
<button onclick="action('{{kid.id}}','dropped_school')" class="{{'btn-done' if 'dropped_school' in kid.times else ''}}">{{ '✅ DROPPED SCHOOL' if 'dropped_school' in kid.times else '🏫 DROPPED SCHOOL' }}</button>
<button onclick="action('{{kid.id}}','picked_school')" class="{{'btn-done' if 'picked_school' in kid.times else ''}}">{{ '✅ PICKED SCHOOL' if 'picked_school' in kid.times else '🏫 PICKED SCHOOL' }}</button>
<button onclick="action('{{kid.id}}','dropped_home')" class="{{'btn-done' if 'dropped_home' in kid.times else 'btn-blue'}}">{{ '✅ DROPPED HOME' if 'dropped_home' in kid.times else '🏠 DROPPED HOME' }}</button><br>
<button class="btn-orange" onclick="action('{{kid.id}}','absent')">🚫 ABSENT</button>
<button onclick="action('{{kid.id}}','present')" style="background:#888;color:white">↩️ BACK PRESENT</button>
{% endif %}
</div>
{% endfor %}
</div>
<script>
function action(kid_id, act){
 fetch('/api/{{code}}/{{van.plate}}/action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({kid_id: kid_id, action: act})}).then(()=>location.reload())
}
function sendTraffic(){
 let msg = document.getElementById('trafficMsg').value;
 if(!msg){ alert('Please type reason first!'); return; }
 fetch('/api/{{code}}/{{van.plate}}/traffic', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})}).then(()=>{ alert('Alert sent! ✅\\n'+msg); document.getElementById('trafficMsg').value=''; })
}
</script>
"""

@app.route("/driver/<code>/<plate>")
def driver_page(code, plate):
    db = load_db()
    school = db['schools'].get(code)
    if not school: return "No school"
    van = school['vans'].get(plate.upper())
    if not van: return "No van"
    locked = school['paid_until'] < str(date.today())
    kids = {k:v for k,v in school['kids'].items() if v['van_plate']==plate.upper()}
    changed=False
    for kid in kids.values():
        if maybe_auto_reset(kid): changed=True
        cnt=0
        if 'picked_home' in kid['times']: cnt=25
        if 'dropped_school' in kid['times']: cnt=50
        if 'picked_school' in kid['times']: cnt=75
        if 'dropped_home' in kid['times']: cnt=100
        kid['progress']=cnt
    if changed: save_db(db)
    from jinja2 import Template
    return Template(DRIVER_HTML).render(school=school, van=van, code=code, kids=kids, locked=locked, today=str(date.today()))

@app.route("/api/<code>/<plate>/action", methods=["POST"])
def driver_action(code, plate):
    db = load_db()
    data = request.get_json()
    kid_id = data['kid_id']; act = data['action']
    kid = db['schools'][code]['kids'][kid_id]
    now = datetime.now().strftime("%I:%M %p %d %b")
    short = datetime.now().strftime("%I:%M %p")

    if act == 'picked_home':
        kid['status'] = f"On way to School 🚐 — picked at {short}"
        kid['times']['picked_home'] = now; kid['absent']=False
        whatsapp(kid['parent_phone'], f"✅ Fikisha: {kid['name']} PICKED HOME at {now} — on way to school. Van {plate}")
    elif act == 'dropped_school':
        kid['status'] = f"At School 🏫 — arrived at {short}"
        kid['times']['dropped_school'] = now
        whatsapp(kid['parent_phone'], f"🏫 Fikisha: {kid['name']} DROPPED at SCHOOL at {now}. Safe.")
    elif act == 'picked_school':
        kid['status'] = f"On way Home 🏠 — left school at {short}"
        kid['times']['picked_school'] = now
        whatsapp(kid['parent_phone'], f"🚐 Fikisha: {kid['name']} PICKED at SCHOOL at {now}. Heading to {kid['stage']}.")
    elif act == 'dropped_home':
        kid['status'] = f"Home Safe ✅ — dropped at {short}"
        kid['times']['dropped_home'] = now
        kid['dropped_home_ts'] = datetime.now().isoformat()
        whatsapp(kid['parent_phone'], f"✅ Fikisha: {kid['name']} DROPPED HOME at {now}. Day complete! Auto-reset in 2hrs.")
    elif act == 'absent':
        kid['status'] = "ABSENT Today 🚫 — no pickup"; kid['absent']=True
        whatsapp(kid['parent_phone'], f"🚫 Fikisha: {kid['name']} marked ABSENT.")
    elif act == 'present':
        kid['absent']=False; kid['status']="At Home 🏠 — waiting for van"; kid['times']={}; kid.pop('dropped_home_ts',None)
    save_db(db)
    return jsonify({"ok": True})

@app.route("/api/<code>/<plate>/traffic", methods=["POST"])
def traffic(code, plate):
    db = load_db()
    school = db['schools'][code]
    custom_msg = (request.get_json() or {}).get('message','').strip() or "Traffic ~15 mins late"
    now = datetime.now().strftime("%I:%M %p")
    for kid in [k for k in school['kids'].values() if k['van_plate']==plate.upper() and not k['absent']]:
        whatsapp(kid['parent_phone'], f"Fikisha ALERT [{now}] Van {plate}: {custom_msg}. Kid: {kid['name']} {kid['stage']}. Driver {school['vans'][plate]['driver_name']} {school['vans'][plate]['driver_phone']}")
    return jsonify({"sent": True})

@app.route("/p/<kid_id>")
def parent_view(kid_id):
    db = load_db()
    for code, school in db['schools'].items():
        if kid_id in school['kids']:
            kid = school['kids'][kid_id]
            van = school['vans'][kid['van_plate']]
            timeline = "<br>".join([f"• {k}: {v}" for k,v in kid['times'].items()]) or "Waiting..."
            return CSS + f"<div class='card'><h2>👨‍👩‍👧 {kid['name']}</h2>Stage: {kid['stage']}<br>Van: {kid['van_plate']} Driver {van['driver_name']} {van['driver_phone']}<br><h3>Status: <span class='badge'>{kid['status']}</span></h3><div class='progress'><div class='progress-fill' style='width: {25*len(kid['times'])}%'></div></div><b>Timeline:</b><br>{timeline}<br><br><i>Live from Fikisha 🚐 fikisha-18fx.onrender.com</i></div>"
    return "Kid not found"

@app.route("/report/<code>")
def report(code):
    db = load_db()
    school = db['schools'].get(code)
    total = len(school['kids']); picked = sum(1 for k in school['kids'].values() if 'picked_home' in k['times']); dropped = sum(1 for k in school['kids'].values() if 'dropped_home' in k['times']); absent = sum(1 for k in school['kids'].values() if k['absent'])
    msg = f"Fikisha DAILY REPORT - {school['name']} - {date.today()}:\nTotal: {total}, Picked: {picked}, Dropped: {dropped}, Absent: {absent}"
    return CSS + f"<div class='card'><pre>{msg}</pre><form method='post' action='/api/{code}/send_report'><button>Send WhatsApp to Director ({school['director_phone']})</button></form><br><a href='/admin/{code}'>Back</a></div>"

@app.route("/api/<code>/send_report", methods=["POST"])
def send_report(code):
    db = load_db(); school=db['schools'][code]
    total=len(school['kids']); picked=sum(1 for k in school['kids'].values() if 'picked_home' in k['times'])
    whatsapp(school['director_phone'], f"REPORT {school['name']} {date.today()} Picked:{picked}/{total}")
    return f"Sent<br><a href='/admin/{code}'>Back</a>"

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)