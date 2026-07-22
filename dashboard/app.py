"""
dashboard/app.py  —  Phase 6: Web Dashboard
Run:  python dashboard/app.py   (from anywhere)
Then: http://127.0.0.1:8080
"""
from __future__ import annotations
import sys
import os

# Ensure project root is on path so 'agents', 'config' etc. can be imported
# regardless of which directory you run this from
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
import json
from collections import defaultdict
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timezone

REPORTS_DIR = Path("./reports")
app = FastAPI(title="AI Code Review Dashboard", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_copilot = None
def get_copilot():
    global _copilot
    if _copilot is None:
        try:
            from agents.copilot_agent import GitHubCopilotAgent
            _copilot = GitHubCopilotAgent()
        except Exception as e:
            print(f"[dashboard] Copilot init failed: {e}")
    return _copilot

def load_reports(limit: int = 200) -> list[dict]:
    if not REPORTS_DIR.exists():
        return []
    reports = []
    for f in sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_filename"] = f.name
            reports.append(data)
        except Exception:
            pass
    return reports

def compute_stats(reports: list[dict]) -> dict:
    if not reports:
        return {"total_prs":0,"avg_score":0,"total_critical":0,"approved":0,
                "categories":{},"score_trend":[],"agent_avg_scores":{}}
    total_prs      = len(reports)
    avg_score      = sum(r.get("overall_score",0) for r in reports) / total_prs
    total_critical = sum(r.get("critical_count",0) for r in reports)
    approved_count = sum(1 for r in reports if r.get("approved"))
    cat_counts: dict[str,int] = defaultdict(int)
    for r in reports:
        for f in r.get("findings",[]):
            cat_counts[f.get("category","other")] += 1
    trend = [{"pr":f"PR #{r.get('pr_number','?')}","score":r.get("overall_score",0),
               "date":r.get("reviewed_at","")[:10],"repo":r.get("repo","")}
             for r in reversed(reports[:20])]
    agent_scores: dict[str,list[float]] = defaultdict(list)
    for r in reports:
        for agent, summary in r.get("agent_summaries",{}).items():
            agent_scores[agent].append(float(summary.get("score",0)))
    agent_avg = {a: round(sum(s)/len(s),2) for a,s in agent_scores.items() if s}
    blocked_count  = sum(1 for r in reports if r.get("gate",{}).get("blocked") is True)
    cleared_count  = sum(1 for r in reports if r.get("gate",{}).get("blocked") is False)
    resolved_count = sum(len(r.get("gate",{}).get("resolved_issues",[])) for r in reports)
    return {"total_prs":total_prs,"avg_score":round(avg_score,2),
            "total_critical":total_critical,"approved":approved_count,
            "categories":dict(cat_counts),"score_trend":trend,"agent_avg_scores":agent_avg,
            "blocked_count":blocked_count,"cleared_count":cleared_count,"resolved_count":resolved_count}

@app.get("/api/reports")
async def get_reports(limit:int=200): return load_reports(limit)

@app.get("/api/stats")
async def get_stats(): return compute_stats(load_reports())

@app.get("/api/reports/{filename}")
async def get_report(filename:str):
    path = REPORTS_DIR/filename
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"error":"not found"}

class CopilotQuestion(BaseModel):
    question: str

@app.post("/api/copilot")
async def ask_copilot(req: CopilotQuestion):
    copilot = get_copilot()
    if copilot is None:
        return {"answer": "Copilot unavailable. Check GROQ_API_KEY in .env"}
    try:
        return {"answer": copilot.ask(req.question)}
    except Exception as e:
        return {"answer": f"Error: {e}"}

@app.get("/health")
async def health():
    return {"status":"ok","reports_found":len(load_reports())}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    reports      = load_reports(100)
    stats        = compute_stats(reports)
    trend_data   = json.dumps(stats["score_trend"])
    cat_data     = json.dumps(stats["categories"])
    agent_data   = json.dumps(stats["agent_avg_scores"])
    reports_json = json.dumps([
        {"filename":r.get("_filename",""),"repo":r.get("repo","—"),
         "pr":r.get("pr_number","?"),"title":(r.get("pr_title") or "—")[:45],
         "score":r.get("overall_score",0),"approved":r.get("approved",False),
         "critical":r.get("critical_count",0),"warnings":r.get("warning_count",0),
         "total":r.get("total_findings",0),"date":(r.get("reviewed_at") or "")[:10],
         "pipeline":r.get("pipeline","standard"),
         "gate_blocked": r.get("gate",{}).get("blocked", None),
         "gate_reason":  r.get("gate",{}).get("reason",""),
         "resolved":     r.get("gate",{}).get("resolved_issues",[]),
         "still_present":r.get("gate",{}).get("still_present",[])}
        for r in reports
    ])
    ts       = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    sc_color = '#4ade80' if stats['avg_score']>=.85 else '#fb923c' if stats['avg_score']>=.5 else '#f87171'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>AI Code Review Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;overflow:hidden}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column}}

/* Header */
.header{{background:#1e293b;padding:14px 28px;border-bottom:1px solid #334155;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}}
.header h1{{font-size:18px;font-weight:700;color:#f1f5f9}}
.header .sub{{font-size:11px;color:#64748b;margin-top:2px}}
.badge{{background:#3b82f6;color:white;padding:2px 8px;border-radius:999px;font-size:11px}}

/* Two-column layout — fills remaining height */
.layout{{display:grid;grid-template-columns:1fr 380px;flex:1;overflow:hidden}}

/* Left — scrollable */
.main{{overflow-y:auto;padding:20px 24px;display:flex;flex-direction:column;gap:16px}}

/* Right — fixed copilot panel, full height */
.copilot-panel{{
  display:flex;flex-direction:column;
  background:#1e293b;
  border-left:1px solid #334155;
  height:100%;
  overflow:hidden;
}}
.cp-head{{
  padding:16px 18px 12px;
  border-bottom:1px solid #334155;
  flex-shrink:0;
}}
.cp-head h2{{font-size:14px;font-weight:700;color:#f1f5f9;display:flex;align-items:center;gap:6px}}
.cp-head p{{font-size:11px;color:#64748b;margin-top:4px}}

/* Chat messages — scrollable */
.cp-msgs{{
  flex:1;overflow-y:auto;
  padding:14px 16px;
  display:flex;flex-direction:column;gap:10px;
}}
.bubble{{
  padding:10px 14px;border-radius:12px;
  font-size:13px;line-height:1.65;
  max-width:96%;word-break:break-word;white-space:pre-wrap;
}}
.bubble-bot{{
  background:#0f172a;color:#cbd5e1;
  border:1px solid #334155;
  border-bottom-left-radius:3px;
  align-self:flex-start;
}}
.bubble-user{{
  background:#1e3a5f;color:#bfdbfe;
  border-bottom-right-radius:3px;
  align-self:flex-end;
}}
.bubble-thinking{{
  background:#0f172a;color:#64748b;
  border:1px solid #334155;
  border-bottom-left-radius:3px;
  align-self:flex-start;
  font-style:italic;
  animation:pulse 1.4s ease-in-out infinite;
}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.45}}}}

/* Quick chips */
.chips{{display:flex;flex-wrap:wrap;gap:5px;padding:10px 16px;border-top:1px solid #1e293b;flex-shrink:0}}
.chip{{
  background:#0f172a;border:1px solid #334155;color:#94a3b8;
  padding:4px 10px;border-radius:999px;font-size:11px;
  cursor:pointer;transition:all .15s;
}}
.chip:hover{{border-color:#3b82f6;color:#60a5fa;background:#1e3a5f}}

/* Input row */
.cp-input-area{{
  padding:12px 14px;
  border-top:1px solid #334155;
  flex-shrink:0;
  background:#1e293b;
}}
.input-row{{display:flex;gap:8px;align-items:flex-end}}
.cp-input-area textarea{{
  flex:1;background:#0f172a;color:#e2e8f0;
  border:1px solid #334155;border-radius:8px;
  padding:10px 12px;font-size:13px;
  font-family:inherit;resize:none;
  height:72px;outline:none;
  transition:border-color .15s;
}}
.cp-input-area textarea:focus{{border-color:#3b82f6}}
.send-btn{{
  background:#3b82f6;color:white;border:none;
  border-radius:8px;width:40px;height:40px;
  cursor:pointer;font-size:18px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  transition:background .15s;
}}
.send-btn:hover{{background:#2563eb}}
.send-btn:disabled{{background:#334155;cursor:not-allowed}}
.hint{{font-size:10px;color:#475569;margin-top:6px;text-align:right}}

/* Dashboard cards */
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}
.stat{{background:#1e293b;border-radius:10px;padding:14px 16px;border:1px solid #334155}}
.stat .label{{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}}
.stat .value{{font-size:24px;font-weight:700;color:#f1f5f9}}
.stat .sub{{font-size:10px;color:#64748b;margin-top:2px}}
.grid2{{display:grid;grid-template-columns:2fr 1fr;gap:12px}}
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.card{{background:#1e293b;border-radius:10px;padding:16px 18px;border:1px solid #334155}}
.card h3{{font-size:11px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px}}
canvas{{max-height:170px}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;font-size:10px;font-weight:600;color:#64748b;text-transform:uppercase;padding:7px 10px;border-bottom:1px solid #334155}}
td{{padding:8px 10px;font-size:12px;border-bottom:1px solid #0f172a;color:#cbd5e1;vertical-align:middle}}
tr:hover td{{background:#0f172a;cursor:pointer}}
a{{color:#60a5fa;text-decoration:none}}
.pill{{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:600}}
.ph{{background:#14532d;color:#4ade80}}
.pm{{background:#451a03;color:#fb923c}}
.pl{{background:#450a0a;color:#f87171}}
.ba{{background:#14532d;color:#4ade80;padding:2px 7px;border-radius:999px;font-size:10px}}
.br{{background:#450a0a;color:#f87171;padding:2px 7px;border-radius:999px;font-size:10px}}
.bp{{background:#1e3a5f;color:#93c5fd;padding:2px 7px;border-radius:999px;font-size:10px}}
.gate-blocked{{background:#450a0a;color:#f87171;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}}
.gate-open{{background:#14532d;color:#4ade80;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}}
.empty{{text-align:center;padding:32px;color:#64748b;font-size:13px}}
.ab{{margin-bottom:8px}}
.ab-row{{display:flex;justify-content:space-between;font-size:11px;color:#94a3b8;margin-bottom:3px}}
.ab-track{{background:#334155;border-radius:999px;height:6px}}
.ab-fill{{height:6px;border-radius:999px}}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>🤖 AI Code Review Dashboard</h1>
    <div class="sub">Multi-Agent · RAG-Powered · LangGraph · DevSecOps</div>
  </div>
  <div style="display:flex;align-items:center;gap:8px">
    <span class="badge">v2.0</span>
    <span style="font-size:11px;color:#64748b">{ts}</span>
  </div>
</div>

<div class="layout">

  <!-- ── LEFT: dashboard ─────────────────────────────── -->
  <div class="main">

    <div class="stats">
      <div class="stat"><div class="label">PRs Reviewed</div><div class="value">{stats['total_prs']}</div><div class="sub">total</div></div>
      <div class="stat"><div class="label">Avg Score</div><div class="value" style="color:{sc_color}">{stats['avg_score']:.2f}</div><div class="sub">out of 1.0</div></div>
      <div class="stat"><div class="label">Critical Issues</div><div class="value" style="color:#f87171">{stats['total_critical']}</div><div class="sub">across all PRs</div></div>
      <div class="stat"><div class="label">Approved</div><div class="value" style="color:#4ade80">{stats['approved']}</div><div class="sub">passed review</div></div>
      <div class="stat"><div class="label">Rejected</div><div class="value" style="color:#f87171">{stats['total_prs']-stats['approved']}</div><div class="sub">needs changes</div></div>
    </div>
    <div class="stats" style="margin-top:10px">
      <div class="stat"><div class="label">🔴 PRs Blocked</div><div class="value" style="color:#f87171">{stats.get('blocked_count',0)}</div><div class="sub">gate active</div></div>
      <div class="stat"><div class="label">✅ PRs Cleared</div><div class="value" style="color:#4ade80">{stats.get('cleared_count',0)}</div><div class="sub">gate passed</div></div>
      <div class="stat"><div class="label">🔧 Issues Auto-Resolved</div><div class="value" style="color:#60a5fa">{stats.get('resolved_count',0)}</div><div class="sub">across all PRs</div></div>
      <div class="stat"><div class="label">⏳ Awaiting Fix</div><div class="value" style="color:#f59e0b">{stats.get('blocked_count',0)}</div><div class="sub">PRs pending</div></div>
      <div class="stat"><div class="label">🎯 Gate Success Rate</div><div class="value" style="color:#a78bfa">{"N/A" if not (stats.get('blocked_count',0)+stats.get('cleared_count',0)) else f"{stats.get('cleared_count',0)/(stats.get('blocked_count',0)+stats.get('cleared_count',0))*100:.0f}%"}</div><div class="sub">cleared / total</div></div>
    </div>

    <div class="grid2">
      <div class="card"><h3>📈 Score Trend (Last 20 PRs)</h3><canvas id="trendChart"></canvas></div>
      <div class="card"><h3>🗂️ Issues by Category</h3><canvas id="catChart"></canvas></div>
    </div>

    <div class="grid3">
      <div class="card"><h3>🔐 Security Distribution</h3><canvas id="secChart"></canvas></div>
      <div class="card"><h3>🤖 Agent Score Breakdown</h3><div id="agentBars"></div></div>
      <div class="card"><h3>⚡ Pipeline Stats</h3><div id="pipelineStats" style="font-size:12px;color:#94a3b8;line-height:2.2"></div></div>
    </div>

    <div class="card">
      <h3>📋 Recent PR Reviews</h3>
      <div id="tableContainer"></div>
    </div>

  </div>

  <!-- ── RIGHT: copilot ──────────────────────────────── -->
  <div class="copilot-panel">

    <div class="cp-head">
      <h2>✨ AI Copilot</h2>
      <p>Ask about your code, security issues, fixes, or tests</p>
    </div>

    <div class="cp-msgs" id="chatMsgs">
      <div class="bubble bubble-bot">👋 Hi! I'm your AI code copilot.

I can help you with:
• Explaining security findings
• Generating fixes for issues
• Writing test cases
• Reviewing code quality
• Answering questions about your PRs

Try a quick prompt below or ask anything!</div>
    </div>

    <div class="chips" id="chipsRow">
      <span class="chip" onclick="chip(this)">🔐 Security issues?</span>
      <span class="chip" onclick="chip(this)">🔧 Fix hardcoded secret</span>
      <span class="chip" onclick="chip(this)">🧪 Generate tests</span>
      <span class="chip" onclick="chip(this)">💉 Fix SQL injection</span>
      <span class="chip" onclick="chip(this)">📊 Worst files?</span>
      <span class="chip" onclick="chip(this)">📝 Document login()</span>
    </div>

    <div class="cp-input-area">
      <div class="input-row">
        <textarea id="cpQ" placeholder="Ask anything about your code..."></textarea>
        <button class="send-btn" id="sendBtn" onclick="send()" title="Send (Ctrl+Enter)">➤</button>
      </div>
      <div class="hint">Ctrl+Enter to send</div>
    </div>

  </div>

</div><!-- /layout -->

<script>
const REPORTS={reports_json};
const TREND={trend_data};
const CATS={cat_data};
const AGENTS={agent_data};

// Trend chart
new Chart(document.getElementById('trendChart').getContext('2d'),{{
  type:'line',
  data:{{labels:TREND.map(t=>t.pr),datasets:[{{data:TREND.map(t=>t.score),
    borderColor:'#60a5fa',backgroundColor:'rgba(96,165,250,0.08)',
    tension:0.4,fill:true,pointRadius:4,
    pointBackgroundColor:TREND.map(t=>t.score>=.85?'#4ade80':t.score>=.5?'#fb923c':'#f87171')}}]}},
  options:{{responsive:true,scales:{{
    y:{{min:0,max:1,grid:{{color:'#334155'}},ticks:{{color:'#94a3b8',font:{{size:10}}}}}},
    x:{{grid:{{color:'#334155'}},ticks:{{color:'#94a3b8',maxRotation:45,font:{{size:10}}}}}}
  }},plugins:{{legend:{{display:false}}}}}}
}});

// Doughnut
new Chart(document.getElementById('catChart').getContext('2d'),{{
  type:'doughnut',
  data:{{labels:Object.keys(CATS),datasets:[{{data:Object.values(CATS),
    backgroundColor:['#ef4444','#f59e0b','#3b82f6','#8b5cf6','#10b981','#f97316','#06b6d4']}}]}},
  options:{{responsive:true,plugins:{{legend:{{position:'bottom',
    labels:{{color:'#94a3b8',boxWidth:10,padding:6,font:{{size:10}}}}}}}}}}
}});

// Security bar
const crit=REPORTS.reduce((s,r)=>s+(r.critical||0),0);
const warn=REPORTS.reduce((s,r)=>s+(r.warnings||0),0);
const info=Math.max(0,REPORTS.reduce((s,r)=>s+(r.total-r.critical-r.warnings),0));
new Chart(document.getElementById('secChart').getContext('2d'),{{
  type:'bar',
  data:{{labels:['Critical','Warning','Info'],datasets:[{{data:[crit,warn,info],
    backgroundColor:['#ef4444','#f59e0b','#3b82f6'],borderRadius:4}}]}},
  options:{{responsive:true,scales:{{
    y:{{grid:{{color:'#334155'}},ticks:{{color:'#94a3b8',font:{{size:10}}}}}},
    x:{{grid:{{display:false}},ticks:{{color:'#94a3b8',font:{{size:10}}}}}}
  }},plugins:{{legend:{{display:false}}}}}}
}});

// Agent bars
const ab=document.getElementById('agentBars');
const icons={{security:'🔐',quality:'✨',architecture:'🏗️',performance:'⚡',docs:'📝'}};
const colors={{security:'#ef4444',quality:'#3b82f6',architecture:'#8b5cf6',performance:'#10b981',docs:'#f59e0b'}};
if(!Object.keys(AGENTS).length){{
  ab.innerHTML='<p style="color:#64748b;font-size:12px">Run with multi-agent pipeline to see scores.</p>';
}}else{{
  Object.entries(AGENTS).forEach(([n,s])=>{{
    ab.innerHTML+=`<div class="ab"><div class="ab-row"><span>${{icons[n]||'•'}} ${{n}}</span><span>${{s.toFixed(2)}}</span></div>
    <div class="ab-track"><div class="ab-fill" style="width:${{(s*100).toFixed(0)}}%;background:${{colors[n]||'#60a5fa'}}"></div></div></div>`;
  }});
}}

// Pipeline
const pipes={{}};
REPORTS.forEach(r=>{{pipes[r.pipeline]=(pipes[r.pipeline]||0)+1;}});
document.getElementById('pipelineStats').innerHTML=
  Object.entries(pipes).map(([p,n])=>`<div>🔗 ${{p}}: <strong style="color:#f1f5f9">${{n}} PRs</strong></div>`).join('')||
  '<div style="color:#64748b;font-size:12px">No data yet.</div>';

// Table
const tc=document.getElementById('tableContainer');
if(!REPORTS.length){{
  tc.innerHTML='<div class="empty">No reviews yet. Run <code style="background:#334155;padding:1px 5px;border-radius:3px">python app.py --mock</code></div>';
}}else{{
  const rows=REPORTS.map(r=>{{
    const sc=r.score,cls=sc>=.85?'ph':sc>=.5?'pm':'pl';
    const url=r.repo&&r.pr?`https://github.com/${{r.repo}}/pull/${{r.pr}}`:'#';
    return `<tr onclick="window.open('${{url}}','_blank')">
      <td><a href="${{url}}" target="_blank" onclick="event.stopPropagation()">${{r.repo}}</a></td>
      <td>PR #${{r.pr}}</td>
      <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{r.title}}</td>
      <td><span class="pill ${{cls}}">${{sc.toFixed(2)}}</span></td>
      <td>
        ${{r.gate_blocked===true
          ? '<span class="gate-blocked">🔴 BLOCKED</span>'
          : r.gate_blocked===false
            ? '<span class="gate-open">✅ CLEARED</span>'
            : r.approved
              ? '<span class="ba">✅ OK</span>'
              : '<span class="br">❌ Fix</span>'
        }}
      </td>
      <td style="color:#f87171;font-weight:600">${{r.critical}}</td>
      <td>${{r.warnings}}</td><td>${{r.total}}</td>
      <td><span class="bp">${{r.pipeline}}</span></td>
      <td style="color:#64748b;font-size:11px">${{r.date}}</td>
    </tr>`;
  }}).join('');
  tc.innerHTML=`<table><thead><tr>
    <th>Repo</th><th>PR</th><th>Title</th><th>Score</th><th>Status</th>
    <th>Crit</th><th>Warn</th><th>Total</th><th>Pipeline</th><th>Date</th>
  </tr></thead><tbody>${{rows}}</tbody></table>`;
}}

// ── Copilot chat ──────────────────────────────────────────
const msgsEl = document.getElementById('chatMsgs');

function addBubble(text, cls) {{
  const d = document.createElement('div');
  d.className = `bubble ${{cls}}`;
  d.textContent = text;
  msgsEl.appendChild(d);
  msgsEl.scrollTop = msgsEl.scrollHeight;
  return d;
}}

function chip(el) {{
  document.getElementById('cpQ').value = el.textContent.replace(/^\\S+\\s/,'');
  send();
}}

async function send() {{
  const q = document.getElementById('cpQ').value.trim();
  if (!q) return;
  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  document.getElementById('cpQ').value = '';
  addBubble(q, 'bubble-user');
  const thinking = addBubble('Thinking...', 'bubble-thinking');
  try {{
    const r = await fetch('/api/copilot', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{question:q}})
    }});
    const d = await r.json();
    thinking.remove();
    addBubble(d.answer || 'No response.', 'bubble-bot');
  }} catch(e) {{
    thinking.remove();
    addBubble('Error: ' + e.message, 'bubble-bot');
  }}
  btn.disabled = false;
}}

document.getElementById('cpQ').addEventListener('keydown', e => {{
  if (e.ctrlKey && e.key === 'Enter') send();
}});
</script>
</body></html>"""
    return HTMLResponse(content=html)

if __name__ == "__main__":
    import uvicorn
    import sys
    import os
    # Add project root to path so imports like 'agents.copilot_agent' work
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    # Use app object directly (not string) to avoid module path issues
    uvicorn.run(app, host="127.0.0.1", port=8080, reload=False)