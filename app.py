import streamlit as st
import re
import threading
import json
from groq import Groq
from hindsight_client import Hindsight
from datetime import datetime, date, timedelta

# ── API KEYS ────────────────────────────────────────────────────────────────
GROQ_API_KEY      = st.secrets["GROQ_API_KEY"]
HINDSIGHT_API_KEY = st.secrets["HINDSIGHT_API_KEY"]
HINDSIGHT_URL     = st.secrets.get("HINDSIGHT_URL", "https://api.hindsight.vectorize.io")

groq  = Groq(api_key=GROQ_API_KEY)
hmem  = Hindsight(base_url=HINDSIGHT_URL, api_key=HINDSIGHT_API_KEY)

st.set_page_config(
    page_title="AXIOM · Discipline AI",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

now       = datetime.now()
today_str = str(date.today())

# ── SESSION DEFAULTS ────────────────────────────────────────────────────────
SD = {
    "username":      "",
    "display_name":  "",
    "user_set":      False,
    "profile_loaded":False,
    "is_new_user":   True,
    "onboard_step":  0,       # 0=not started, 1=collecting, 2=done
    "score":         0,
    "messages":      [],
    "today_status":  "empty",
    "last_delta":    0,
    "goals":         [],      # list of {icon, label, detail}
    "heatmap":       [],      # list of {date_str, status}  max 30
    "plan":          [],      # tomorrow's plan items
    "plan_date":     "",
    "streak":        0,
    "total_sessions":0,
    "last_fired_hour":-1,
    "last_fire_date":"",
    "raw_goals_text":"",      # raw text from onboarding for context
    "last_session_messages":[],  # previous session chat restored on login
    "last_session_date":"",      # when last session happened
    "last_session_summary":"",   # short summary of last session
}
for k, v in SD.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── HELPERS ─────────────────────────────────────────────────────────────────

def bank_id():
    return f"axiom-{st.session_state.username}"

def strip_score(text: str) -> str:
    return re.sub(r'\s*\[SCORE:[+-]\d+\].*', '', text, flags=re.DOTALL).strip()

def parse_score(text: str):
    m = re.search(r'\[SCORE:([+-]\d+)\]', text)
    return int(m.group(1)) if m else None

def save_memory(user_msg: str, ai_msg: str):
    def _go():
        try:
            ts = now.strftime("%d %b %Y %I:%M %p")
            hmem.retain(
                bank_id=bank_id(),
                content=f"[{ts}] {st.session_state.display_name}: {user_msg} | AXIOM: {ai_msg[:300]}"
            )
        except: pass
    threading.Thread(target=_go, daemon=True).start()

def recall(query: str) -> str:
    try:
        r = hmem.recall(bank_id=bank_id(), query=query)
        if r and hasattr(r, 'results') and r.results:
            texts = [x.text for x in r.results[:4]
                     if hasattr(x,'text') and x.text and '[PROFILE]' not in x.text]
            return " | ".join(texts[:3]) if texts else "No prior records."
    except: pass
    return "No prior records."

def save_profile():
    """Persist user profile to Hindsight so it survives page refresh."""
    def _go():
        try:
            # Save last 20 messages so they restore on next login
            msgs_to_save = st.session_state.messages[-20:] if st.session_state.messages else []
            profile = {
                "score":         st.session_state.score,
                "goals":         st.session_state.goals,
                "heatmap":       st.session_state.heatmap,
                "streak":        st.session_state.streak,
                "total_sessions":st.session_state.total_sessions,
                "onboard_step":  st.session_state.onboard_step,
                "today_status":  st.session_state.today_status,
                "last_updated":  today_str,
                "raw_goals_text":st.session_state.raw_goals_text,
                "last_session_messages": msgs_to_save,
                "last_session_date": today_str,
                "last_session_summary": st.session_state.last_session_summary,
                "plan":          st.session_state.plan,
                "plan_date":     st.session_state.plan_date,
            }
            hmem.retain(
                bank_id=bank_id(),
                content=f"[PROFILE] {json.dumps(profile)}",
                context="profile"
            )
        except: pass
    threading.Thread(target=_go, daemon=True).start()

def load_profile() -> dict | None:
    try:
        r = hmem.recall(bank_id=bank_id(), query="PROFILE score goals heatmap streak")
        if r and hasattr(r, 'results') and r.results:
            for x in r.results:
                txt = getattr(x, 'text', '')
                if '[PROFILE]' in txt:
                    m = re.search(r'\{.*\}', txt, re.DOTALL)
                    if m:
                        return json.loads(m.group())
    except: pass
    return None

def get_heatmap_today_class():
    s = st.session_state.today_status
    return {"active":"ha","fail":"hf","empty":"he"}.get(s,"he")

def update_heatmap(status: str):
    """Update today's entry in heatmap list."""
    hmap = st.session_state.heatmap
    ts   = today_str
    # find today's entry or add it
    found = False
    for item in hmap:
        if item.get("date") == ts:
            item["status"] = status
            found = True
            break
    if not found:
        hmap.append({"date": ts, "status": status})
    # keep only last 30 days
    st.session_state.heatmap = hmap[-30:]

def build_heatmap_html() -> str:
    """Build 30 boxes. Real dates only — no fake history for new users."""
    hmap_dict = {item["date"]: item["status"] for item in st.session_state.heatmap}
    boxes = ""
    today = date.today()
    for i in range(29, -1, -1):
        d   = today - timedelta(days=i)
        ds  = str(d)
        s   = hmap_dict.get(ds, "he")
        tip = ds
        if i == 0:
            s   = get_heatmap_today_class()
            cls = f"hm {s} ht"
        else:
            cls = f"hm {s}"
        boxes += f'<span class="{cls}" title="{tip}"></span>'
    return boxes

def call_groq(system_prompt: str, history: list, user_msg: str) -> tuple[str,str]:
    """Stream Groq reply. Returns (full_raw, clean)."""
    msgs = [{"role":"system","content":system_prompt}]
    msgs += [{"role":m["role"],"content":m["content"]} for m in history[-8:]]
    msgs.append({"role":"user","content":user_msg})
    full = ""
    ph   = st.empty()
    try:
        stream = groq.chat.completions.create(
            messages=msgs, model="llama-3.1-8b-instant",
            temperature=0.65, max_tokens=320, stream=True
        )
        for chunk in stream:
            tok = chunk.choices[0].delta.content
            if tok:
                full += tok
                ph.markdown(strip_score(full) + " ▌")
        clean = strip_score(full)
        ph.markdown(clean)
    except Exception as e:
        clean = f"Error — {e}"
        ph.error(clean)
        full  = clean
    return full, clean

def make_plan(report: str) -> list:
    goals_str = ", ".join(g["label"] for g in st.session_state.goals) if st.session_state.goals else "studies, health, projects"
    try:
        prompt = (f"Tomorrow's discipline schedule for a person with goals: {goals_str}.\n"
                  f"Today's report: \"{report}\"\n"
                  f"Return ONLY a JSON array. Each item: "
                  f"{{\"hour\":7,\"task\":\"Morning workout\",\"type\":\"health\"}}\n"
                  f"6-8 slots. Types: health/study/project/startup/rest. No explanation.")
        r = groq.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.1-8b-instant", temperature=0.2, max_tokens=500
        )
        raw = re.sub(r'^```json|^```|```$','',
                     r.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
        items = json.loads(raw)
        return [{"hour":p["hour"],"task":p["task"],"type":p.get("type","study"),"fired":False}
                for p in items if "hour" in p and "task" in p]
    except:
        return [
            {"hour":6, "task":"Wake up, no snooze",          "type":"health",  "fired":False},
            {"hour":8, "task":"Focus on primary goal",        "type":"study",   "fired":False},
            {"hour":11,"task":"Continue deep work",           "type":"study",   "fired":False},
            {"hour":14,"task":"Project — 1 focused hour",     "type":"project", "fired":False},
            {"hour":17,"task":"Exercise",                     "type":"health",  "fired":False},
            {"hour":20,"task":"Review day + plan tomorrow",   "type":"study",   "fired":False},
            {"hour":22,"task":"Wind down — sleep on time",    "type":"rest",    "fired":False},
        ]

def check_schedule():
    dname = st.session_state.display_name
    FIXED = {
        7:  f"🌅 Good morning, **{dname}**! Ready to set your targets for today?",
        13: "☀️ **Midday check-in** — What have you completed so far?",
        20: "🌙 **Evening log** — How did today go? Tell me everything.",
        22: f"🔔 **End of day, {dname}** — What did you finish? Sleep time?",
    }
    if st.session_state.last_fire_date != today_str:
        st.session_state.last_fired_hour = -1
        st.session_state.last_fire_date  = today_str
    h = now.hour
    if h in FIXED and st.session_state.last_fired_hour != h:
        st.session_state.last_fired_hour = h
        st.session_state.messages.append({"role":"assistant","content":FIXED[h]})
        return True
    for i, item in enumerate(st.session_state.plan):
        key = f"plan_{today_str}_{item['hour']}"
        if item["hour"] == h and not item["fired"] and key not in st.session_state:
            st.session_state[key]           = True
            st.session_state.plan[i]["fired"]= True
            ic  = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}.get(item["type"],"📌")
            msg = (f"⏰ **{now.strftime('%I:%M %p')} — Scheduled task**\n\n"
                   f"{ic} Time for: **{item['task']}**\n\nMark done when finished.")
            st.session_state.messages.append({"role":"assistant","content":msg})
            return True
    return False

# ── SVG LOGOS ────────────────────────────────────────────────────────────────
LOGO_SM = ('<svg width="40" height="40" viewBox="0 0 40 40" fill="none">'
           '<defs><linearGradient id="g1" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">'
           '<stop offset="0%" stop-color="#2D5FA8"/><stop offset="100%" stop-color="#0B2248"/></linearGradient></defs>'
           '<path d="M20 2L4 8v10.5C4 27.5 11 35 20 38 29 35 36 27.5 36 18.5V8L20 2Z" fill="url(#g1)" stroke="#3A72C8" stroke-width="1.2"/>'
           '<line x1="20" y1="11" x2="20" y2="29" stroke="#7BC8EE" stroke-width=".9"/>'
           '<path d="M20 11C17 11 14 13 14 16.5C13 17.5 12.5 19 13.5 21C12.5 22.5 12.5 25 14.5 26.5C15 28 17 29.2 19.2 29.2H20V11Z" fill="none" stroke="#72C8F0" stroke-width="1.3" stroke-linecap="round"/>'
           '<path d="M20 11C23 11 26 13 26 16.5C27 17.5 27.5 19 26.5 21C27.5 22.5 27.5 25 25.5 26.5C25 28 23 29.2 20.8 29.2H20V11Z" fill="none" stroke="#2E6EC0" stroke-width="1.3" stroke-linecap="round"/>'
           '<line x1="22" y1="15.5" x2="25" y2="15.5" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/>'
           '<line x1="24" y1="15.5" x2="24" y2="19" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/>'
           '<line x1="22" y1="22" x2="25.5" y2="22" stroke="#2E6EC0" stroke-width="1.1" stroke-linecap="round"/>'
           '<circle cx="22" cy="22" r="1" fill="#2E6EC0"/><circle cx="25.5" cy="22" r="1" fill="#2E6EC0"/></svg>')

LOGO_LG = ('<svg width="54" height="54" viewBox="0 0 54 54" fill="none">'
           '<defs><linearGradient id="g2" x1="0" y1="0" x2="54" y2="54" gradientUnits="userSpaceOnUse">'
           '<stop offset="0%" stop-color="#2D5FA8"/><stop offset="100%" stop-color="#0B2248"/></linearGradient></defs>'
           '<path d="M27 3L5 11v14C5 38.5 14.5 48.5 27 52 39.5 48.5 49 38.5 49 25V11L27 3Z" fill="url(#g2)" stroke="#3A80D0" stroke-width="1.4"/>'
           '<line x1="27" y1="15" x2="27" y2="39" stroke="#90D0F0" stroke-width="1"/>'
           '<path d="M27 15C23 15 19 18 19 22.5C17.5 24 17 26 18 28.5C17 30.5 17 33.5 19.5 35.5C20 37.5 22.5 39.2 25.5 39.2H27V15Z" fill="none" stroke="#78C8F0" stroke-width="1.5" stroke-linecap="round"/>'
           '<path d="M19.5 24.5C21.5 23 23 24.5 21 26.5" stroke="#78C8F0" stroke-width="1.2" stroke-linecap="round"/>'
           '<path d="M18.5 30.5C21 29 22.5 32.5 20 34" stroke="#78C8F0" stroke-width="1.2" stroke-linecap="round"/>'
           '<path d="M27 15C31 15 35 18 35 22.5C36.5 24 37 26 36 28.5C37 30.5 37 33.5 34.5 35.5C34 37.5 31.5 39.2 28.5 39.2H27V15Z" fill="none" stroke="#2A68C0" stroke-width="1.5" stroke-linecap="round"/>'
           '<line x1="29.5" y1="20" x2="33.5" y2="20" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/>'
           '<line x1="32.5" y1="20" x2="32.5" y2="25" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/>'
           '<line x1="29.5" y1="29.5" x2="34" y2="29.5" stroke="#2A68C0" stroke-width="1.3" stroke-linecap="round"/>'
           '<circle cx="29.5" cy="29.5" r="1.3" fill="#2A68C0"/><circle cx="34" cy="29.5" r="1.3" fill="#2A68C0"/></svg>')

# ── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('[https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Outfit:wght@400;500;600;700;800;900&display=swap](https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Outfit:wght@400;500;600;700;800;900&display=swap)');
*,*::before,*::after{box-sizing:border-box;}
html,body,.stApp{background:#060A0F !important;font-family:'Outfit',sans-serif !important;color:#D8E6F3 !important;}

/* Fix: Make the header transparent but keep it active so the sidebar toggle is clickable */
[data-testid="stHeader"] {background-color: transparent !important; box-shadow: none !important;}
#MainMenu, footer, .stDeployButton, [data-testid="stToolbar"], [data-testid="stDecoration"] {display:none !important;}

.block-container{padding:1.8rem 2.5rem 5rem !important;max-width:100% !important;width:100% !important;}
::-webkit-scrollbar{width:4px;}::-webkit-scrollbar-track{background:#090E16;}::-webkit-scrollbar-thumb{background:#1C2B3A;border-radius:4px;}

/* Upgraded Sidebar Styling */
[data-testid="stSidebar"]{background:#07090F !important;border-right:1px solid #1A2535 !important;width:300px !important;min-width:300px !important;}
[data-testid="stSidebar"] .block-container{padding:1.6rem 1.4rem 2rem !important;}

.brand{display:flex;align-items:center;gap:12px;margin-bottom:4px;}
.bname{font-weight:800;font-size:1.3rem;color:#EEF5FF;letter-spacing:-.3px;line-height:1;}
.btag{font-family:'DM Mono',monospace;font-size:.72rem;color:#4A6888;letter-spacing:1.5px;text-transform:uppercase;margin-left:54px;margin-bottom:14px;margin-top:1px;}

.pill{display:flex;align-items:center;gap:8px;border-radius:99px;padding:6px 13px;margin-bottom:5px;font-family:'DM Mono',monospace;font-size:.72rem;font-weight:500;}
.pg{background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.22);color:#22C55E;}
.pb{background:rgba(56,189,248,.08);border:1px solid rgba(56,189,248,.25);color:#38BDF8;}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;animation:blink 2.5s ease-in-out infinite;}
.dg{background:#22C55E;box-shadow:0 0 6px #22C55E;}
.db{background:#38BDF8;box-shadow:0 0 6px #38BDF8;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}

.sec{font-family:'DM Mono',monospace;font-size:.68rem;color:#4A6888;letter-spacing:1.8px;text-transform:uppercase;margin:18px 0 9px;padding-bottom:6px;border-bottom:1px solid #1A2535;font-weight:500;}

.hmap{display:grid;grid-template-columns:repeat(10,1fr);gap:4px;width:100%;}
.hm{aspect-ratio:1;border-radius:3px;transition:transform .12s;cursor:default;min-width:0;}
.hm:hover{transform:scale(1.4);}
.he{background:#0D1520;border:1px solid #1A2535;}
.ha{background:#2563EB;box-shadow:0 0 4px rgba(37,99,235,.6);}
.hf{background:#DC2626;box-shadow:0 0 4px rgba(220,38,38,.55);}
.ht{outline:2px solid #38BDF8;outline-offset:1px;}
.hleg{display:flex;gap:12px;margin-top:8px;font-family:'DM Mono',monospace;font-size:.68rem;color:#4A6888;}
.hd{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:4px;}

.gc{display:flex;align-items:center;gap:10px;background:#0C1219;border:1px solid #1A2535;border-radius:10px;padding:10px 12px;margin-bottom:6px;transition:background .2s,border-color .2s,transform .2s;}
.gc:hover{background:#111820;border-color:#2563EB;transform:translateX(3px);}
.ge{font-size:1.1rem;flex-shrink:0;}
.gt{font-weight:700;font-size:.88rem;color:#E0EAF8;display:block;line-height:1.25;}
.gs{font-family:'DM Mono',monospace;font-size:.68rem;color:#4A6888;margin-top:1px;display:block;}

.pi{display:flex;align-items:flex-start;gap:10px;padding:8px 11px;margin-bottom:5px;border-radius:8px;background:#0C1320;border:1px solid #1A2535;transition:border-color .2s;}
.pi:hover{border-color:#38BDF8;}
.pt{font-family:'DM Mono',monospace;font-size:.7rem;color:#38BDF8;flex-shrink:0;min-width:36px;font-weight:500;}
.pk{font-size:.82rem;color:#B8D0E8;line-height:1.4;}
.pk.done{opacity:.38;text-decoration:line-through;}

.crow{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px;}
/* Upgraded Cards with Glassmorphism and Hover */
.card{background:rgba(8, 13, 20, 0.7);backdrop-filter:blur(10px);border:1px solid #1A2535;border-radius:16px;padding:18px 20px;position:relative;overflow:hidden;transition:transform .2s ease-in-out, border-color .2s ease, box-shadow .2s ease;}
.card:hover{transform:translateY(-3px);border-color:#2563EB;box-shadow:0 8px 24px rgba(0,0,0,.2);}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#38BDF8,#2563EB);opacity:.7;}
.clabel{font-family:'DM Mono',monospace;font-size:.75rem;color:#5A80A8;letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;font-weight:500;}
.cval{font-weight:800;font-size:2.1rem;color:#EEF5FF;line-height:1;letter-spacing:-1px;}
.cunit{font-size:.95rem;font-weight:400;color:#3A5070;}
.cbar{height:4px;background:#111A26;border-radius:99px;margin-top:12px;overflow:hidden;}
.cbarf{height:100%;border-radius:99px;background:linear-gradient(90deg,#38BDF8,#2563EB);transition:width 1s cubic-bezier(.25,.46,.45,.94);}
.cst{font-weight:700;font-size:1.15rem;margin-top:6px;line-height:1.2;}
.cup{font-family:'DM Mono',monospace;font-size:.7rem;color:#22C55E;margin-top:7px;}
.cdn{font-family:'DM Mono',monospace;font-size:.7rem;color:#DC2626;margin-top:7px;}
.cne{font-family:'DM Mono',monospace;font-size:.7rem;color:#3A5070;margin-top:7px;}

.mhdr{display:flex;align-items:center;gap:16px;margin-bottom:20px;}
.mtitle{font-weight:900;font-size:2.8rem;color:#EEF5FF;letter-spacing:-2px;line-height:1;}
.msub{font-family:'DM Mono',monospace;font-size:.68rem;color:#5A80A8;letter-spacing:2.5px;text-transform:uppercase;margin-top:5px;font-weight:500;}

.ubadge{font-family:'DM Mono',monospace;font-size:.72rem;color:#38BDF8;background:rgba(56,189,248,.09);border:1px solid rgba(56,189,248,.25);border-radius:99px;padding:4px 12px;margin-bottom:11px;display:inline-block;font-weight:500;}

.div{border:none;border-top:1px solid #111A26;margin:0 0 20px;}

[data-testid="stChatMessage"]{background:transparent !important;border:none !important;padding:14px 0 !important;border-bottom:1px solid #0D1520 !important;}
[data-testid="chatAvatarIcon-user"]{background:#111D2C !important;border:1px solid #1C2D40 !important;border-radius:9px !important;}
[data-testid="chatAvatarIcon-assistant"]{background:linear-gradient(135deg,#1E4A8A,#0F2A5A) !important;border:1px solid #2A5FAD !important;border-radius:9px !important;box-shadow:0 0 10px rgba(37,99,235,.35) !important;}
[data-testid="stChatMessage"] p{font-family:'Outfit',sans-serif !important;font-size:1.05rem !important;font-weight:400 !important;line-height:1.75 !important;color:#C8D8EC !important;}
[data-testid="stChatMessage"] p strong,[data-testid="stChatMessage"] strong{font-weight:700 !important;color:#EEF5FF !important;}
[data-testid="stChatMessage"] li{font-family:'Outfit',sans-serif !important;font-size:.97rem !important;line-height:1.7 !important;color:#C8D8EC !important;}

/* Upgraded Chat Input */
[data-testid="stChatInput"]{background:#080D14 !important;border:1px solid #1C2B3A !important;border-radius:14px !important;transition: border-color .2s, box-shadow .2s;}
[data-testid="stChatInput"]:focus-within{border-color:#38BDF8 !important;box-shadow:0 0 0 3px rgba(56,189,248,.15) !important;}
[data-testid="stChatInput"] textarea{font-family:'Outfit',sans-serif !important;font-size:1.02rem !important;color:#D8E6F3 !important;background:transparent !important;caret-color:#38BDF8 !important;}
[data-testid="stChatInput"] textarea::placeholder{color:#2B3F56 !important;}
[data-testid="stChatInput"] button{background:linear-gradient(135deg,#2563EB,#1A3A80) !important;border-radius:10px !important;border:none !important; transition: opacity .2s;}
[data-testid="stChatInput"] button:hover{opacity:0.9 !important;}

.stat-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;}
.stat-label{font-family:'DM Mono',monospace;font-size:.68rem;color:#4A6888;}
.stat-val{font-family:'DM Mono',monospace;font-size:.72rem;color:#C8D8EC;font-weight:500;}

[data-testid="stMetric"]{display:none !important;}
</style>
""", unsafe_allow_html=True)

# ── LOGIN SCREEN ─────────────────────────────────────────────────────────────
if not st.session_state.user_set:
    st.markdown("""
    <style>
    .stTextInput input{background:#0D1219 !important;border:1px solid #1A2535 !important;border-radius:10px !important;color:#D8E6F3 !important;font-size:1.05rem !important;padding:.8rem 1.2rem !important; transition: border-color .2s, box-shadow .2s;}
    .stTextInput input:focus{border-color:#38BDF8 !important; box-shadow:0 0 0 3px rgba(56,189,248,.15) !important;}
    .stButton button{background:linear-gradient(135deg,#2563EB,#1A3A80) !important;color:white !important;border:none !important;border-radius:10px !important;font-size:1.05rem !important;font-weight:700 !important;padding:.75rem !important; transition: transform .1s, opacity .2s;}
    .stButton button:hover{opacity:0.9 !important; transform:translateY(-1px) !important;}
    </style>""", unsafe_allow_html=True)
    st.markdown("<br><br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown(f"""
        <div style='text-align:center;margin-bottom:36px;'>
            <div style='display:flex;justify-content:center;margin-bottom:16px;'>{LOGO_LG}</div>
            <div style='font-size:3.2rem;font-weight:900;color:#EEF5FF;letter-spacing:-2px;line-height:1;'>AXIOM</div>
            <div style='font-family:"DM Mono",monospace;font-size:.7rem;color:#4A6888;letter-spacing:3px;text-transform:uppercase;margin-top:8px;'>Discipline Intelligence Engine</div>
            <div style='font-size:.9rem;color:#3A5070;margin-top:14px;line-height:1.6;'>
                Your personal accountability AI.<br>
                Remembers your goals. Tracks your progress.<br>Never forgets.
            </div>
        </div>
        """, unsafe_allow_html=True)
        name = st.text_input("", placeholder="Enter your name to begin...", key="name_input", label_visibility="collapsed")
        if st.button("Start →", use_container_width=True):
            if name.strip():
                uname = name.strip().lower().replace(" ", "-")
                dname = name.strip().title()
                # Reset all state cleanly
                for k, v in SD.items():
                    st.session_state[k] = v
                st.session_state.username     = uname
                st.session_state.display_name = dname
                st.session_state.user_set     = True
                st.rerun()
            else:
                st.warning("Please enter your name.")
    st.stop()

# ── LOAD PROFILE FROM HINDSIGHT (once per login) ─────────────────────────────
if not st.session_state.profile_loaded:
    with st.spinner(f"Loading {st.session_state.display_name}'s profile..."):
        p = load_profile()
    if p:
        # Returning user — restore everything
        st.session_state.score          = p.get("score", 0)
        st.session_state.goals          = p.get("goals", [])
        st.session_state.heatmap        = p.get("heatmap", [])
        st.session_state.streak         = p.get("streak", 0)
        st.session_state.total_sessions = p.get("total_sessions", 0)
        st.session_state.onboard_step   = p.get("onboard_step", 0)
        st.session_state.raw_goals_text = p.get("raw_goals_text", "")
        st.session_state.is_new_user    = (st.session_state.onboard_step < 2)
        st.session_state.plan           = p.get("plan", [])
        st.session_state.plan_date      = p.get("plan_date", "")
        st.session_state.last_session_date    = p.get("last_session_date", "")
        st.session_state.last_session_summary = p.get("last_session_summary", "")
        # Restore previous chat messages so user sees old conversation
        saved_msgs = p.get("last_session_messages", [])
        if saved_msgs:
            st.session_state.messages = saved_msgs
        # restore today's status if same day, else start fresh day
        last_updated = p.get("last_updated", "")
        if last_updated == today_str:
            st.session_state.today_status = p.get("today_status", "empty")
        else:
            st.session_state.today_status = "empty"
            st.session_state.total_sessions += 1
            # New day — add separator message to show date break
            if saved_msgs:
                separator = {
                    "role": "assistant",
                    "content": f"---\n📅 **New session — {now.strftime('%d %b %Y')}**\n\nWelcome back! Above is your history from last time."
                }
                st.session_state.messages.append(separator)
    else:
        # No Hindsight profile found
        uname_check = st.session_state.username
        if "samith" in uname_check:
            # Samith's pre-seeded data — his real profile
            st.session_state.score        = 820
            st.session_state.onboard_step = 2
            st.session_state.is_new_user  = False
            st.session_state.total_sessions = 15
            st.session_state.streak       = 5
            st.session_state.today_status = "empty"
            st.session_state.goals = [
                {"icon":"🧠","label":"NeuroKey BCI",    "detail":"EEG + Python Capstone"},
                {"icon":"📡","label":"VLSI & DSP",      "detail":"VTU Coursework"},
                {"icon":"🚀","label":"Tinkercore",      "detail":"Startup · ECE Outreach"},
                {"icon":"💪","label":"Gym & Nutrition", "detail":"Hostel Diet · Strength"},
                {"icon":"🔩","label":"IoT Club",        "detail":"Leadership · Workshops"},
            ]
            # Seed 30-day heatmap with realistic history
            from datetime import timedelta
            history_pattern = ["active","active","fail","active","active","active","empty",
                               "active","fail","active","active","active","active","fail",
                               "active","active","active","empty","active","active","active",
                               "active","active","fail","active","active","empty","active","active"]
            hmap = []
            for i, status in enumerate(history_pattern):
                d = date.today() - timedelta(days=29-i)
                hmap.append({"date": str(d), "status": status})
            st.session_state.heatmap = hmap
            st.session_state.last_session_date = str(date.today() - timedelta(days=1))
            st.session_state.last_session_summary = (
                "Samith worked on NeuroKey EEG signal processing, "
                "completed DSP assignment, and hit the gym."
            )
            # Save this seeded profile to Hindsight so it persists
            save_profile()
        else:
            # Genuine new user
            st.session_state.is_new_user  = True
            st.session_state.onboard_step = 0
    st.session_state.profile_loaded = True

pct   = round((st.session_state.score / 1000) * 100, 1)
dname = st.session_state.display_name
uname = st.session_state.username

# ── CHECK SCHEDULED REMINDERS ───────────────────────────────────────────────
if check_schedule():
    st.rerun()

# ── BOOT MESSAGE (first message of this session) ─────────────────────────────
# Only show boot message if no messages loaded from Hindsight
if not st.session_state.messages:
    if st.session_state.onboard_step == 0:
        # Complete new user — warm introduction
        boot = (
            f"Hey **{dname}**! 👋 I'm **AXIOM** — your personal discipline AI.\n\n"
            "I'm built to help you stay consistent with your goals across days, weeks, and months. "
            "I remember everything you tell me — your wins, your struggles, your excuses — "
            "and I hold you accountable so you actually make progress.\n\n"
            "**Let's start with one question:**\n\n"
            "What are you currently working on or trying to improve? "
            "*(Could be studies, a project, fitness, a startup — anything.)*"
        )
        st.session_state.onboard_step = 1
    elif st.session_state.onboard_step == 1:
        # Mid-onboarding
        boot = (
            f"Welcome back, **{dname}**! 👋\n\n"
            "We were just getting to know each other. Tell me more about your goals "
            "so I can start tracking your progress properly."
        )
    else:
        # Returning user — show their data, pick up where they left off
        goals_str = ", ".join(g["label"] for g in st.session_state.goals) if st.session_state.goals else "your goals"
        mem_snippet = recall("recent progress update today plan")
        if mem_snippet and mem_snippet != "No prior records.":
            context = f"\n\nFrom our last session: _{mem_snippet[:200]}_"
        else:
            context = ""
        boot = (
            f"Welcome back, **{dname}**! 👋\n\n"
            f"I've got your profile loaded — tracking: **{goals_str}**. "
            f"Your current score is **{st.session_state.score}/1000**."
            f"{context}\n\n"
            "Ready to log today's update? Tell me how things went — "
            "projects, health, sleep, whatever's on your mind."
        )
    st.session_state.messages.append({"role":"assistant","content":boot})

# ── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        f'<div class="brand">{LOGO_SM}<span class="bname">AXIOM</span></div>'
        f'<div class="btag">Discipline Intelligence Engine</div>'
        f'<div class="ubadge">👤 {dname}</div>'
        f'<div class="pill pg"><span class="dot dg"></span>Hindsight Memory: LIVE</div>'
        f'<div class="pill pb"><span class="dot db"></span>Groq LLM: CONNECTED</div>',
        unsafe_allow_html=True
    )

    # ── HEATMAP ──
    st.markdown('<div class="sec">30-Day Activity</div>', unsafe_allow_html=True)
    if st.session_state.heatmap or st.session_state.today_status != "empty":
        hmap_html = build_heatmap_html()
        st.markdown(
            f'<div class="hmap">{hmap_html}</div>'
            f'<div class="hleg">'
            f'<span><span class="hd" style="background:#2563EB"></span>On Track</span>'
            f'<span><span class="hd" style="background:#DC2626"></span>Excused</span>'
            f'<span><span class="hd" style="background:#0D1520;border:1px solid #182030"></span>Pending</span>'
            f'</div>', unsafe_allow_html=True
        )
    else:
        st.markdown(
            '<div style="font-family:\'DM Mono\',monospace;font-size:.65rem;color:#253545;padding:8px 0;">'
            'Your activity will appear here as you log progress.</div>',
            unsafe_allow_html=True
        )

    # ── GOALS ──
    st.markdown('<div class="sec">Active Targets</div>', unsafe_allow_html=True)
    if st.session_state.goals:
        for g in st.session_state.goals:
            st.markdown(
                f'<div class="gc"><span class="ge">{g.get("icon","🎯")}</span>'
                f'<div><span class="gt">{g.get("label","Goal")}</span>'
                f'<span class="gs">{g.get("detail","")}</span></div></div>',
                unsafe_allow_html=True
            )
    else:
        st.markdown(
            '<div style="font-family:\'DM Mono\',monospace;font-size:.65rem;color:#253545;padding:8px 0;">'
            'Goals will appear here after you tell AXIOM what you\'re working on.</div>',
            unsafe_allow_html=True
        )

    # ── TOMORROW'S PLAN ──
    if st.session_state.plan:
        st.markdown("<div class='sec'>📋 Tomorrow's Plan</div>", unsafe_allow_html=True)
        icons = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
        for item in st.session_state.plan:
            h   = item["hour"]
            ap  = "AM" if h < 12 else "PM"
            h12 = h if h <= 12 else h - 12
            h12 = 12 if h12 == 0 else h12
            ic  = icons.get(item["type"], "📌")
            dc  = "done" if item["fired"] else ""
            st.markdown(
                f'<div class="pi"><span class="pt">{h12}{ap}</span>'
                f'<span class="pk {dc}">{ic} {item["task"]}</span></div>',
                unsafe_allow_html=True
            )

    # ── STATS ──
    if st.session_state.total_sessions > 0 or st.session_state.streak > 0:
        st.markdown('<div class="sec">Stats</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-family:\'DM Mono\',monospace;font-size:.65rem;color:#4A6888;line-height:2;">'
            f'🔥 Streak: {st.session_state.streak} days<br>'
            f'📅 Sessions: {st.session_state.total_sessions}</div>',
            unsafe_allow_html=True
        )

    st.markdown('<div class="sec">Session</div>', unsafe_allow_html=True)
    if st.button("Switch User", use_container_width=True):
        for k, v in SD.items():
            st.session_state[k] = v
        st.rerun()

# ── MAIN HEADER ──────────────────────────────────────────────────────────────
st.markdown(
    f'<div class="mhdr">{LOGO_LG}'
    f'<div><div class="mtitle">AXIOM</div>'
    f'<div class="msub">Discipline Intelligence &nbsp;·&nbsp; Persistent Memory &nbsp;·&nbsp; No Excuses</div>'
    f'</div></div>',
    unsafe_allow_html=True
)

# ── SCORE CARDS ──────────────────────────────────────────────────────────────
d     = st.session_state.last_delta
dhtml = (f'<div class="cup">▲ +{d} pts this session</div>' if d > 0 else
         f'<div class="cdn">▼ {d} pts this session</div>'   if d < 0 else
         '<div class="cne">— Awaiting today\'s log</div>')

sc     = st.session_state.today_status
sc_col = {"active":"#22C55E","fail":"#DC2626"}.get(sc, "#1E3050")
sc_lbl = {"active":"🟢 On Track","fail":"🔴 Off Track","empty":"⏳ Pending"}.get(sc, "⏳ Pending")

# For new users show a welcome card instead of score
if st.session_state.onboard_step < 2:
    st.markdown(f"""<div class="crow">
  <div class="card" style="grid-column:span 2">
    <div class="clabel">Getting Started</div>
    <div class="cst" style="color:#38BDF8;margin-top:8px;">Tell AXIOM your goals</div>
    <div class="cne">Answer the questions in the chat to set up your profile</div>
  </div>
  <div class="card">
    <div class="clabel">Your Score</div>
    <div class="cval">0<span class="cunit"> /1000</span></div>
    <div class="cbar"><div class="cbarf" style="width:0%"></div></div>
  </div>
  <div class="card">
    <div class="clabel">Memory Bank</div>
    <div class="cst" style="color:#38BDF8">ACTIVE</div>
    <div class="cup">axiom-{uname}</div>
  </div>
</div>""", unsafe_allow_html=True)
else:
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
    <div class="cst" style="color:{sc_col}">{sc_lbl}</div>
    {dhtml}
  </div>
  <div class="card">
    <div class="clabel">Memory Bank</div>
    <div class="cst" style="color:#38BDF8">ACTIVE</div>
    <div class="cup">axiom-{uname}</div>
  </div>
</div>""", unsafe_allow_html=True)

st.markdown('<hr class="div">', unsafe_allow_html=True)

# ── CHAT HISTORY ─────────────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="👤" if msg["role"]=="user" else "🛡️"):
        st.markdown(msg["content"])

# ── INPUT ─────────────────────────────────────────────────────────────────────
placeholder_text = (
    "Tell me about yourself..." if st.session_state.onboard_step < 2
    else "Log your progress, or say good morning..."
)
user_input = st.chat_input(placeholder_text)

if user_input:
    with st.chat_message("user", avatar="👤"):
        st.markdown(user_input)
    st.session_state.messages.append({"role":"user","content":user_input})

    mem = recall(user_input)

    # ── BUILD SYSTEM PROMPT ───────────────────────────────────────────────
    goals_ctx = ""
    if st.session_state.goals:
        goals_ctx = "USER'S GOALS:\n" + "\n".join(
            f"- {g['label']}: {g.get('detail','')}" for g in st.session_state.goals
        )
    else:
        goals_ctx = "USER'S GOALS: Not yet collected — still onboarding."

    if st.session_state.onboard_step < 2:
        SYSTEM = f"""You are AXIOM — a warm, intelligent accountability AI. You are onboarding {dname} right now.

ONBOARDING TASK:
You need to collect the following information from {dname} through friendly conversation:
1. What they are currently working on (studies, job, projects, startup, etc.)
2. Their health/fitness goals (gym, diet, sleep)
3. Their biggest current challenge or accountability need

Rules:
- Ask ONE question at a time. Don't dump all questions at once.
- Be conversational and warm — not robotic.
- When you have enough information (at least 2-3 goals collected), confirm with them:
  "Perfect! I've got your profile set up. I'll track [their goals] from now on. Ready to get started?"
- Use their name: {dname}
- DO NOT add any [SCORE:...] tags during onboarding.

MEMORY: {mem}"""
    else:
        SYSTEM = f"""You are AXIOM — a firm but respectful accountability AI mentor for {dname}.

{goals_ctx}
CURRENT SCORE: {st.session_state.score}/1000
TODAY'S STATUS: {st.session_state.today_status}
HINDSIGHT MEMORY: {mem}

BEHAVIOR:
1. Greet naturally for "hi/hello/good morning" — don't interrogate immediately.
2. Reference past memory naturally with dates when relevant.
3. For progress reports: acknowledge clearly, update score, ask about next goal if not mentioned.
4. For excuses: be firm, cite pattern if memory shows it, give ONE concrete action to do right now.
5. Max 4 sentences unless giving a plan. **Bold** key items.
6. When user gives full day report → end with "Building your plan for tomorrow."
7. SCORING (add as last line when there is actual content to score):
   Progress/discipline → [SCORE:+X] where X=5 to 25
   Excuses/failure     → [SCORE:-Y] where Y=10 to 30
   Casual greeting only → NO score tag."""

    # ── GET RESPONSE ─────────────────────────────────────────────────────
    with st.chat_message("assistant", avatar="🛡️"):
        full, clean = call_groq(SYSTEM, st.session_state.messages[:-1], user_input)

    st.session_state.messages.append({"role":"assistant","content":clean})
    save_memory(user_input, clean)

    # ── EXTRACT GOALS DURING ONBOARDING ──────────────────────────────────
    if st.session_state.onboard_step < 2:
        # Try to extract goals from what user said
        try:
            gr = groq.chat.completions.create(
                messages=[{"role":"user","content":
                    f"Extract 2-5 short goal labels from this text: '{user_input}'\n"
                    f"Format: JSON array like: [{{\"icon\":\"📚\",\"label\":\"DSP Studies\",\"detail\":\"VTU coursework\"}}]\n"
                    f"Choose appropriate emoji icons. Return ONLY the JSON array. If no clear goals, return []."}],
                model="llama-3.1-8b-instant", temperature=0.1, max_tokens=200
            )
            raw_g = re.sub(r'^```json|^```|```$','',
                           gr.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
            extracted = json.loads(raw_g)
            if isinstance(extracted, list) and extracted:
                existing_labels = [g["label"] for g in st.session_state.goals]
                for g in extracted:
                    if g.get("label") and g["label"] not in existing_labels:
                        st.session_state.goals.append(g)
                st.session_state.goals = st.session_state.goals[:8]
                st.session_state.raw_goals_text += f" {user_input}"
        except: pass

        # Check if AXIOM confirmed onboarding complete
        if any(phrase in clean.lower() for phrase in
               ["profile set up","got your profile","ready to get started",
                "i'll track","i will track","let's get started","let's begin"]):
            st.session_state.onboard_step   = 2
            st.session_state.is_new_user    = False
            st.session_state.total_sessions = 1
            st.session_state.streak         = 1

    # ── UPDATE SCORE & HEATMAP ────────────────────────────────────────────
    pts = parse_score(full)
    if pts is not None and pts != 0:
        st.session_state.score        = max(0, min(1000, st.session_state.score + pts))
        st.session_state.today_status = "active" if pts > 0 else "fail"
        st.session_state.last_delta   = pts
        update_heatmap(st.session_state.today_status)

    # ── GENERATE PLAN IF FULL DAY REPORT ─────────────────────────────────
    is_report = any(w in user_input.lower() for w in [
        "today","did","done","gym","slept","sleep","finished","worked",
        "completed","skipped","missed","studied","worked out","progress"
    ])
    if is_report and st.session_state.onboard_step == 2 and st.session_state.plan_date != today_str:
        with st.spinner("⚙️ Building your plan for tomorrow..."):
            plan = make_plan(user_input)
        st.session_state.plan      = plan
        st.session_state.plan_date = today_str
        icons    = {"health":"💪","study":"📡","project":"🧠","startup":"🚀","rest":"😴"}
        plan_msg = f"📋 **{dname}'s Plan for Tomorrow**\n\n"
        for item in plan:
            h   = item["hour"]
            ap  = "AM" if h < 12 else "PM"
            h12 = h if h <= 12 else h - 12
            h12 = 12 if h12 == 0 else h12
            plan_msg += f"**{h12}:00 {ap}** — {icons.get(item['type'],'📌')} {item['task']}\n\n"
        plan_msg += "_I'll remind you at each scheduled time. Stay consistent._"
        st.session_state.messages.append({"role":"assistant","content":plan_msg})

    # ── SAVE PROFILE TO HINDSIGHT ─────────────────────────────────────────
    save_profile()

    # Generate session summary in background after enough conversation
    if len(st.session_state.messages) % 6 == 0 and len(st.session_state.messages) > 0:
        def _summarize():
            try:
                recent = st.session_state.messages[-6:]
                conv   = "\n".join(f"{m['role'].upper()}: {m['content'][:200]}" for m in recent)
                r = groq.chat.completions.create(
                    messages=[{"role":"user","content":
                        f"Summarize this conversation in 1-2 sentences for context: {conv}"}],
                    model="llama-3.1-8b-instant", temperature=0.1, max_tokens=100
                )
                st.session_state.last_session_summary = r.choices[0].message.content.strip()
            except: pass
        threading.Thread(target=_summarize, daemon=True).start()

    # Rerun to refresh UI cards and heatmap
    if pts is not None and pts != 0:
        st.rerun()
