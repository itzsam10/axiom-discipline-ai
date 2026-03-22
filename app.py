import streamlit as st
import requests
import re
import threading
import json
from groq import Groq
from datetime import datetime, date


# ─── KEYS ──────────────────────────────────────────────────────────────────
GROQ_API_KEY      = "gsk_9Y8Ry..."
HINDSIGHT_API_KEY = "hsk_f85e..."
HINDSIGHT_URL     = "https://api.hindsight.vectorize.io"
MH = {"Authorization": f"Bearer {HINDSIGHT_API_KEY}", "Content-Type": "application/json"}
client = Groq(api_key=GROQ_API_KEY)

st.set_page_config(page_title="AXIOM · Discipline AI", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")

# ─── STATE ─────────────────────────────────────────────────────────────────
DEFAULTS = {
    "score": 820,
    "messages": [],
    "today_status": "empty",
    "last_delta": 0,
    "last_fired_hour": -1,
    "last_fire_date": "",
    "discipline_plan": [],      # list of {hour, task, fired}
    "plan_date": "",            # date plan was created
    "plan_shown": False,        # whether plan has been shown in sidebar
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

pct = round((st.session_state.score / 1000) * 100, 1)
now = datetime.now()
today_str = str(date.today())

# ─── HELPERS ───────────────────────────────────────────────────────────────
def save_bg(user_msg, ai_msg):
    def _go():
        try:
            ts = now.strftime("%d %b %Y %I:%M %p")
            requests.post(f"{HINDSIGHT_URL}/v1/memories", headers=MH,
                json={"text": f"[{ts}] Samith: {user_msg} | AXIOM: {ai_msg}"}, timeout=6)
        except: pass
    threading.Thread(target=_go, daemon=True).start()

def recall(query):
    try:
        r = requests.post(f"{HINDSIGHT_URL}/v1/memories/search", headers=MH,
            json={"query": query, "top_k": 2}, timeout=2)
        if r.status_code == 200:
            res = r.json().get("results", [])
            if res:
                return " | ".join(x.get("text","") for x in res if x.get("text"))
    except: pass
    return "No prior records."

def strip_tag(text):
    return re.sub(r'\s*\[SCORE:[+-]\d+\].*', '', text, flags=re.DOTALL).strip()

def get_pts(text):
    m = re.search(r'\[SCORE:([+-]\d+)\]', text)
    return int(m.group(1)) if m else None

def stream_reply(system_prompt, user_text, history=None):
    """Stream Groq reply. Returns (full_raw, clean_text)."""
    messages = [{"role":"system","content":system_prompt}]
    if history:
        for m in (history[-6:]):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role":"user","content":user_text})
    full = ""
    ph = st.empty()
    try:
        stream = client.chat.completions.create(
            messages=messages, model="llama-3.1-8b-instant",
            temperature=0.55, max_tokens=300, stream=True,
        )
        for chunk in stream:
            tok = chunk.choices[0].delta.content
            if tok:
                full += tok
                ph.markdown(strip_tag(full) + " ▌")
        clean = strip_tag(full)
        ph.markdown(clean)
    except Exception as e:
        clean = f"Error — {e}"
        ph.error(clean)
        full = clean
    return full, clean

def generate_discipline_plan(user_summary: str) -> list:
    """Ask Groq to create tomorrow's hour-by-hour plan as JSON."""
    prompt = f"""You are a strict discipline planner. Based on this student's report:
"{user_summary}"

Create a realistic discipline schedule for TOMORROW. Output ONLY valid JSON — a list of objects.
Each object: {{"hour": 6, "task": "Wake up, 20 min walk before breakfast", "type": "health"}}

Rules:
- hour is 0-23 (24h format)
- Include 6-8 slots covering: morning routine, study sessions (VLSI, DSP, NeuroKey), gym, meals, club work, sleep
- task should be specific and short (under 12 words)
- type is one of: health, study, project, startup, rest
- Output ONLY the JSON array. No explanation. No markdown. Just the array."""

    try:
        resp = client.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant",
            temperature=0.3, max_tokens=400, stream=False,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'^```json|^```|```$', '', raw, flags=re.MULTILINE).strip()
        plan_data = json.loads(raw)
        # add fired=False to each
        return [{"hour": p["hour"], "task": p["task"], "type": p.get("type","study"), "fired": False}
                for p in plan_data if "hour" in p and "task" in p]
    except:
        # fallback plan
        return [
            {"hour": 6,  "task": "Wake up — no snooze", "type": "health", "fired": False},
            {"hour": 8,  "task": "VLSI Design — 1 chapter", "type": "study", "fired": False},
            {"hour": 11, "task": "DSP assignment or revision", "type": "study", "fired": False},
            {"hour": 14, "task": "NeuroKey — 1 hour coding", "type": "project", "fired": False},
            {"hour": 17, "task": "Gym session", "type": "health", "fired": False},
            {"hour": 19, "task": "Tinkercore / IoT Club tasks", "type": "startup", "fired": False},
            {"hour": 22, "task": "Wind down — sleep by 11 PM", "type": "rest", "fired": False},
        ]

def check_plan_notifications():
    """Check if any plan item should fire right now."""
    if not st.session_state.discipline_plan:
        return False
    if st.session_state.plan_date != today_str:
        return False  # plan is for a different day

    hour = now.hour
    today_fire_key = f"plan_fired_{today_str}_{hour}"

    for i, item in enumerate(st.session_state.discipline_plan):
        if item["hour"] == hour and not item["fired"]:
            if today_fire_key not in st.session_state:
                st.session_state[today_fire_key] = True
                st.session_state.discipline_plan[i]["fired"] = True
                type_icon = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}.get(item["type"],"📌")
                notif = (f"⏰ **Scheduled Task — {now.strftime('%I:%M %p')}**\n\n"
                         f"{type_icon} It's time for: **{item['task']}**\n\n"
                         f"Are you on it? Report back when done.")
                st.session_state.messages.append({"role": "assistant", "content": notif})
                return True
    return False

def check_fixed_schedule():
    """Fixed daily check-ins (morning, midday, evening, night)."""
    FIXED = {
        7:  "🌅 **Morning Check-In** — 7 AM. State your top 3 targets for today: **NeuroKey**, **VLSI/DSP**, and **gym**. No vague answers.",
        13: "☀️ **Midday Pulse** — Half the day is gone. What have you completed? Are you on track with morning targets? Did you eat properly?",
        20: "🌙 **Evening Accountability** — Log everything: NeuroKey progress, VLSI/DSP coursework, gym, Tinkercore. I'm scoring this.",
        22: "🔔 **Final Check — 10 PM.** Wrap up. Confirm tasks done, food eaten, sleep time set. What did you actually finish today?",
    }
    if st.session_state.last_fire_date != today_str:
        st.session_state.last_fired_hour = -1
        st.session_state.last_fire_date = today_str

    hour = now.hour
    if hour in FIXED and st.session_state.last_fired_hour != hour:
        st.session_state.last_fired_hour = hour
        st.session_state.messages.append({"role": "assistant", "content": FIXED[hour]})
        return True
    return False

# ─── LOGO SVG — shield + split brain (matches your design) ─────────────────
LOGO_SM = (
    '<svg width="40" height="40" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">'
    '<defs>'
    '<linearGradient id="sb1" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">'
    '<stop offset="0%" stop-color="#2D5FA8"/><stop offset="100%" stop-color="#0B2248"/></linearGradient>'
    '</defs>'
    '<path d="M20 2L4 8v10.5C4 27.5 11 35 20 38 29 35 36 27.5 36 18.5V8L20 2Z" fill="url(#sb1)" stroke="#3A72C8" stroke-width="1.2"/>'
    '<path d="M20 2L4 8v10.5C4 27.5 11 35 20 38V2Z" fill="white" opacity=".05"/>'
    '<line x1="20" y1="11" x2="20" y2="29" stroke="#7BC8EE" stroke-width=".9" opacity=".9"/>'
    '<path d="M20 11C17 11 14 13 14 16.5C13 17.5 12.5 19 13.5 21C12.5 22.5 12.5 25 14.5 26.5C15 28 17 29.2 19.2 29.2H20V11Z" fill="none" stroke="#72C8F0" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M14.5 18C16 17 17 18 15.5 19.5" stroke="#72C8F0" stroke-width="1" stroke-linecap="round"/>'
    '<path d="M13.5 22.5C15.5 21.5 16.5 24 14.5 25" stroke="#72C8F0" stroke-width="1" stroke-linecap="round"/>'
    '<path d="M20 11C23 11 26 13 26 16.5C27 17.5 27.5 19 26.5 21C27.5 22.5 27.5 25 25.5 26.5C25 28 23 29.2 20.8 29.2H20V11Z" fill="none" stroke="#2E6EC0" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>'
    '<line x1="22" y1="15.5" x2="25" y2="15.5" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/>'
    '<line x1="24" y1="15.5" x2="24" y2="19" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/>'
    '<line x1="22" y1="22" x2="25.5" y2="22" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/>'
    '<circle cx="22" cy="22" r="1" fill="#2E6EC0"/><circle cx="25.5" cy="22" r="1" fill="#2E6EC0"/>'
    '</svg>'
)

LOGO_LG = (
    '<svg width="54" height="54" viewBox="0 0 54 54" fill="none" xmlns="http://www.w3.org/2000/svg">'
    '<defs>'
    '<linearGradient id="lb1" x1="0" y1="0" x2="54" y2="54" gradientUnits="userSpaceOnUse">'
    '<stop offset="0%" stop-color="#2D5FA8"/><stop offset="100%" stop-color="#0B2248"/></linearGradient>'
    '<linearGradient id="lb2" x1="27" y1="0" x2="27" y2="54" gradientUnits="userSpaceOnUse">'
    '<stop offset="0%" stop-color="#4A90D8" stop-opacity=".15"/><stop offset="100%" stop-color="#4A90D8" stop-opacity="0"/></linearGradient>'
    '</defs>'
    '<path d="M27 3L5 11v14C5 38.5 14.5 48.5 27 52 39.5 48.5 49 38.5 49 25V11L27 3Z" fill="url(#lb1)" stroke="#3A80D0" stroke-width="1.4"/>'
    '<path d="M27 3L5 11v14C5 38.5 14.5 48.5 27 52V3Z" fill="url(#lb2)"/>'
    '<line x1="27" y1="15" x2="27" y2="39" stroke="#90D0F0" stroke-width="1" opacity=".9"/>'
    '<path d="M27 15C23 15 19 18 19 22.5C17.5 24 17 26 18 28.5C17 30.5 17 33.5 19.5 35.5C20 37.5 22.5 39.2 25.5 39.2H27V15Z" fill="none" stroke="#78C8F0" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M19.5 24.5C21.5 23 23 24.5 21 26.5" stroke="#78C8F0" stroke-width="1.2" stroke-linecap="round"/>'
    '<path d="M18.5 30.5C21 29 22.5 32.5 20 34" stroke="#78C8F0" stroke-width="1.2" stroke-linecap="round"/>'
    '<path d="M27 15C31 15 35 18 35 22.5C36.5 24 37 26 36 28.5C37 30.5 37 33.5 34.5 35.5C34 37.5 31.5 39.2 28.5 39.2H27V15Z" fill="none" stroke="#2A68C0" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
    '<line x1="29.5" y1="20" x2="33.5" y2="20" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/>'
    '<line x1="32.5" y1="20" x2="32.5" y2="25" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/>'
    '<line x1="29.5" y1="29.5" x2="34" y2="29.5" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/>'
    '<circle cx="29.5" cy="29.5" r="1.3" fill="#2A68C0"/><circle cx="34" cy="29.5" r="1.3" fill="#2A68C0"/>'
    '<line x1="32.5" y1="25" x2="34" y2="25" stroke="#2A68C0" stroke-width="1.1" stroke-linecap="round"/>'
    '<circle cx="34" cy="25" r="1.1" fill="#2A68C0"/>'
    '</svg>'
)

# ─── CSS ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Outfit:wght@400;500;600;700;800;900&display=swap');
*,*::before,*::after{box-sizing:border-box;}
html,body,.stApp{background:#060A0F !important;font-family:'Outfit',sans-serif !important;color:#D8E6F3 !important;}
#MainMenu,header,footer,.stDeployButton,[data-testid="stToolbar"],[data-testid="stDecoration"]{display:none !important;}
.block-container{padding:1.8rem 2.8rem 5rem !important;max-width:100% !important;}
::-webkit-scrollbar{width:4px;}::-webkit-scrollbar-track{background:#090E16;}::-webkit-scrollbar-thumb{background:#1C2B3A;border-radius:4px;}

[data-testid="stSidebar"]{background:#07090F !important;border-right:1px solid #141E2C !important;min-width:260px !important;max-width:260px !important;}
[data-testid="stSidebar"] .block-container{padding:1.5rem 1.2rem 2rem !important;}

.brand{display:flex;align-items:center;gap:10px;margin-bottom:2px;}
.bname{font-weight:800;font-size:1.2rem;color:#EEF5FF;letter-spacing:-.3px;}
.btag{font-family:'DM Mono',monospace;font-size:.56rem;color:#2C4060;letter-spacing:2px;text-transform:uppercase;margin-left:52px;margin-bottom:12px;}
.pill{display:flex;align-items:center;gap:7px;border-radius:99px;padding:5px 12px;margin-bottom:4px;font-family:'DM Mono',monospace;font-size:.65rem;}
.pg{background:rgba(34,197,94,.07);border:1px solid rgba(34,197,94,.18);color:#22C55E;}
.pb{background:rgba(56,189,248,.07);border:1px solid rgba(56,189,248,.2);color:#38BDF8;}
.dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;animation:pulse 2.5s ease-in-out infinite;}
.dg{background:#22C55E;box-shadow:0 0 5px #22C55E;}
.db{background:#38BDF8;box-shadow:0 0 5px #38BDF8;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}
.sec{font-family:'DM Mono',monospace;font-size:.56rem;color:#1E3050;letter-spacing:2px;text-transform:uppercase;margin:16px 0 7px;padding-bottom:5px;border-bottom:1px solid #111A26;}
.hmap{display:flex;flex-wrap:wrap;gap:3px;}
.hm{width:13px;height:13px;border-radius:3px;transition:transform .1s;cursor:default;}
.hm:hover{transform:scale(1.7);}
.he{background:#0D1520;border:1px solid #182030;}
.ha{background:#2563EB;box-shadow:0 0 5px rgba(37,99,235,.55);}
.hf{background:#DC2626;box-shadow:0 0 5px rgba(220,38,38,.5);}
.ht{outline:1.5px solid #38BDF8;outline-offset:1.5px;}
.hleg{display:flex;gap:11px;margin-top:6px;font-family:'DM Mono',monospace;font-size:.57rem;color:#253545;}
.hd{width:8px;height:8px;border-radius:2px;display:inline-block;margin-right:3px;}
.gc{display:flex;align-items:center;gap:8px;background:#0B1018;border:1px solid #131D2B;border-radius:9px;padding:7px 9px;margin-bottom:4px;}
.ge{font-size:.9rem;flex-shrink:0;}
.gt{font-weight:600;font-size:.78rem;color:#D0DFF0;display:block;line-height:1.2;}
.gs{font-family:'DM Mono',monospace;font-size:.58rem;color:#2C4060;}

/* plan card */
.plan-item{display:flex;align-items:flex-start;gap:8px;padding:6px 8px;margin-bottom:3px;border-radius:7px;background:#0C1320;border:1px solid #131D2B;}
.plan-time{font-family:'DM Mono',monospace;font-size:.62rem;color:#38BDF8;flex-shrink:0;min-width:38px;padding-top:1px;}
.plan-task{font-size:.75rem;color:#A8C0D8;line-height:1.3;}
.plan-done{opacity:.4;text-decoration:line-through;}

/* MAIN HEADER */
.mhdr{display:flex;align-items:center;gap:14px;margin-bottom:16px;}
.mtitle{font-weight:900;font-size:2.6rem;color:#EEF5FF;letter-spacing:-2px;line-height:1;}
/* SUBTITLE — now properly visible */
.msub{
    font-family:'DM Mono',monospace;
    font-size:.65rem;
    color:#5A85A8;
    letter-spacing:2.5px;
    text-transform:uppercase;
    margin-top:5px;
    font-weight:500;
}

.crow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:22px;}
.card{background:#080D14;border:1px solid #131D2B;border-radius:13px;padding:15px 17px;position:relative;overflow:hidden;}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,#38BDF8,#2563EB);opacity:.55;}
.clabel{font-family:'DM Mono',monospace;font-size:.72rem;color:#4A6888;letter-spacing:1px;text-transform:uppercase;margin-bottom:9px;}
.cval{font-weight:800;font-size:2rem;color:#EEF5FF;line-height:1;letter-spacing:-1px;}
.cunit{font-size:.9rem;font-weight:400;color:#2C4060;}
.cbar{height:3px;background:#111A26;border-radius:99px;margin-top:9px;overflow:hidden;}
.cbarf{height:100%;border-radius:99px;background:linear-gradient(90deg,#38BDF8,#2563EB);transition:width 1s cubic-bezier(.25,.46,.45,.94);}
.cstat{font-weight:700;font-size:1.15rem;margin-top:6px;line-height:1.2;}
.cup{font-family:'DM Mono',monospace;font-size:.67rem;color:#22C55E;margin-top:6px;}
.cdn{font-family:'DM Mono',monospace;font-size:.67rem;color:#DC2626;margin-top:6px;}
.cne{font-family:'DM Mono',monospace;font-size:.67rem;color:#1E3050;margin-top:6px;}
.div{border:none;border-top:1px solid #111A26;margin:0 0 18px;}

[data-testid="stChatMessage"]{background:transparent !important;border:none !important;padding:13px 0 !important;border-bottom:1px solid #0D1520 !important;}
[data-testid="chatAvatarIcon-user"]{background:#111D2C !important;border:1px solid #1C2D40 !important;border-radius:9px !important;}
[data-testid="chatAvatarIcon-assistant"]{background:linear-gradient(135deg,#1E4A8A,#0F2A5A) !important;border:1px solid #2A5FAD !important;border-radius:9px !important;box-shadow:0 0 10px rgba(37,99,235,.35) !important;}
[data-testid="stChatMessage"] p{font-family:'Outfit',sans-serif !important;font-size:1rem !important;font-weight:400 !important;line-height:1.75 !important;color:#BDD0E8 !important;}
[data-testid="stChatMessage"] p strong,[data-testid="stChatMessage"] strong{font-weight:700 !important;color:#EEF5FF !important;}
[data-testid="stChatMessage"] li{font-family:'Outfit',sans-serif !important;font-size:.96rem !important;line-height:1.7 !important;color:#BDD0E8 !important;}
[data-testid="stChatInput"]{background:#080D14 !important;border:1px solid #1C2B3A !important;border-radius:13px !important;}
[data-testid="stChatInput"]:focus-within{border-color:#2563EB !important;box-shadow:0 0 0 3px rgba(37,99,235,.12) !important;}
[data-testid="stChatInput"] textarea{font-family:'Outfit',sans-serif !important;font-size:.95rem !important;color:#D8E6F3 !important;background:transparent !important;caret-color:#38BDF8 !important;}
[data-testid="stChatInput"] textarea::placeholder{color:#1C2B3A !important;}
[data-testid="stChatInput"] button{background:linear-gradient(135deg,#2563EB,#1A3A80) !important;border-radius:8px !important;border:none !important;}
[data-testid="stMetric"]{display:none !important;}
</style>
""", unsafe_allow_html=True)

# ─── BOOT: initial check-in if no messages ─────────────────────────────────
if not st.session_state.messages:
    CHECKIN = ("**Daily Discipline Check-In — AXIOM is logging your progress.**\n\n"
               "Answer each one directly:\n\n"
               "1. 🧠 **NeuroKey** — What did you work on today? Any blockers?\n"
               "2. 📡 **VLSI / DSP** — Coursework completed? Assignments pending?\n"
               "3. 💪 **Gym** — Did you hit the gym? Followed the hostel diet?\n"
               "4. 🚀 **Tinkercore / IoT Club** — Any updates, tasks done?\n"
               "5. 😴 **Sleep** — Hours slept last night?\n\n"
               "Reply with your updates. I will score you, build your plan for tomorrow, and hold you accountable.")
    st.session_state.messages.append({"role": "assistant", "content": CHECKIN})

# check schedule on every page load
fired_plan  = check_plan_notifications()
fired_fixed = check_fixed_schedule()
if fired_plan or fired_fixed:
    st.rerun()

# ─── SIDEBAR ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        f'<div class="brand">{LOGO_SM}<span class="bname">AXIOM</span></div>'
        f'<div class="btag">Discipline Intelligence Engine</div>'
        f'<div class="pill pg"><span class="dot dg"></span>Hindsight Memory: LIVE</div>'
        f'<div class="pill pb"><span class="dot db"></span>Groq LLM: CONNECTED</div>',
        unsafe_allow_html=True
    )
    # heatmap
    st.markdown('<div class="sec">30-Day Activity</div>', unsafe_allow_html=True)
    past = ["ha","ha","hf","ha","ha","ha","he","ha","hf","ha","ha","ha",
            "ha","hf","ha","ha","ha","he","ha","ha","ha","ha","ha","hf",
            "ha","ha","he","ha","ha"]
    tcls = {"empty":"he ht","active":"ha ht","fail":"hf ht"}.get(st.session_state.today_status,"he ht")
    boxes = "".join(f'<span class="hm {c}"></span>' for c in past)
    boxes += f'<span class="hm {tcls}" title="Today"></span>'
    st.markdown(
        f'<div class="hmap">{boxes}</div>'
        f'<div class="hleg">'
        f'<span><span class="hd" style="background:#2563EB"></span>On Track</span>'
        f'<span><span class="hd" style="background:#DC2626"></span>Excused</span>'
        f'<span><span class="hd" style="background:#0D1520;border:1px solid #182030"></span>Rest</span>'
        f'</div>', unsafe_allow_html=True
    )
    # goals
    st.markdown('<div class="sec">Active Targets</div>', unsafe_allow_html=True)
    for em,t,s in [("🧠","NeuroKey BCI","EEG + Python Capstone"),("📡","VLSI & DSP","VTU Coursework"),
                   ("🚀","Tinkercore","Startup · ECE Outreach"),("💪","Gym & Nutrition","Hostel Diet · Strength"),
                   ("🔩","IoT Club","Leadership · Workshops")]:
        st.markdown(f'<div class="gc"><span class="ge">{em}</span><div><span class="gt">{t}</span><span class="gs">{s}</span></div></div>', unsafe_allow_html=True)

    # discipline plan (if exists and is for today/tomorrow)
    if st.session_state.discipline_plan:
        st.markdown('<div class="sec">📋 Tomorrow\'s Plan</div>', unsafe_allow_html=True)
        type_icons = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
        for item in st.session_state.discipline_plan:
            hr = item["hour"]
            ampm = "AM" if hr < 12 else "PM"
            h12 = hr if hr <= 12 else hr - 12
            h12 = 12 if h12 == 0 else h12
            icon = type_icons.get(item["type"],"📌")
            done_cls = "plan-done" if item["fired"] else ""
            st.markdown(
                f'<div class="plan-item">'
                f'<span class="plan-time">{h12}{ampm}</span>'
                f'<span class="plan-task {done_cls}">{icon} {item["task"]}</span>'
                f'</div>', unsafe_allow_html=True
            )

# ─── MAIN HEADER ───────────────────────────────────────────────────────────
st.markdown(
    f'<div class="mhdr">{LOGO_LG}'
    f'<div><div class="mtitle">AXIOM</div>'
    f'<div class="msub">Discipline&nbsp;Intelligence &nbsp;·&nbsp; Persistent&nbsp;Memory &nbsp;·&nbsp; No&nbsp;Excuses</div>'
    f'</div></div>',
    unsafe_allow_html=True
)

# score cards
d = st.session_state.last_delta
dhtml = (f'<div class="cup">▲ +{d} pts</div>' if d>0 else
         f'<div class="cdn">▼ {d} pts</div>'   if d<0 else
         '<div class="cne">— Awaiting log</div>')
sc = st.session_state.today_status
sc_col = {"active":"#22C55E","fail":"#DC2626"}.get(sc,"#1E3050")
sc_lbl = {"active":"🟢 On Track","fail":"🔴 Slacking","empty":"⏳ Pending"}.get(sc,"⏳ Pending")

st.markdown(f"""
<div class="crow">
  <div class="card"><div class="clabel">Discipline Score</div>
    <div class="cval">{st.session_state.score}<span class="cunit"> /1000</span></div>
    <div class="cbar"><div class="cbarf" style="width:{pct}%"></div></div></div>
  <div class="card"><div class="clabel">Consistency Rate</div>
    <div class="cval">{pct}<span class="cunit">%</span></div>
    <div class="cbar"><div class="cbarf" style="width:{pct}%"></div></div></div>
  <div class="card"><div class="clabel">Today's Status</div>
    <div class="cstat" style="color:{sc_col}">{sc_lbl}</div>{dhtml}</div>
  <div class="card"><div class="clabel">Memory Bank</div>
    <div class="cstat" style="color:#38BDF8">ACTIVE</div>
    <div class="cup">Hindsight Connected</div></div>
</div>
""", unsafe_allow_html=True)
st.markdown('<hr class="div">', unsafe_allow_html=True)

# chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"]=="user" else "🛡️"):
        st.markdown(msg["content"])

# ─── INPUT ─────────────────────────────────────────────────────────────────
user_input = st.chat_input("Answer the check-in or log your progress...")

if user_input:
    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})

    mem = recall(user_input)

    # detect if this is a full day report (to trigger plan generation)
    is_day_report = any(w in user_input.lower() for w in [
        "today", "did", "done", "completed", "finished", "worked",
        "gym", "slept", "sleep", "neurokey", "vlsi", "dsp", "club"
    ])

    SYSTEM = f"""You are AXIOM — cold, strict, proactive accountability AI for Samith (ECE student, BCI project NeuroKey, IoT Club, startup Tinkercore, hostel life, gym goals).

MEMORY: {mem}

RULES:
1. Max 4 sentences. Dense, no filler.
2. **Bold** project names, key failures/wins.
3. Cite memory with date when relevant — naturally, not verbatim.
4. Progress → cold approval, push harder, ask about next task.
5. Excuse/stress → stern. Give ONE concrete action he can do right now.
6. If he hasn't mentioned a topic (gym, NeuroKey, VLSI etc.) — demand an update on it.
7. No cheerleading. No all-caps. No soft language.
8. If this looks like a full day update, end with: "Plan for tomorrow is being generated."
9. MANDATORY last line: [SCORE:+X] or [SCORE:-Y]. X=5-25, Y=10-40. Nothing after."""

    with st.chat_message("assistant", avatar="🛡️"):
        full, clean = stream_reply(SYSTEM, user_input, st.session_state.messages[:-1])

    pts = get_pts(full)
    if pts is not None:
        st.session_state.score        = max(0, min(1000, st.session_state.score + pts))
        st.session_state.today_status = "active" if pts > 0 else "fail"
        st.session_state.last_delta   = pts

    st.session_state.messages.append({"role": "assistant", "content": clean})
    save_bg(user_input, clean)

    # generate plan if full day report and no plan yet for today/tomorrow
    if is_day_report and st.session_state.plan_date != today_str:
        with st.spinner("⚙️ AXIOM is building your discipline plan for tomorrow..."):
            plan = generate_discipline_plan(user_input)
        st.session_state.discipline_plan = plan
        st.session_state.plan_date = today_str

        # summarize plan in chat
        plan_msg = "📋 **Tomorrow's Discipline Plan — Generated.**\n\n"
        type_icons = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
        for item in plan:
            hr = item["hour"]
            ampm = "AM" if hr < 12 else "PM"
            h12 = hr if hr <= 12 else hr - 12
            h12 = 12 if h12 == 0 else h12
            icon = type_icons.get(item["type"],"📌")
            plan_msg += f"**{h12}:00 {ampm}** — {icon} {item['task']}\n\n"
        plan_msg += "\n_AXIOM will notify you in this chat at each scheduled hour. Stay on the page or refresh at that time._"
        st.session_state.messages.append({"role": "assistant", "content": plan_msg})

    if pts is not None:
        st.rerun()
