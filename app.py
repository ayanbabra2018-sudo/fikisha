from flask import Flask, render_template_string, request, redirect, jsonify
import json, os
from datetime import datetime, date

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

@app.route("/")
def home():
    db_data = load_db()
    html = """
    <h2>🚐 Fikisha Super Admin - YOU</h2>
    <form method="post" action="/create_school">
     School Name: <input name="school_name" required>
     Code: <input name="code" placeholder="BRIGHT123" required>
     Director WhatsApp: <input name="director" placeholder="2567..." required>
     Paid Until: <input name="paid_until" type="date" required>
     <button>Create School</button>
    </form><hr><h3>Schools:</h3>
    """
    for code, s in db_data.get("schools", {}).items():
        html += f"<b>{s['name']}</b> ({code}) Paid:{s['paid_until']} | <a href='/admin/{code}'>Admin</a> | <a href='/report/{code}'>Report</a><br>"
        for vp, van in s.get('vans', {}).items():
            html += f"&nbsp;&nbsp;🚐 {vp} - {van['driver_name']} - <a href='/driver/{code}/{vp}'>Driver Link</a><br>"
        html += "<hr>"
    return html

@app.route("/create_school", methods=["POST"])
def create_school():
    db = load_db()
    code = request.form['code'].upper().strip()
    db['schools'][code] = {
        "name": request.form['school_name'],
        "code": code,
        "director_phone": request.form['director'],
        "paid_until": request.form['paid_until'],
        "vans": {},
        "kids": {}
    }
    save_db(db)
    return redirect(f"/admin/{code}")

ADMIN_HTML = """
<h2>🏫 {{school.name}} Admin ({{code}}) - Paid until {{school.paid_until}}</h2>
<a href="/">Super Admin</a> | <a href="/report/{{code}}">Daily Report</a>
<hr>
<h3>Add Van</h3>
<form method="post" action="/admin/{{code}}/add_van">
 Plate: <input name="plate" placeholder="UAA123" required>
 Driver Name: <input name="driver_name" required>
 Driver Phone: <input name="driver_phone" required>
 <button>Add Van</button>
</form>
<hr>
<h3>Add Kid</h3>
<form method="post" action="/admin/{{code}}/add_kid">
 Name: <input name="kid_name" required>
 Stage: <input name="stage" placeholder="Kisaasi Trading Centre" required>
 Parent WhatsApp: <input name="parent_phone" placeholder="2567..." required>
 Van: <select name="van_plate" required>
 {% for vp in school.vans %}<option value="{{vp}}">{{vp}}</option>{% endfor %}
 </select>
 <button>Add Kid</button>
</form>
<hr>
<h3>Vans & Kids</h3>
{% for vp, van in school.vans.items() %}
<b>🚐 {{vp}} - {{van.driver_name}} ({{van.driver_phone}})</b> - <a href="/driver/{{code}}/{{vp}}">Open Driver Page</a><br>
{% for kid_id, kid in school.kids.items() if kid.van_plate==vp %}
&nbsp;&nbsp; - {{kid.name}} ({{kid.stage}}) - {{kid.parent_phone}} - <a href="/p/{{kid_id}}">Parent View</a><br>
{% endfor %}<br>
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
    db['schools'][code]['vans'][plate] = {
        "plate": plate,
        "driver_name": request.form['driver_name'],
        "driver_phone": request.form['driver_phone']
    }
    save_db(db)
    return redirect(f"/admin/{code}")

@app.route("/admin/<code>/add_kid", methods=["POST"])
def add_kid(code):
    db = load_db()
    import uuid
    kid_id = str(uuid.uuid4())[:8].upper()
    db['schools'][code]['kids'][kid_id] = {
        "id": kid_id,
        "name": request.form['kid_name'],
        "stage": request.form['stage'],
        "parent_phone": request.form['parent_phone'],
        "van_plate": request.form['van_plate'].upper(),
        "status": "At Home",
        "times": {},
        "absent": False
    }
    save_db(db)
    whatsapp(request.form['parent_phone'], f"Fikisha: {request.form['kid_name']} added to Van {request.form['van_plate']}. Parent view: fikisha.com/p/{kid_id}")
    return redirect(f"/admin/{code}")

DRIVER_HTML = """
<h2>🚐 Driver: {{van.driver_name}} - Van {{van.plate}} - {{school.name}}</h2>
{% if locked %}<h1 style="color:red">🔒 PAY TO UNLOCK - School payment expired on {{school.paid_until}}. Call Fikisha Admin.</h1>{% endif %}
<p>Code: {{code}} | Date: {{today}}</p>
<div style="background:#ffe6e6;padding:10px;margin:10px 0;">
<input id="customMsg" placeholder="Type message to ALL parents... e.g. Van breakdown at Kireka" style="width:70%;padding:10px;">
<button onclick="sendTraffic()" style="background:red;color:white;padding:10px;">SEND TO ALL</button>
</div>
<hr>
<div style="display:grid; grid-template-columns:1fr 1fr; gap:10px">
{% for kid_id, kid in kids.items() %}
<div style="border:2px solid #333; padding:10px; background: {{'#ccc' if kid.absent else '#e6ffe6'}}">
<b>{{kid.name}}</b> - {{kid.stage}}<br>
Parent: {{kid.parent_phone}} - <a href="/p/{{kid.id}}" target="_blank">Parent View</a><br>
Status: <b>{{kid.status}}</b><br>
Times: {{kid.times}}<br>
{% if not locked %}
<button onclick="action('{{kid.id}}','picked_home')">PICKED HOME</button>
<button onclick="action('{{kid.id}}','dropped_school')">DROPPED SCHOOL</button>
<button onclick="action('{{kid.id}}','picked_school')">PICKED SCHOOL</button>
<button onclick="action('{{kid.id}}','dropped_home')">DROPPED HOME</button>
<button onclick="action('{{kid.id}}','absent')" style="background:orange">ABSENT</button>
<button onclick="action('{{kid.id}}','present')" style="background:lightblue">BACK TO PRESENT</button>
{% endif %}
</div>
{% endfor %}
</div>
<script>
function action(kid_id, act){
 fetch('/api/{{code}}/{{van.plate}}/action', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({kid_id: kid_id, action: act})}).then(()=>location.reload())
}
function sendTraffic(){
 let msg = document.getElementById('customMsg').value;
 if(!msg) msg = 'Traffic - 15 mins late';
 fetch('/api/{{code}}/{{van.plate}}/traffic', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})}).then(()=>{alert('Sent to all parents!'); document.getElementById('customMsg').value='';})
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
    from jinja2 import Template
    return Template(DRIVER_HTML).render(school=school, van=van, code=code, kids=kids, locked=locked, today=str(date.today()))

@app.route("/api/<code>/<plate>/action", methods=["POST"])
def driver_action(code, plate):
    db = load_db()
    data = request.get_json()
    kid_id = data['kid_id']
    act = data['action']
    kid = db['schools'][code]['kids'][kid_id]
    now = datetime.now().strftime("%I:%M %p %d %b")
    if act == 'picked_home':
        kid['status'] = f"Picked HOME at {now}"
        kid['times']['picked_home'] = now
        kid['absent'] = False
        whatsapp(kid['parent_phone'], f"Fikisha: {kid['name']} PICKED at HOME ({kid['stage']}) at {now} by Van {plate}. Live: fikisha.com/p/{kid_id}")
    elif act == 'dropped_school':
        kid['status'] = f"Dropped SCHOOL at {now}"
        kid['times']['dropped_school'] = now
        whatsapp(kid['parent_phone'], f"Fikisha: {kid['name']} DROPPED at SCHOOL at {now}.")
    elif act == 'picked_school':
        kid['status'] = f"Picked SCHOOL at {now}"
        kid['times']['picked_school'] = now
        whatsapp(kid['parent_phone'], f"Fikisha: {kid['name']} PICKED at SCHOOL at {now}. On way home.")
    elif act == 'dropped_home':
        kid['status'] = f"Dropped HOME at {now} ✅"
        kid['times']['dropped_home'] = now
        whatsapp(kid['parent_phone'], f"Fikisha: {kid['name']} DROPPED at HOME at {now}. ✅ Day complete.")
    elif act == 'absent':
        kid['status'] = "ABSENT Today"
        kid['absent'] = True
        whatsapp(kid['parent_phone'], f"Fikisha: {kid['name']} marked ABSENT today by driver. No pickup.")
    elif act == 'present':
        kid['absent'] = False
        kid['status'] = "At Home"
    save_db(db)
    return jsonify({"ok": True})

@app.route("/api/<code>/<plate>/traffic", methods=["POST"])
def traffic(code, plate):
    db = load_db()
    data = request.get_json() or {}
    custom = data.get('message', 'Traffic - 15 mins late')
    now = datetime.now().strftime("%I:%M %p %d %b")
    school = db['schools'][code]
    kids = [k for k in school['kids'].values() if k['van_plate']==plate.upper() and not k['absent']]
    for kid in kids:
        whatsapp(kid['parent_phone'], f"Fikisha ALERT Van {plate} at {now}: {custom}. Driver: {school['vans'][plate.upper()]['driver_name']}")
    return jsonify({"sent": len(kids)})

@app.route("/p/<kid_id>")
def parent_view(kid_id):
    db = load_db()
    for code, school in db['schools'].items():
        if kid_id in school['kids']:
            kid = school['kids'][kid_id]
            van = school['vans'][kid['van_plate']]
            return f"<h2>👨‍👩‍👧 Parent View: {kid['name']}</h2>Stage: {kid['stage']}<br>Van: {kid['van_plate']} - Driver {van['driver_name']} {van['driver_phone']}<br><h3>Status: {kid['status']}</h3>Times Today: {kid['times']}<br><br><i>Live from Fikisha</i>"
    return "Kid not found"

@app.route("/report/<code>")
def report(code):
    db = load_db()
    school = db['schools'].get(code)
    if not school: return "No school"
    total = len(school['kids'])
    picked_home = sum(1 for k in school['kids'].values() if 'picked_home' in k['times'])
    dropped_home = sum(1 for k in school['kids'].values() if 'dropped_home' in k['times'])
    absent = sum(1 for k in school['kids'].values() if k['absent'])
    msg = f"Fikisha DAILY REPORT - {school['name']} - {date.today()}:\nTotal Kids: {total}\nPicked Home: {picked_home}\nDropped Home: {dropped_home}\nAbsent: {absent}\nVans: {len(school['vans'])}"
    return f"<pre>{msg}</pre><br><form method='post' action='/api/{code}/send_report'><button>Send WhatsApp Report to Director ({school['director_phone']})</form>"

@app.route("/api/<code>/send_report", methods=["POST"])
def send_report(code):
    db = load_db()
    school = db['schools'][code]
    total = len(school['kids'])
    picked_home = sum(1 for k in school['kids'].values() if 'picked_home' in k['times'])
    dropped_home = sum(1 for k in school['kids'].values() if 'dropped_home' in k['times'])
    absent = sum(1 for k in school['kids'].values() if k['absent'])
    msg = f"Fikisha DAILY REPORT - {school['name']} - {date.today()}:\nTotal: {total}, Picked Home: {picked_home}, Dropped Home: {dropped_home}, Absent: {absent}"
    whatsapp(school['director_phone'], msg)
    return f"Report sent to {school['director_phone']}<br><a href='/admin/{code}'>Back</a>"

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)