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

st.set_page_config(page_title="AXIOM · Discipline AI", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")

# ─── SESSION STATE ──────────────────────────────────────────────────────────
# New users start at 500 (not 820 — that's Samith's real score)
DEFAULTS = {
    "score": 500,           # new users start at 500
    "messages": [],
    "today_status": "empty",
    "last_delta": 0,
    "discipline_plan": [],
    "plan_date": "",
    "username": "",
    "user_set": False,
    "last_fired_hour": -1,
    "last_fire_date": "",
    "is_samith": False,     # track if this is the primary user
    "onboarded": False,     # has user completed onboarding questions
    "user_goals": [],       # goals collected during onboarding
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

now       = datetime.now()
today_str = str(date.today())
pct       = round((st.session_state.score / 1000) * 100, 1)

# ─── LOGIN SCREEN ───────────────────────────────────────────────────────────
if not st.session_state.user_set:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700;900&display=swap');
    html,body,.stApp{background:#060A0F !important;font-family:'Outfit',sans-serif !important;color:#D8E6F3 !important;}
    #MainMenu,header,footer{display:none !important;}
    .block-container{padding:3rem !important;}
    .stTextInput input{background:#0D1219 !important;border:1px solid #1A2535 !important;border-radius:10px !important;color:#D8E6F3 !important;font-size:1rem !important;}
    .stButton button{background:linear-gradient(135deg,#2563EB,#1A3A80) !important;color:white !important;border:none !important;border-radius:10px !important;font-size:1rem !important;font-weight:600 !important;}
    </style>
    """, unsafe_allow_html=True)
    st.markdown("<br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("""
        <div style='text-align:center;margin-bottom:32px;'>
            <div style='font-size:3.5rem;font-weight:900;color:#EEF5FF;letter-spacing:-2px;'>AXIOM</div>
            <div style='font-size:.75rem;color:#4A6888;letter-spacing:3px;text-transform:uppercase;margin-top:6px;'>Discipline Intelligence Engine</div>
            <div style='font-size:.85rem;color:#3A5070;margin-top:12px;'>Your personal accountability AI — remembers everything.</div>
        </div>
        """, unsafe_allow_html=True)
        name = st.text_input("Enter your name to begin:", placeholder="e.g. Samith, Raj, Priya...", key="name_input")
        if st.button("Start Session →", use_container_width=True):
            if name.strip():
                uname = name.strip().lower().replace(" ", "-")
                st.session_state.username     = uname
                st.session_state.user_set     = True
                st.session_state.messages     = []
                st.session_state.discipline_plan = []
                st.session_state.plan_date    = ""
                st.session_state.last_delta   = 0
                st.session_state.today_status = "empty"
                # Samith gets his real score, new users start at 500
                st.session_state.is_samith    = "samith" in uname
                st.session_state.score        = 820 if "samith" in uname else 500
                st.session_state.onboarded    = True if "samith" in uname else False
                st.session_state.user_goals   = ["NeuroKey BCI","VLSI & DSP","Tinkercore","Gym","IoT Club"] if "samith" in uname else []
                st.rerun()
            else:
                st.error("Please enter your name.")
    st.stop()

# ─── PER-USER BANK ID (fully isolated) ──────────────────────────────────────
BANK_ID = f"axiom-{st.session_state.username}"

# ─── HELPERS ───────────────────────────────────────────────────────────────
def strip_tag(text):
    return re.sub(r'\s*\[SCORE:[+-]\d+\].*', '', text, flags=re.DOTALL).strip()

def get_pts(text):
    m = re.search(r'\[SCORE:([+-]\d+)\]', text)
    return int(m.group(1)) if m else None

def save_memory_bg(user_msg, ai_msg):
    """Save memory in background thread — never blocks UI."""
    def _go():
        try:
            ts = now.strftime("%d %b %Y %I:%M %p")
            mem_client.retain(
                bank_id=BANK_ID,
                content=f"[{ts}] {st.session_state.username}: {user_msg}. AXIOM: {ai_msg[:250]}"
            )
        except: pass
    threading.Thread(target=_go, daemon=True).start()

def recall_memory(query):
    """Recall from this user's private bank only."""
    try:
        result = mem_client.recall(bank_id=BANK_ID, query=query)
        if result and hasattr(result, 'results') and result.results:
            texts = [r.text for r in result.results[:3] if hasattr(r, 'text') and r.text]
            if texts:
                return " | ".join(texts)
    except: pass
    return "No prior records."

def generate_plan(summary, goals):
    """Generate personalized tomorrow plan based on user's actual goals."""
    goals_str = ", ".join(goals) if goals else "studies, health, personal projects"
    try:
        prompt = f"""Create a realistic discipline schedule for TOMORROW for a person whose goals are: {goals_str}.
Based on their today's report: "{summary}"
Output ONLY a valid JSON array. Each object: {{"hour":6,"task":"Wake up and stretch","type":"health"}}
Include 6-8 time slots covering their specific goals. Types: health/study/project/startup/rest.
JSON array only, no explanation, no markdown."""
        resp = groq_client.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant", temperature=0.3, max_tokens=500
        )
        raw = re.sub(r'^```json|^```|```$','',resp.choices[0].message.content.strip(),flags=re.MULTILINE).strip()
        return [{"hour":p["hour"],"task":p["task"],"type":p.get("type","study"),"fired":False}
                for p in json.loads(raw)]
    except:
        # Generic fallback — NOT Samith-specific
        return [
            {"hour":6, "task":"Wake up — no snooze",        "type":"health",  "fired":False},
            {"hour":8, "task":"Work on top priority goal",   "type":"study",   "fired":False},
            {"hour":11,"task":"Continue morning work",       "type":"study",   "fired":False},
            {"hour":14,"task":"Project work — 1 hour focus", "type":"project", "fired":False},
            {"hour":17,"task":"Exercise / gym session",      "type":"health",  "fired":False},
            {"hour":20,"task":"Evening review + planning",   "type":"study",   "fired":False},
            {"hour":22,"task":"Wind down — sleep by 11 PM",  "type":"rest",    "fired":False},
        ]

def check_scheduled():
    """Fire scheduled reminders at set hours."""
    uname_display = st.session_state.username.replace("-"," ").title()
    FIXED = {
        7:  f"🌅 **Good morning, {uname_display}!** When you're ready, let me know your plan for today.",
        13: "☀️ **Midday check-in.** Half the day is done — what have you completed so far?",
        20: "🌙 **Evening log.** How did today go? Tell me what you worked on.",
        22: f"🔔 **End of day, {uname_display}.** What did you finish today? What time are you sleeping?",
    }
    if st.session_state.last_fire_date != today_str:
        st.session_state.last_fired_hour = -1
        st.session_state.last_fire_date  = today_str
    h = now.hour
    if h in FIXED and st.session_state.last_fired_hour != h:
        st.session_state.last_fired_hour = h
        st.session_state.messages.append({"role":"assistant","content":FIXED[h]})
        return True
    for i, item in enumerate(st.session_state.discipline_plan):
        key = f"plan_{today_str}_{item['hour']}"
        if item["hour"] == h and not item["fired"] and key not in st.session_state:
            st.session_state[key] = True
            st.session_state.discipline_plan[i]["fired"] = True
            icons = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
            ic    = icons.get(item["type"],"📌")
            msg   = f"⏰ **Scheduled — {now.strftime('%I:%M %p')}**\n\n{ic} Time for: **{item['task']}**\n\nLet me know when you're done."
            st.session_state.messages.append({"role":"assistant","content":msg})
            return True
    return False

# ─── LOGOS ─────────────────────────────────────────────────────────────────
LOGO_SM = '<svg width="40" height="40" viewBox="0 0 40 40" fill="none"><defs><linearGradient id="sb1" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#2D5FA8"/><stop offset="100%" stop-color="#0B2248"/></linearGradient></defs><path d="M20 2L4 8v10.5C4 27.5 11 35 20 38 29 35 36 27.5 36 18.5V8L20 2Z" fill="url(#sb1)" stroke="#3A72C8" stroke-width="1.2"/><line x1="20" y1="11" x2="20" y2="29" stroke="#7BC8EE" stroke-width=".9" opacity=".9"/><path d="M20 11C17 11 14 13 14 16.5C13 17.5 12.5 19 13.5 21C12.5 22.5 12.5 25 14.5 26.5C15 28 17 29.2 19.2 29.2H20V11Z" fill="none" stroke="#72C8F0" stroke-width="1.3" stroke-linecap="round"/><path d="M20 11C23 11 26 13 26 16.5C27 17.5 27.5 19 26.5 21C27.5 22.5 27.5 25 25.5 26.5C25 28 23 29.2 20.8 29.2H20V11Z" fill="none" stroke="#2E6EC0" stroke-width="1.3" stroke-linecap="round"/><line x1="22" y1="15.5" x2="25" y2="15.5" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/><line x1="24" y1="15.5" x2="24" y2="19" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/><line x1="22" y1="22" x2="25.5" y2="22" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/><circle cx="22" cy="22" r="1" fill="#2E6EC0"/><circle cx="25.5" cy="22" r="1" fill="#2E6EC0"/></svg>'
LOGO_LG = '<svg width="54" height="54" viewBox="0 0 54 54" fill="none"><defs><linearGradient id="lb1" x1="0" y1="0" x2="54" y2="54" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="#2D5FA8"/><stop offset="100%" stop-color="#0B2248"/></linearGradient></defs><path d="M27 3L5 11v14C5 38.5 14.5 48.5 27 52 39.5 48.5 49 38.5 49 25V11L27 3Z" fill="url(#lb1)" stroke="#3A80D0" stroke-width="1.4"/><line x1="27" y1="15" x2="27" y2="39" stroke="#90D0F0" stroke-width="1" opacity=".9"/><path d="M27 15C23 15 19 18 19 22.5C17.5 24 17 26 18 28.5C17 30.5 17 33.5 19.5 35.5C20 37.5 22.5 39.2 25.5 39.2H27V15Z" fill="none" stroke="#78C8F0" stroke-width="1.5" stroke-linecap="round"/><path d="M19.5 24.5C21.5 23 23 24.5 21 26.5" stroke="#78C8F0" stroke-width="1.2" stroke-linecap="round"/><path d="M18.5 30.5C21 29 22.5 32.5 20 34" stroke="#78C8F0" stroke-width="1.2" stroke-linecap="round"/><path d="M27 15C31 15 35 18 35 22.5C36.5 24 37 26 36 28.5C37 30.5 37 33.5 34.5 35.5C34 37.5 31.5 39.2 28.5 39.2H27V15Z" fill="none" stroke="#2A68C0" stroke-width="1.5" stroke-linecap="round"/><line x1="29.5" y1="20" x2="33.5" y2="20" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/><line x1="32.5" y1="20" x2="32.5" y2="25" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/><line x1="29.5" y1="29.5" x2="34" y2="29.5" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/><circle cx="29.5" cy="29.5" r="1.3" fill="#2A68C0"/><circle cx="34" cy="29.5" r="1.3" fill="#2A68C0"/></svg>'

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
.user-badge{font-family:'DM Mono',monospace;font-size:.65rem;color:#38BDF8;background:rgba(56,189,248,.08);border:1px solid rgba(56,189,248,.2);border-radius:99px;padding:3px 10px;margin-bottom:10px;display:inline-block;}
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

# ─── BOOT: First message logic ──────────────────────────────────────────────
uname_display = st.session_state.username.replace("-"," ").title()

if not st.session_state.messages:
    if st.session_state.is_samith:
        # Samith gets his full personalised check-in
        boot_msg = (
            f"Welcome back, **{uname_display}**! 👋\n\n"
            "Ready for today's accountability check. Give me your update:\n\n"
            "1. 🧠 **NeuroKey** — Progress today?\n"
            "2. 📡 **VLSI / DSP** — Coursework done?\n"
            "3. 💪 **Gym** — Did you go? Diet on track?\n"
            "4. 🚀 **Tinkercore / IoT Club** — Any tasks completed?\n"
            "5. 😴 **Sleep** — How many hours last night?\n\n"
            "Or just say good morning — we can start from there."
        )
    else:
        # New user — AXIOM introduces itself and starts collecting their info
        boot_msg = (
            f"Hey **{uname_display}**! 👋 I'm **AXIOM** — your personal discipline AI.\n\n"
            "I remember everything you tell me across sessions, track your progress, "
            "and hold you accountable to your goals over time.\n\n"
            "To get started, tell me a bit about yourself:\n\n"
            "1. 🎯 **What are you currently working on?** (studies, job, projects, startup?)\n"
            "2. 💪 **What health goals do you have?** (gym, diet, sleep?)\n"
            "3. 📅 **What's your biggest challenge right now?**\n\n"
            "The more you tell me, the better I can help you stay on track. Go ahead!"
        )
    st.session_state.messages.append({"role":"assistant","content":boot_msg})

if check_scheduled():
    st.rerun()

# ─── SIDEBAR ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        f'<div class="brand">{LOGO_SM}<span class="bname">AXIOM</span></div>'
        f'<div class="btag">Discipline Intelligence Engine</div>'
        f'<div class="user-badge">👤 {uname_display}</div>'
        f'<div class="pill pg"><span class="dot dg"></span>Hindsight Memory: LIVE</div>'
        f'<div class="pill pb"><span class="dot db"></span>Groq LLM: CONNECTED</div>',
        unsafe_allow_html=True
    )

    # Heatmap — only Samith gets fake historical data, others start all empty
    st.markdown('<div class="sec">30-Day Activity</div>', unsafe_allow_html=True)
    if st.session_state.is_samith:
        past = ["ha","ha","hf","ha","ha","ha","he","ha","hf","ha","ha","ha",
                "ha","hf","ha","ha","ha","he","ha","ha","ha","ha","ha","hf",
                "ha","ha","he","ha","ha"]
    else:
        # New users — all empty, only today's box is dynamic
        past = ["he"] * 29

    tc = {"empty":"he ht","active":"ha ht","fail":"hf ht"}.get(st.session_state.today_status,"he ht")
    boxes = "".join(f'<span class="hm {c}"></span>' for c in past)
    boxes += f'<span class="hm {tc}" title="Today"></span>'
    st.markdown(
        f'<div class="hmap">{boxes}</div>'
        f'<div class="hleg">'
        f'<span><span class="hd" style="background:#2563EB"></span>On Track</span>'
        f'<span><span class="hd" style="background:#DC2626"></span>Excused</span>'
        f'<span><span class="hd" style="background:#0D1520;border:1px solid #182030"></span>Rest</span>'
        f'</div>', unsafe_allow_html=True
    )

    # Goals — Samith's specific goals vs dynamic goals for others
    st.markdown('<div class="sec">Active Targets</div>', unsafe_allow_html=True)
    if st.session_state.is_samith:
        goals_display = [
            ("🧠","NeuroKey BCI",    "EEG + Python Capstone"),
            ("📡","VLSI & DSP",      "VTU Coursework"),
            ("🚀","Tinkercore",      "Startup · ECE Outreach"),
            ("💪","Gym & Nutrition", "Hostel Diet · Strength"),
            ("🔩","IoT Club",        "Leadership · Workshops"),
        ]
    elif st.session_state.user_goals:
        # Goals collected during onboarding
        icons_map = ["🎯","📚","💪","🚀","😴","🔩","⚡"]
        goals_display = [(icons_map[i % len(icons_map)], g, "Your goal") for i,g in enumerate(st.session_state.user_goals)]
    else:
        goals_display = [
            ("🎯","Your Goals",  "Tell AXIOM to get started"),
            ("📚","Studies",     "Coursework & learning"),
            ("💪","Health",      "Gym, diet & sleep"),
            ("🚀","Projects",    "Work & side projects"),
            ("😴","Recovery",    "Rest & mental health"),
        ]

    for em, t, s in goals_display:
        st.markdown(
            f'<div class="gc"><span class="ge">{em}</span>'
            f'<div><span class="gt">{t}</span><span class="gs">{s}</span></div></div>',
            unsafe_allow_html=True
        )

    # Tomorrow's plan
    if st.session_state.discipline_plan:
        st.markdown("<div class='sec'>📋 Tomorrow's Plan</div>", unsafe_allow_html=True)
        icons = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
        for item in st.session_state.discipline_plan:
            h  = item["hour"]
            ap = "AM" if h < 12 else "PM"
            h12 = h if h <= 12 else h - 12
            h12 = 12 if h12 == 0 else h12
            ic = icons.get(item["type"],"📌")
            dc = "plan-done" if item["fired"] else ""
            st.markdown(
                f'<div class="plan-item">'
                f'<span class="plan-time">{h12}{ap}</span>'
                f'<span class="plan-task {dc}">{ic} {item["task"]}</span>'
                f'</div>', unsafe_allow_html=True
            )

    st.markdown('<div class="sec">Session</div>', unsafe_allow_html=True)
    if st.button("Switch User", use_container_width=True):
        # Reset ALL state cleanly
        for k in list(DEFAULTS.keys()):
            st.session_state[k] = DEFAULTS[k]
        st.rerun()

# ─── MAIN AREA ─────────────────────────────────────────────────────────────
st.markdown(
    f'<div class="mhdr">{LOGO_LG}'
    f'<div><div class="mtitle">AXIOM</div>'
    f'<div class="msub">Discipline Intelligence &nbsp;·&nbsp; Persistent Memory &nbsp;·&nbsp; No Excuses</div>'
    f'</div></div>',
    unsafe_allow_html=True
)

d     = st.session_state.last_delta
dhtml = (f'<div class="cup">▲ +{d} pts</div>' if d > 0 else
         f'<div class="cdn">▼ {d} pts</div>'   if d < 0 else
         '<div class="cne">— Awaiting log</div>')
sc     = st.session_state.today_status
sc_col = {"active":"#22C55E","fail":"#DC2626"}.get(sc,"#1E3050")
sc_lbl = {"active":"🟢 On Track","fail":"🔴 Off Track","empty":"⏳ Pending"}.get(sc,"⏳ Pending")

st.markdown(f"""<div class="crow">
  <div class="card">
    <div class="clabel">Discipline Score</div>
    <div class="cval">{st.session_state.score}<span class="cunit"> /1000</span></div>
    <div class="cbar"><div class="cbarf" style="width:{pct}%"></div></div>
  </div>
  <div class="card">
    <div class="clabel">Consistency Rate</div>
    <div class="cval">{pct}<span class="cunit">%</span></div>
    <div class="cbar"><div class="cbarf" style="width:{pct}%"></div></div>
  </div>
  <div class="card">
    <div class="clabel">Today's Status</div>
    <div class="cstat" style="color:{sc_col}">{sc_lbl}</div>
    {dhtml}
  </div>
  <div class="card">
    <div class="clabel">Memory Bank</div>
    <div class="cstat" style="color:#38BDF8">ACTIVE</div>
    <div class="cup">Hindsight · {BANK_ID}</div>
  </div>
</div>""", unsafe_allow_html=True)

st.markdown('<hr class="div">', unsafe_allow_html=True)

# Chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"]=="user" else "🛡️"):
        st.markdown(msg["content"])

# ─── INPUT ─────────────────────────────────────────────────────────────────
user_input = st.chat_input("Tell me about yourself, or log your progress...")

if user_input:
    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)
    st.session_state.messages.append({"role":"user","content":user_input})

    # Recall from this user's private memory bank
    mem = recall_memory(user_input)

    # Build user profile — specific for Samith, dynamic for others
    if st.session_state.is_samith:
        user_profile = (
            "- ECE student, Batch 2023-2027, East Point College Bengaluru\n"
            "- Subjects: VLSI Design, Digital Signal Processing\n"
            "- Capstone: NeuroKey (BCI using EEG + Python)\n"
            "- Leads IoT Club, building startup Tinkercore, rides Royal Enfield Bullet\n"
            "- Hostel life, gym goals, diet constraints"
        )
        goals_context = "NeuroKey BCI, VLSI, DSP, Tinkercore, IoT Club, gym"
    else:
        goals_context = ", ".join(st.session_state.user_goals) if st.session_state.user_goals else "not yet collected — learn from conversation"
        user_profile  = (
            f"- New user — currently learning their goals from conversation\n"
            f"- Known goals so far: {goals_context}\n"
            f"- Keep asking questions to understand their work, studies, health, projects"
        )

    SYSTEM = f"""You are AXIOM — a warm but firm accountability AI mentor for {uname_display}.

USER PROFILE:
- Name: {uname_display}
{user_profile}

HINDSIGHT MEMORY (this user's private history): {mem}

BEHAVIOR RULES:
1. Greet naturally for casual messages ("hi", "good morning" etc). Don't interrogate immediately.
2. For NEW users who haven't shared goals yet: ask ONE question at a time to learn about them.
   Ask about: what they're studying/working on, their health goals, their biggest challenge.
   Once you learn their goals, confirm them and say you'll track these from now on.
3. For users with known goals: check in on those specific goals. Reference memory with dates.
4. Be respectful and encouraging for progress. Be firm but not harsh for excuses.
5. Use their name occasionally.
6. Max 4 sentences unless giving a full plan.
7. **Bold** key project names, wins, failures.
8. If memory has relevant past data, cite it naturally with the date.
9. If they give a full day update → end with "I'll build your plan for tomorrow."
10. SCORING — mandatory when there is actual progress/excuse content:
    Progress/discipline → [SCORE:+X] where X is 5-25
    Excuses/laziness   → [SCORE:-Y] where Y is 10-30
    Pure greeting with no update → skip scoring entirely, do NOT add [SCORE:+0]"""

    with st.chat_message("assistant", avatar="🛡️"):
        ph   = st.empty()
        full = ""
        try:
            msgs = [{"role":"system","content":SYSTEM}]
            for m in st.session_state.messages[-8:-1]:
                msgs.append({"role":m["role"],"content":m["content"]})
            msgs.append({"role":"user","content":user_input})

            stream = groq_client.chat.completions.create(
                messages=msgs,
                model="llama-3.1-8b-instant",
                temperature=0.6, max_tokens=300, stream=True,
            )
            for chunk in stream:
                tok = chunk.choices[0].delta.content
                if tok:
                    full += tok
                    ph.markdown(strip_tag(full) + " ▌")
            clean = strip_tag(full)
            ph.markdown(clean)
        except Exception as e:
            clean = f"Connection error — {e}"
            ph.error(clean)
            full  = clean

    # Update score
    pts = get_pts(full)
    if pts is not None and pts != 0:
        st.session_state.score        = max(0, min(1000, st.session_state.score + pts))
        st.session_state.today_status = "active" if pts > 0 else "fail"
        st.session_state.last_delta   = pts

    clean = strip_tag(full)
    st.session_state.messages.append({"role":"assistant","content":clean})

    # Save to Hindsight memory (background, non-blocking)
    save_memory_bg(user_input, clean)

    # Extract goals from new user's messages and store them
    if not st.session_state.is_samith and not st.session_state.onboarded:
        goal_keywords = ["study","studying","working on","project","startup","gym","fitness",
                         "course","college","job","internship","coding","engineering","design"]
        if any(w in user_input.lower() for w in goal_keywords):
            # Ask Groq to extract goals from what the user said
            try:
                goal_resp = groq_client.chat.completions.create(
                    messages=[{"role":"user","content":
                        f"Extract 2-4 short goal labels from this text: '{user_input}'. "
                        f"Return ONLY a JSON array of short strings. Example: [\"DSP studies\",\"gym\",\"startup\"]. "
                        f"Nothing else."}],
                    model="llama-3.1-8b-instant", temperature=0.1, max_tokens=100
                )
                raw_goals = re.sub(r'^```json|^```|```$','',goal_resp.choices[0].message.content.strip(),flags=re.MULTILINE).strip()
                extracted = json.loads(raw_goals)
                if isinstance(extracted, list) and extracted:
                    existing = st.session_state.user_goals
                    new_goals = [g for g in extracted if g not in existing]
                    st.session_state.user_goals = (existing + new_goals)[:6]
            except: pass

    # Generate discipline plan after a full day report
    is_report = any(w in user_input.lower() for w in [
        "today","did","done","gym","slept","sleep","finished","worked",
        "completed","skipped","missed","studied","worked out"
    ])
    if is_report and st.session_state.plan_date != today_str:
        with st.spinner("⚙️ Building your discipline plan for tomorrow..."):
            plan = generate_plan(user_input, st.session_state.user_goals)
        st.session_state.discipline_plan = plan
        st.session_state.plan_date       = today_str
        icons     = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
        plan_msg  = f"📋 **{uname_display}'s Discipline Plan for Tomorrow**\n\n"
        for item in plan:
            h   = item["hour"]
            ap  = "AM" if h < 12 else "PM"
            h12 = h if h <= 12 else h - 12
            h12 = 12 if h12 == 0 else h12
            plan_msg += f"**{h12}:00 {ap}** — {icons.get(item['type'],'📌')} {item['task']}\n\n"
        plan_msg += "_I'll remind you at each scheduled time when the app is open. Stay consistent._"
        st.session_state.messages.append({"role":"assistant","content":plan_msg})

    if pts is not None and pts != 0:
        st.rerun()
