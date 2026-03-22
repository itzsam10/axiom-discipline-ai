import streamlit as st
import re
import threading
import json
from groq import Groq
from hindsight_client import Hindsight
from datetime import datetime, date

# ─── KEYS ──────────────────────────────────────────────────────────────────
GROQ_API_KEY      = st.secrets["GROQ_API_KEY"]
HINDSIGHT_API_KEY = st.secrets["HINDSIGHT_API_KEY"]
HINDSIGHT_URL     = st.secrets.get("HINDSIGHT_URL", "https://api.hindsight.vectorize.io")

groq_client = Groq(api_key=GROQ_API_KEY)
mem_client  = Hindsight(base_url=HINDSIGHT_URL, api_key=HINDSIGHT_API_KEY)
BANK_ID     = "discipline-bot"

st.set_page_config(page_title="AXIOM · Discipline AI", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")

for k, v in {"score":820,"messages":[],"today_status":"empty","last_delta":0,"discipline_plan":[],"plan_date":""}.items():
    if k not in st.session_state: st.session_state[k] = v

pct = round((st.session_state.score/1000)*100,1)
now = datetime.now()
today_str = str(date.today())

# ─── HELPERS ───────────────────────────────────────────────────────────────
def strip_tag(text):
    return re.sub(r'\s*\[SCORE:[+-]\d+\].*','',text,flags=re.DOTALL).strip()

def get_pts(text):
    m = re.search(r'\[SCORE:([+-]\d+)\]',text)
    return int(m.group(1)) if m else None

def save_memory_bg(user_msg, ai_msg):
    def _go():
        try:
            ts = now.strftime("%d %b %Y %I:%M %p")
            mem_client.retain(
                bank_id=BANK_ID,
                content=f"[{ts}] Samith said: {user_msg}. AXIOM replied: {ai_msg[:200]}"
            )
        except: pass
    threading.Thread(target=_go, daemon=True).start()

def recall_memory(query):
    try:
        result = mem_client.recall(bank_id=BANK_ID, query=query)
        if result and hasattr(result, 'results') and result.results:
            return " | ".join(r.text for r in result.results[:3] if hasattr(r,'text'))
    except: pass
    return "No prior records."

def generate_plan(summary):
    try:
        prompt = f"""Create a tomorrow discipline schedule for this ECE student based on: "{summary}"
Output ONLY a JSON array. Each item: {{"hour":6,"task":"Wake up and stretch","type":"health"}}
Include 6-8 slots. Types: health/study/project/startup/rest. Output only the JSON array."""
        resp = groq_client.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant", temperature=0.3, max_tokens=400
        )
        raw = re.sub(r'^```json|^```|```$','',resp.choices[0].message.content.strip(),flags=re.MULTILINE).strip()
        return [{"hour":p["hour"],"task":p["task"],"type":p.get("type","study"),"fired":False} for p in json.loads(raw)]
    except:
        return [{"hour":6,"task":"Wake up — no snooze","type":"health","fired":False},
                {"hour":8,"task":"VLSI Design — 1 chapter","type":"study","fired":False},
                {"hour":11,"task":"DSP revision or assignment","type":"study","fired":False},
                {"hour":14,"task":"NeuroKey — 1 hour coding","type":"project","fired":False},
                {"hour":17,"task":"Gym session","type":"health","fired":False},
                {"hour":19,"task":"Tinkercore / IoT Club tasks","type":"startup","fired":False},
                {"hour":22,"task":"Wind down — sleep by 11 PM","type":"rest","fired":False}]

def check_scheduled():
    FIXED = {
        7:  "🌅 **Morning Check-In** — 7 AM. State your top 3 targets: **NeuroKey**, **VLSI/DSP**, **gym**.",
        13: "☀️ **Midday Pulse** — What have you completed? On track with morning targets?",
        20: "🌙 **Evening Log** — Report: NeuroKey, VLSI/DSP, gym, Tinkercore. I'm scoring this.",
        22: "🔔 **Final Check** — Confirm tasks done, food eaten, sleep time set.",
    }
    if st.session_state.get("last_fire_date") != today_str:
        st.session_state["last_fired_hour"] = -1
        st.session_state["last_fire_date"] = today_str
    h = now.hour
    if h in FIXED and st.session_state.get("last_fired_hour") != h:
        st.session_state["last_fired_hour"] = h
        st.session_state.messages.append({"role":"assistant","content":FIXED[h]})
        return True
    for i,item in enumerate(st.session_state.discipline_plan):
        key = f"plan_{today_str}_{item['hour']}"
        if item["hour"]==h and not item["fired"] and key not in st.session_state:
            st.session_state[key] = True
            st.session_state.discipline_plan[i]["fired"] = True
            icons = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
            ic = icons.get(item["type"],"📌")
            msg = f"⏰ **Scheduled — {now.strftime('%I:%M %p')}**\n\n{ic} Time for: **{item['task']}**\n\nReport back when done."
            st.session_state.messages.append({"role":"assistant","content":msg})
            return True
    return False

# ─── SVG LOGOS ─────────────────────────────────────────────────────────────
LOGO_SM = '<svg width="40" height="40" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="sb1" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#2D5FA8"/><stop offset="100%" stop-color="#0B2248"/></linearGradient></defs><path d="M20 2L4 8v10.5C4 27.5 11 35 20 38 29 35 36 27.5 36 18.5V8L20 2Z" fill="url(#sb1)" stroke="#3A72C8" stroke-width="1.2"/><path d="M20 2L4 8v10.5C4 27.5 11 35 20 38V2Z" fill="white" opacity=".05"/><line x1="20" y1="11" x2="20" y2="29" stroke="#7BC8EE" stroke-width=".9" opacity=".9"/><path d="M20 11C17 11 14 13 14 16.5C13 17.5 12.5 19 13.5 21C12.5 22.5 12.5 25 14.5 26.5C15 28 17 29.2 19.2 29.2H20V11Z" fill="none" stroke="#72C8F0" stroke-width="1.3" stroke-linecap="round"/><path d="M14.5 18C16 17 17 18 15.5 19.5" stroke="#72C8F0" stroke-width="1" stroke-linecap="round"/><path d="M13.5 22.5C15.5 21.5 16.5 24 14.5 25" stroke="#72C8F0" stroke-width="1" stroke-linecap="round"/><path d="M20 11C23 11 26 13 26 16.5C27 17.5 27.5 19 26.5 21C27.5 22.5 27.5 25 25.5 26.5C25 28 23 29.2 20.8 29.2H20V11Z" fill="none" stroke="#2E6EC0" stroke-width="1.3" stroke-linecap="round"/><line x1="22" y1="15.5" x2="25" y2="15.5" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/><line x1="24" y1="15.5" x2="24" y2="19" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/><line x1="22" y1="22" x2="25.5" y2="22" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/><circle cx="22" cy="22" r="1" fill="#2E6EC0"/><circle cx="25.5" cy="22" r="1" fill="#2E6EC0"/></svg>'

LOGO_LG = '<svg width="54" height="54" viewBox="0 0 54 54" fill="none" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="lb1" x1="0" y1="0" x2="54" y2="54" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#2D5FA8"/><stop offset="100%" stop-color="#0B2248"/></linearGradient></defs><path d="M27 3L5 11v14C5 38.5 14.5 48.5 27 52 39.5 48.5 49 38.5 49 25V11L27 3Z" fill="url(#lb1)" stroke="#3A80D0" stroke-width="1.4"/><path d="M27 3L5 11v14C5 38.5 14.5 48.5 27 52V3Z" fill="white" opacity=".05"/><line x1="27" y1="15" x2="27" y2="39" stroke="#90D0F0" stroke-width="1" opacity=".9"/><path d="M27 15C23 15 19 18 19 22.5C17.5 24 17 26 18 28.5C17 30.5 17 33.5 19.5 35.5C20 37.5 22.5 39.2 25.5 39.2H27V15Z" fill="none" stroke="#78C8F0" stroke-width="1.5" stroke-linecap="round"/><path d="M19.5 24.5C21.5 23 23 24.5 21 26.5" stroke="#78C8F0" stroke-width="1.2" stroke-linecap="round"/><path d="M18.5 30.5C21 29 22.5 32.5 20 34" stroke="#78C8F0" stroke-width="1.2" stroke-linecap="round"/><path d="M27 15C31 15 35 18 35 22.5C36.5 24 37 26 36 28.5C37 30.5 37 33.5 34.5 35.5C34 37.5 31.5 39.2 28.5 39.2H27V15Z" fill="none" stroke="#2A68C0" stroke-width="1.5" stroke-linecap="round"/><line x1="29.5" y1="20" x2="33.5" y2="20" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/><line x1="32.5" y1="20" x2="32.5" y2="25" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/><line x1="29.5" y1="29.5" x2="34" y2="29.5" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/><circle cx="29.5" cy="29.5" r="1.3" fill="#2A68C0"/><circle cx="34" cy="29.5" r="1.3" fill="#2A68C0"/></svg>'

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
.plan-item{display:flex;align-items:flex-start;gap:8px;padding:6px 8px;margin-bottom:3px;border-radius:7px;background:#0C1320;border:1px solid #131D2B;}
.plan-time{font-family:'DM Mono',monospace;font-size:.62rem;color:#38BDF8;flex-shrink:0;min-width:34px;padding-top:1px;}
.plan-task{font-size:.74rem;color:#A8C0D8;line-height:1.3;}
.plan-done{opacity:.4;text-decoration:line-through;}
.mhdr{display:flex;align-items:center;gap:14px;margin-bottom:16px;}
.mtitle{font-weight:900;font-size:2.6rem;color:#EEF5FF;letter-spacing:-2px;line-height:1;}
.msub{font-family:'DM Mono',monospace;font-size:.65rem;color:#5A85A8;letter-spacing:2.5px;text-transform:uppercase;margin-top:5px;font-weight:500;}
.crow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:22px;}
.card{background:#080D14;border:1px solid #131D2B;border-radius:13px;padding:15px 17px;position:relative;overflow:hidden;}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,#38BDF8,#2563EB);opacity:.55;}
.clabel{font-family:'DM Mono',monospace;font-size:.72rem;color:#4A6888;letter-spacing:1px;text-transform:uppercase;margin-bottom:9px;}
.cval{font-weight:800;font-size:2rem;color:#EEF5FF;line-height:1;letter-spacing:-1px;}
.cunit{font-size:.9rem;font-weight:400;color:#2C4060;}
.cbar{height:3px;background:#111A26;border-radius:99px;margin-top:9px;overflow:hidden;}
.cbarf{height:100%;border-radius:99px;background:linear-gradient(90deg,#38BDF8,#2563EB);}
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

# ─── BOOT CHECK-IN ─────────────────────────────────────────────────────────
if not st.session_state.messages:
    st.session_state.messages.append({"role":"assistant","content":(
        "**Daily Discipline Check-In — AXIOM is logging your progress.**\n\n"
        "Answer each one directly:\n\n"
        "1. 🧠 **NeuroKey** — What did you work on today? Any blockers?\n"
        "2. 📡 **VLSI / DSP** — Coursework completed? Assignments pending?\n"
        "3. 💪 **Gym** — Did you hit the gym? Followed the hostel diet?\n"
        "4. 🚀 **Tinkercore / IoT Club** — Any updates, tasks done?\n"
        "5. 😴 **Sleep** — Hours slept last night?\n\n"
        "Reply with your updates. I will score you, build your plan for tomorrow, and hold you accountable."
    )})

if check_scheduled(): st.rerun()

# ─── SIDEBAR ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f'<div class="brand">{LOGO_SM}<span class="bname">AXIOM</span></div><div class="btag">Discipline Intelligence Engine</div><div class="pill pg"><span class="dot dg"></span>Hindsight Memory: LIVE</div><div class="pill pb"><span class="dot db"></span>Groq LLM: CONNECTED</div>', unsafe_allow_html=True)
    st.markdown('<div class="sec">30-Day Activity</div>', unsafe_allow_html=True)
    past=["ha","ha","hf","ha","ha","ha","he","ha","hf","ha","ha","ha","ha","hf","ha","ha","ha","he","ha","ha","ha","ha","ha","hf","ha","ha","he","ha","ha"]
    tc={"empty":"he ht","active":"ha ht","fail":"hf ht"}.get(st.session_state.today_status,"he ht")
    boxes="".join(f'<span class="hm {c}"></span>' for c in past)+f'<span class="hm {tc}" title="Today"></span>'
    st.markdown(f'<div class="hmap">{boxes}</div><div class="hleg"><span><span class="hd" style="background:#2563EB"></span>On Track</span><span><span class="hd" style="background:#DC2626"></span>Excused</span><span><span class="hd" style="background:#0D1520;border:1px solid #182030"></span>Rest</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="sec">Active Targets</div>', unsafe_allow_html=True)
    for em,t,s in [("🧠","NeuroKey BCI","EEG + Python Capstone"),("📡","VLSI & DSP","VTU Coursework"),("🚀","Tinkercore","Startup · ECE Outreach"),("💪","Gym & Nutrition","Hostel Diet · Strength"),("🔩","IoT Club","Leadership · Workshops")]:
        st.markdown(f'<div class="gc"><span class="ge">{em}</span><div><span class="gt">{t}</span><span class="gs">{s}</span></div></div>', unsafe_allow_html=True)
    if st.session_state.discipline_plan:
        st.markdown("<div class='sec'>📋 Tomorrow's Plan</div>", unsafe_allow_html=True)
        icons={"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
        for item in st.session_state.discipline_plan:
            h=item["hour"]; ap="AM" if h<12 else "PM"; h12=h if h<=12 else h-12; h12=12 if h12==0 else h12
            ic=icons.get(item["type"],"📌"); dc="plan-done" if item["fired"] else ""
            st.markdown(f'<div class="plan-item"><span class="plan-time">{h12}{ap}</span><span class="plan-task {dc}">{ic} {item["task"]}</span></div>', unsafe_allow_html=True)

# ─── MAIN ──────────────────────────────────────────────────────────────────
st.markdown(f'<div class="mhdr">{LOGO_LG}<div><div class="mtitle">AXIOM</div><div class="msub">Discipline Intelligence &nbsp;·&nbsp; Persistent Memory &nbsp;·&nbsp; No Excuses</div></div></div>', unsafe_allow_html=True)

d=st.session_state.last_delta
dhtml=f'<div class="cup">▲ +{d} pts</div>' if d>0 else (f'<div class="cdn">▼ {d} pts</div>' if d<0 else '<div class="cne">— Awaiting log</div>')
sc=st.session_state.today_status
sc_col={"active":"#22C55E","fail":"#DC2626"}.get(sc,"#1E3050")
sc_lbl={"active":"🟢 On Track","fail":"🔴 Slacking","empty":"⏳ Pending"}.get(sc,"⏳ Pending")

st.markdown(f"""<div class="crow">
<div class="card"><div class="clabel">Discipline Score</div><div class="cval">{st.session_state.score}<span class="cunit"> /1000</span></div><div class="cbar"><div class="cbarf" style="width:{pct}%"></div></div></div>
<div class="card"><div class="clabel">Consistency Rate</div><div class="cval">{pct}<span class="cunit">%</span></div><div class="cbar"><div class="cbarf" style="width:{pct}%"></div></div></div>
<div class="card"><div class="clabel">Today's Status</div><div class="cstat" style="color:{sc_col}">{sc_lbl}</div>{dhtml}</div>
<div class="card"><div class="clabel">Memory Bank</div><div class="cstat" style="color:#38BDF8">ACTIVE</div><div class="cup">Hindsight Connected</div></div>
</div>""", unsafe_allow_html=True)
st.markdown('<hr class="div">', unsafe_allow_html=True)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"]=="user" else "🛡️"):
        st.markdown(msg["content"])

# ─── INPUT ─────────────────────────────────────────────────────────────────
user_input = st.chat_input("Log your progress, or confess your excuses...")

if user_input:
    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)
    st.session_state.messages.append({"role":"user","content":user_input})

    # Recall from Hindsight
    mem = recall_memory(user_input)

    SYSTEM = f"""You are AXIOM — strict, cold, proactive accountability AI for Samith.
Samith: ECE student 2023-2027, East Point College Bengaluru. VLSI, DSP coursework. 
Capstone: NeuroKey (BCI, EEG+Python). IoT Club leader. Startup Tinkercore. Hostel life, gym goals.

HINDSIGHT MEMORY (past sessions): {mem}

RULES:
1. Max 4 sentences. Dense, no filler.
2. **Bold** project names, failures, wins.
3. Cite Hindsight memory with date when relevant.
4. Progress → cold approval, push harder. Excuse → stern, give ONE action now.
5. Always ask about a missing topic (gym/NeuroKey/VLSI if not mentioned).
6. No cheerleading. No all-caps. Speak like a strict mentor.
7. MANDATORY last line: [SCORE:+X] or [SCORE:-Y]. X=5-25, Y=10-40. Nothing after."""

    with st.chat_message("assistant", avatar="🛡️"):
        ph = st.empty()
        full = ""
        try:
            stream = groq_client.chat.completions.create(
                messages=[{"role":"system","content":SYSTEM}]+
                         [{"role":m["role"],"content":m["content"]} for m in st.session_state.messages[-6:-1]]+
                         [{"role":"user","content":user_input}],
                model="llama-3.1-8b-instant", temperature=0.55, max_tokens=280, stream=True,
            )
            for chunk in stream:
                tok = chunk.choices[0].delta.content
                if tok:
                    full += tok
                    ph.markdown(strip_tag(full)+" ▌")
            clean = strip_tag(full)
            ph.markdown(clean)
        except Exception as e:
            clean = f"Error — {e}"
            ph.error(clean)
            full = clean

    pts = get_pts(full)
    if pts:
        st.session_state.score = max(0,min(1000,st.session_state.score+pts))
        st.session_state.today_status = "active" if pts>0 else "fail"
        st.session_state.last_delta = pts

    clean = strip_tag(full)
    st.session_state.messages.append({"role":"assistant","content":clean})
    save_memory_bg(user_input, clean)

    is_report = any(w in user_input.lower() for w in ["today","did","done","gym","slept","sleep","neurokey","vlsi","dsp","club","finished","worked"])
    if is_report and st.session_state.plan_date != today_str:
        with st.spinner("⚙️ Building your discipline plan for tomorrow..."):
            plan = generate_plan(user_input)
        st.session_state.discipline_plan = plan
        st.session_state.plan_date = today_str
        icons={"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
        plan_msg = "📋 **Tomorrow's Discipline Plan — Generated by AXIOM.**\n\n"
        for item in plan:
            h=item["hour"]; ap="AM" if h<12 else "PM"; h12=h if h<=12 else h-12; h12=12 if h12==0 else h12
            plan_msg += f"**{h12}:00 {ap}** — {icons.get(item['type'],'📌')} {item['task']}\n\n"
        plan_msg += "_AXIOM will remind you at each scheduled time. Refresh the page to check._"
        st.session_state.messages.append({"role":"assistant","content":plan_msg})

    if pts: st.rerun()
