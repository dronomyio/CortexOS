import { useState, useEffect, useRef } from "react";

// ═══════════════════════════════════════════════════════════════════════════
// CortexOS v3.0 — Unified: Architecture · Diagram · Plugin · Dashboard
// 16 agents · Coordinator · Intelligence Layer · Ingest + Upload + Library
// ═══════════════════════════════════════════════════════════════════════════

const C = {
  bg: "#06060b", surface: "#0c0c14", card: "#111119", cardHi: "#16161f",
  border: "#1a1a2a", borderHi: "#252540",
  text: "#e8ecf4", muted: "#6b7a8d", dim: "#3a4555",
  opus: "#f59e0b", opusG: "rgba(245,158,11,0.10)",
  blue: "#3b82f6", blueG: "rgba(59,130,246,0.08)",
  green: "#10b981", greenG: "rgba(16,185,129,0.08)",
  purple: "#a855f7", purpleG: "rgba(168,85,247,0.08)",
  red: "#ef4444", redG: "rgba(239,68,68,0.08)",
  cyan: "#06b6d4", cyanG: "rgba(6,182,212,0.08)",
  rose: "#f43f5e", roseG: "rgba(244,63,94,0.08)",
  lime: "#84cc16", limeG: "rgba(132,204,22,0.08)",
  pink: "#ec4899", pinkG: "rgba(236,72,153,0.08)",
  amber: "#f59e0b", amberG: "rgba(245,158,11,0.08)",
  teal: "#14b8a6", tealG: "rgba(20,184,166,0.08)",
};

const mono = "'JetBrains Mono','SF Mono','Fira Code',monospace";
const sans = "'DM Sans','Inter',sans-serif";

// ─── Mock Data ───────────────────────────────────────────────────────────

const MOCK_VIDEOS = [
  { id:"vid_a1",title:"Tom Lee: ETH to $4,000 by End of Q1",channel:"CNBC Fast Money",duration:"12:34",uploaded:"2026-02-01",status:"indexed",claims:7,contradictions:2,speaker:"Tom Lee",tags:["ETH","price target","Q1"] },
  { id:"vid_b2",title:"Tom Lee: Massive Exchange Outflows Signal Accumulation",channel:"Bloomberg Markets",duration:"08:45",uploaded:"2026-02-03",status:"indexed",claims:5,contradictions:1,speaker:"Tom Lee",tags:["ETH","exchange flows","on-chain"] },
  { id:"vid_c3",title:"Raoul Pal: The Macro Case for Crypto in 2026",channel:"Real Vision",duration:"32:10",uploaded:"2026-02-05",status:"indexed",claims:12,contradictions:0,speaker:"Raoul Pal",tags:["macro","crypto","rates"] },
  { id:"vid_d4",title:"Tom Lee Revises BTC Target to $150K — Why the Shift?",channel:"CNBC Halftime",duration:"06:22",uploaded:"2026-02-08",status:"indexed",claims:4,contradictions:1,speaker:"Tom Lee",tags:["BTC","price target","revision"] },
  { id:"vid_e5",title:"Cathie Wood: ETH Staking Will Transform DeFi",channel:"ARK Invest",duration:"18:55",uploaded:"2026-02-10",status:"processing",claims:0,contradictions:0,speaker:"Cathie Wood",tags:["ETH","staking","DeFi"] },
  { id:"vid_f6",title:"Tom Lee: Volume Declining for Weeks — What It Means",channel:"Fox Business",duration:"10:18",uploaded:"2026-02-12",status:"transcribing",claims:0,contradictions:0,speaker:"Tom Lee",tags:["volume","analysis"] },
];

const MOCK_JOBS = [
  { url:"youtube.com/watch?v=abc123",status:"complete",agent:"video-ingest",elapsed:"2m 34s" },
  { url:"youtube.com/watch?v=def456",status:"complete",agent:"whisper",elapsed:"1m 12s" },
  { url:"youtube.com/watch?v=ghi789",status:"running",agent:"clip-extract",elapsed:"0m 48s" },
];

// ─── Shared Components ───────────────────────────────────────────────────

const Dot = ({ color, s = 5 }) => (
  <span style={{ display:"inline-block",width:s,height:s,borderRadius:"50%",background:color,boxShadow:`0 0 ${s*2}px ${color}40`,animation:"cxPulse 2.5s ease-in-out infinite",flexShrink:0 }}/>
);

const Tag = ({ children, color, bg }) => (
  <span style={{ display:"inline-flex",alignItems:"center",gap:3,padding:"1px 7px",borderRadius:3,fontSize:8,fontWeight:700,fontFamily:mono,letterSpacing:"0.08em",color,background:bg||`${color}15`,border:`1px solid ${color}25`,textTransform:"uppercase",lineHeight:"18px" }}>{children}</span>
);

const SectionLine = ({ children, color = C.dim }) => (
  <div style={{ fontFamily:mono,fontSize:9,fontWeight:700,color,letterSpacing:"0.12em",textTransform:"uppercase",marginBottom:10,display:"flex",alignItems:"center",gap:8 }}>
    <span style={{ flex:1,height:1,background:`linear-gradient(90deg, transparent, ${color}50, transparent)` }}/>
    {children}
    <span style={{ flex:1,height:1,background:`linear-gradient(90deg, transparent, ${color}50, transparent)` }}/>
  </div>
);

const FlowArrow = ({ label, color = C.dim }) => (
  <div style={{ display:"flex",alignItems:"center",gap:6,padding:"4px 0" }}>
    <div style={{ flex:1,height:1,background:`linear-gradient(90deg, transparent, ${color}60, transparent)` }}/>
    <span style={{ fontSize:8,color,fontFamily:mono,letterSpacing:"0.06em" }}>{label}</span>
    <div style={{ flex:1,height:1,background:`linear-gradient(90deg, transparent, ${color}60, transparent)` }}/>
  </div>
);

const AgentCard = ({ icon, name, desc, color, glow, active, onClick, tag, items, isNew }) => (
  <div onClick={onClick} style={{ background:active?(glow||`${color}08`):C.card,border:`1px solid ${active?color:C.border}`,borderRadius:8,padding:"10px 12px",cursor:"pointer",transition:"all 0.2s ease",position:"relative" }}
    onMouseEnter={e=>{e.currentTarget.style.borderColor=`${color}80`;e.currentTarget.style.background=`${color}06`}}
    onMouseLeave={e=>{e.currentTarget.style.borderColor=active?color:C.border;e.currentTarget.style.background=active?(glow||`${color}08`):C.card}}>
    {isNew&&<span style={{position:"absolute",top:-6,right:8,fontSize:7,fontFamily:mono,fontWeight:700,color:C.lime,background:`${C.lime}18`,padding:"1px 5px",borderRadius:3,border:`1px solid ${C.lime}30`,letterSpacing:"0.08em"}}>NEW</span>}
    <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:4}}>
      <span style={{fontSize:14}}>{icon}</span>
      <span style={{fontFamily:mono,fontSize:11,fontWeight:600,color,flex:1}}>{name}</span>
      {tag&&<Tag color={tag.color} bg={tag.bg}>{tag.text}</Tag>}
      {active&&<Dot color={color} s={4}/>}
    </div>
    <p style={{fontSize:10,color:C.muted,margin:0,lineHeight:1.4,fontFamily:sans}}>{desc}</p>
    {active&&items&&(
      <div style={{marginTop:8,paddingTop:6,borderTop:`1px solid ${C.border}`,display:"flex",flexDirection:"column",gap:3}}>
        {items.map((it,i)=>(<div key={i} style={{display:"flex",alignItems:"center",gap:5,fontSize:9,fontFamily:mono}}><span style={{color,opacity:0.6}}>›</span><span style={{color:C.muted}}>{it}</span></div>))}
      </div>
    )}
  </div>
);

const StatusDot = ({ status }) => {
  const m={indexed:{color:C.green,label:"INDEXED"},processing:{color:C.opus,label:"PROCESSING"},transcribing:{color:C.blue,label:"TRANSCRIBING"},failed:{color:C.red,label:"FAILED"},queued:{color:C.dim,label:"QUEUED"}};
  const s=m[status]||m.queued;
  return <div style={{display:"flex",alignItems:"center",gap:4}}><Dot color={s.color} s={4}/><span style={{fontSize:8,fontFamily:mono,fontWeight:600,color:s.color,letterSpacing:"0.06em"}}>{s.label}</span></div>;
};

const VideoThumb = ({ video, onClick }) => {
  const initials=video.speaker.split(" ").map(w=>w[0]).join("");
  const cm={"Tom Lee":C.opus,"Raoul Pal":C.blue,"Cathie Wood":C.purple};
  const accent=cm[video.speaker]||C.cyan;
  return (
    <div onClick={onClick} style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:8,overflow:"hidden",cursor:"pointer",transition:"all 0.2s"}}
      onMouseEnter={e=>{e.currentTarget.style.borderColor=accent;e.currentTarget.style.transform="translateY(-2px)"}}
      onMouseLeave={e=>{e.currentTarget.style.borderColor=C.border;e.currentTarget.style.transform="none"}}>
      <div style={{height:100,background:`linear-gradient(135deg, ${accent}12, ${C.surface})`,display:"flex",alignItems:"center",justifyContent:"center",position:"relative"}}>
        <div style={{width:40,height:40,borderRadius:"50%",background:`${accent}20`,border:`2px solid ${accent}40`,display:"flex",alignItems:"center",justifyContent:"center",fontFamily:mono,fontSize:15,fontWeight:700,color:accent}}>{initials}</div>
        <span style={{position:"absolute",bottom:5,right:5,background:"rgba(0,0,0,0.7)",padding:"2px 5px",borderRadius:3,fontSize:8,fontFamily:mono,color:C.text,fontWeight:600}}>{video.duration}</span>
        <span style={{position:"absolute",top:5,left:5}}><StatusDot status={video.status}/></span>
      </div>
      <div style={{padding:"7px 9px"}}>
        <div style={{fontSize:10,fontWeight:600,color:C.text,lineHeight:1.3,fontFamily:sans,marginBottom:3,display:"-webkit-box",WebkitLineClamp:2,WebkitBoxOrient:"vertical",overflow:"hidden"}}>{video.title}</div>
        <div style={{fontSize:8,color:C.muted,fontFamily:mono,marginBottom:5}}>{video.channel} · {video.uploaded}</div>
        <div style={{display:"flex",gap:3,flexWrap:"wrap"}}>
          {video.status==="indexed"&&<><Tag color={C.green}>{video.claims} claims</Tag>{video.contradictions>0&&<Tag color={C.rose}>{video.contradictions} conflicts</Tag>}</>}
          {video.tags.slice(0,2).map((t,i)=><Tag key={i} color={C.dim}>{t}</Tag>)}
        </div>
      </div>
    </div>
  );
};

// ─── SVG Diagram Components ──────────────────────────────────────────────

const DBox = ({x,y,w,h,label,sub,color,glow,icon,active,onClick,small,isNew})=>{
  const fs=small?10:12,ss=small?8:9;
  return(<g onClick={onClick} style={{cursor:onClick?"pointer":"default"}}>
    {active&&<rect x={x-2} y={y-2} width={w+4} height={h+4} rx={8} fill="none" stroke={color} strokeWidth="1.5" opacity="0.3" filter="url(#glow)"/>}
    <rect x={x} y={y} width={w} height={h} rx={6} fill={active?(glow||`${color}12`):"#0c0c14"} stroke={active?color:"#1a1a2a"} strokeWidth={active?1:0.5}/>
    {isNew&&<><rect x={x+w-28} y={y-4} width={24} height={12} rx={3} fill="#84cc16" opacity="0.15" stroke="#84cc16" strokeWidth="0.5"/><text x={x+w-16} y={y+4} fontSize="6" fill="#84cc16" textAnchor="middle" fontFamily={mono} fontWeight="700">NEW</text></>}
    {icon&&<text x={x+8} y={y+h/2+(sub?-3:1)} fontSize={small?12:14} fill={color} dominantBaseline="middle">{icon}</text>}
    <text x={x+(icon?8+(small?16:20):w/2)} y={y+h/2+(sub?-4:1)} fontSize={fs} fontWeight="600" fill="#e8ecf4" textAnchor={icon?"start":"middle"} dominantBaseline="middle" fontFamily={mono}>{label}</text>
    {sub&&<text x={x+(icon?8+(small?16:20):w/2)} y={y+h/2+8} fontSize={ss} fill="#6b7a8d" textAnchor={icon?"start":"middle"} dominantBaseline="middle" fontFamily={mono}>{sub}</text>}
  </g>);
};

const DArrow = ({points,color="#3a4555",dashed,label,lx,ly})=>(<g><polyline points={points} fill="none" stroke={color} strokeWidth="1" strokeDasharray={dashed?"4,3":"none"} markerEnd="url(#ah)" opacity="0.7"/>{label&&<text x={lx} y={ly} fontSize="7" fill={`${color}90`} textAnchor="middle" fontFamily={mono}>{label}</text>}</g>);

const DLabel = ({x,y,text,color})=>(<text x={x} y={y} fontSize="8" fontWeight="700" fill={color} textAnchor="start" fontFamily={mono} letterSpacing="1.5">{text}</text>);

// ═══════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════════

export default function CortexOS() {
  // ─── Global state
  const [active, setActive] = useState(null);
  const [flowIdx, setFlowIdx] = useState(0);
  const [running, setRunning] = useState(false);
  const [view, setView] = useState("dashboard");
  const [archPhase, setArchPhase] = useState("coordinator");

  // ─── Dashboard state
  const [urlInput, setUrlInput] = useState("");
  const [batchUrls, setBatchUrls] = useState("");
  const [ingestMode, setIngestMode] = useState("single");
  const [videos, setVideos] = useState(MOCK_VIDEOS);
  const [selectedVideo, setSelectedVideo] = useState(null);
  const [ingestLog, setIngestLog] = useState([]);
  const [isIngesting, setIsIngesting] = useState(false);
  const [filter, setFilter] = useState("all");
  const [searchQ, setSearchQ] = useState("");
  const [uploadFiles, setUploadFiles] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const logRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => { const t = setInterval(() => setFlowIdx(p => (p+1) % 9), 2000); return () => clearInterval(t); }, []);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [ingestLog]);

  // ─── Demo
  const runDemo = () => {
    setRunning(true);
    const seq = ["coordinator","opus-planner","video-ingest","fact-verifier","intelligence","synthesis","x402","observability","mcp-cortexos"];
    seq.forEach((id, i) => setTimeout(() => setActive(id), i * 800));
    setTimeout(() => { setActive(null); setRunning(false); }, seq.length * 800 + 500);
  };

  // ─── Upload handlers
  const handleFiles = (files) => {
    const vf = Array.from(files).filter(f => f.type.startsWith("video/") || /\.(mp4|mkv|avi|mov|webm|flv)$/i.test(f.name));
    if (vf.length > 0) setUploadFiles(prev => [...prev, ...vf]);
  };
  const removeFile = (idx) => setUploadFiles(prev => prev.filter((_, i) => i !== idx));
  const fmtSize = (b) => b < 1024*1024 ? `${(b/1024).toFixed(0)} KB` : `${(b/(1024*1024)).toFixed(1)} MB`;
  const handleDragOver = (e) => { e.preventDefault(); setIsDragging(true); };
  const handleDragLeave = (e) => { e.preventDefault(); setIsDragging(false); };
  const handleDrop = (e) => { e.preventDefault(); setIsDragging(false); if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files); };

  // ─── Ingest (all modes)
  const handleIngest = () => {
    let items = [];
    if (ingestMode === "upload") {
      if (!uploadFiles.length) return;
      items = uploadFiles.map(f => ({ name: f.name, size: fmtSize(f.size), type: "file" }));
    } else if (ingestMode === "single") {
      if (!urlInput.trim()) return;
      items = [{ name: urlInput.trim(), type: "url" }];
    } else {
      items = batchUrls.split("\n").map(u => u.trim()).filter(Boolean).map(u => ({ name: u, type: "url" }));
      if (!items.length) return;
    }
    setIsIngesting(true);
    const ts = () => new Date().toLocaleTimeString();
    const isFile = ingestMode === "upload";
    setIngestLog(prev => [...prev, `[${ts()}] ▸ Starting ${isFile?"upload":"ingest"} for ${items.length} ${isFile?"file":"URL"}(s)...`]);
    items.forEach((item, i) => {
      const label = item.name.length > 42 ? item.name.substring(0,42)+"..." : item.name;
      const base = isFile ? i*1500+2500 : i*1200+2000;
      if (isFile) {
        setTimeout(() => setIngestLog(prev => [...prev, `[${ts()}] ▸ Uploading: ${label} (${item.size})`]), i*1500);
        setTimeout(() => setIngestLog(prev => [...prev, `[${ts()}] ✓ Upload complete — extracting audio`]), i*1500+1800);
      } else {
        setTimeout(() => setIngestLog(prev => [...prev, `[${ts()}] ▸ Downloading: ${label}`]), i*1200);
      }
      setTimeout(() => setIngestLog(prev => [...prev, `[${ts()}] ✓ Whisper transcription complete`]), base);
      setTimeout(() => setIngestLog(prev => [...prev, `[${ts()}] ✓ Opus plan: investigative strategy`]), base+1200);
      setTimeout(() => setIngestLog(prev => [...prev, `[${ts()}] ✓ CLIP keyframes (${Math.floor(Math.random()*20+5)} frames)`]), base+2000);
      setTimeout(() => {
        setIngestLog(prev => [...prev, `[${ts()}] ✓ Weaviate indexed — ${Math.floor(Math.random()*40+10)} chunks`]);
        setVideos(prev => [{ id:`vid_${Math.random().toString(36).substr(2,6)}`, title:isFile?item.name.replace(/\.[^.]+$/,""):`Video from ${item.name.substring(0,30)}`, channel:isFile?"Local Upload":"YouTube", duration:`${Math.floor(Math.random()*25+5)}:${String(Math.floor(Math.random()*60)).padStart(2,"0")}`, uploaded:new Date().toISOString().split("T")[0], status:"indexed", claims:Math.floor(Math.random()*8+2), contradictions:Math.floor(Math.random()*3), speaker:"Unknown", tags:isFile?["uploaded"]:["youtube"] }, ...prev]);
        if (i === items.length-1) { setIngestLog(prev => [...prev, `[${ts()}] ══ Complete: ${items.length} video(s) processed`]); setIsIngesting(false); }
      }, base+3000);
    });
    setUrlInput(""); setBatchUrls(""); setUploadFiles([]);
  };

  // ─── Data
  const coreAgents = [
    { id:"coordinator",icon:"🎯",name:"agent-coordinator",color:C.opus,glow:C.opusG,desc:"Opus 4.6 team lead — plans, assigns parallel tasks, monitors, re-plans",tag:{text:"Opus 4.6",color:C.opus,bg:C.opusG},isNew:true,items:["5-phase mission orchestration","Parallel (5 concurrent)","Re-plan on failure","Conflict resolution"] },
    { id:"opus-planner",icon:"🧠",name:"opus-planner",color:C.amber,glow:C.amberG,desc:"Per-video strategy — skip filler, prioritize claims",tag:{text:"Opus 4.6",color:C.opus,bg:C.opusG},items:["plan_ingest() → skip filler","plan_synthesis() → investigative","Categorize video types"] },
    { id:"intelligence",icon:"🔬",name:"intelligence-layer",color:C.purple,glow:C.purpleG,desc:"Contradictions, speaker scorecards, external data cross-ref",tag:{text:"Opus 4.6",color:C.opus,bg:C.opusG},isNew:true,items:["find_contradictions()","speaker_scorecard()","cross_reference_external()"] },
    { id:"video-ingest",icon:"📹",name:"video-ingest",color:C.blue,glow:C.blueG,desc:"Download → Whisper → CLIP → Weaviate index",tag:{text:"Worker",color:C.blue,bg:C.blueG},items:["yt-dlp + time-range","Whisper CPU/GPU","CLIP keyframes"] },
    { id:"fact-verifier",icon:"✓",name:"fact-verifier",color:C.green,glow:C.greenG,desc:"Extract claims → verify → cross-reference → verdict",tag:{text:"Worker",color:C.green,bg:C.greenG},items:["Opus claim extraction","Weaviate cross-video","CONFIRMED|CONTRADICTED|STALE"] },
    { id:"synthesis",icon:"✨",name:"synthesis-agent",color:C.cyan,glow:C.cyanG,desc:"Cited answers with timestamped evidence",tag:{text:"Worker",color:C.cyan,bg:C.cyanG},items:["Weaviate hybrid search","Opus synthesis","Enrichment on-demand"] },
  ];
  const infraAgents = [
    { id:"x402",icon:"💳",name:"x402-middleware",color:C.red,glow:C.redG,desc:"Payment gate — 402 → pay → token",tag:{text:"x402",color:C.red,bg:C.redG},items:["6 priced ($0.01–$0.25)","25 free","Circle Wallets on Arc"] },
    { id:"x402-pay",icon:"💰",name:"x402-payment-agent",color:C.rose,glow:C.roseG,desc:"Pay upstream, guardrails, ledger",items:["Per-tx + daily limits","Audit trail"] },
    { id:"observability",icon:"📊",name:"observability",color:C.purple,glow:C.purpleG,desc:"Opik tracing, cost, quality scoring",tag:{text:"Opik",color:C.purple,bg:C.purpleG},items:["@trace decorators","Quality scoring"] },
    { id:"video-qa",icon:"❓",name:"video-qa",color:C.teal,glow:C.tealG,desc:"Q&A over video corpus",items:["POST /qa/ask ($0.02)","Opus-grounded"] },
  ];
  const missionFlow = [
    {l:"Mission received",s:"URLs + data + speaker filter",c:C.text},{l:"Opus 4.6 plans",s:"Categorize, prioritize, strategy",c:C.opus},
    {l:"Parallel ingestion",s:"5 concurrent · retry · skip broken",c:C.blue},{l:"Whisper + CLIP",s:"Transcribe, keyframes, index",c:C.cyan},
    {l:"Claim extraction",s:"Opus extracts verifiable claims",c:C.green},{l:"Fact verification",s:"Web + Weaviate cross-ref",c:C.green},
    {l:"Contradiction scan",s:"Self + cross-speaker + data",c:C.rose},{l:"Speaker scorecards",s:"Accuracy · grade · patterns",c:C.purple},
    {l:"Opus synthesis",s:"Resolve conflicts → final report",c:C.opus},
  ];
  const archPhases = [
    {id:"coordinator",title:"COORDINATOR",desc:"Opus 4.6 as team lead — plans, assigns, monitors, re-plans, resolves conflicts."},
    {id:"ingest",title:"INGESTION",desc:"Parallel video processing — download → Whisper → CLIP → Weaviate. 5 concurrent with retry."},
    {id:"intelligence",title:"INTELLIGENCE",desc:"Contradictions, speaker scorecards, external data cross-reference. Opus reasons about conflicts."},
    {id:"x402",title:"x402 PAYMENTS",desc:"CortexOS as server + client. 6 priced, 25 free. MongoDB audit. Circle Wallets on Arc."},
    {id:"mcp",title:"MCP + PLUGIN",desc:"17 MCP tools, 7 agent docs, 3 skills, 2 hooks. Auto-discovered at startup."},
  ];

  const filtered = videos.filter(v => {
    if (filter==="indexed"&&v.status!=="indexed") return false;
    if (filter==="processing"&&v.status==="indexed") return false;
    if (filter==="contradictions"&&v.contradictions===0) return false;
    if (searchQ&&!v.title.toLowerCase().includes(searchQ.toLowerCase())&&!v.speaker.toLowerCase().includes(searchQ.toLowerCase())) return false;
    return true;
  });
  const totalClaims=videos.reduce((s,v)=>s+v.claims,0);
  const totalContra=videos.reduce((s,v)=>s+v.contradictions,0);

  const W=960,H=660,ap=archPhase;

  return (
    <div style={{background:C.bg,minHeight:"100vh",padding:"24px 16px",fontFamily:sans,color:C.text}}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
        @keyframes cxPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(1.6)}}
        @keyframes kernelGlow{0%,100%{box-shadow:0 0 20px rgba(245,158,11,0.03)}50%{box-shadow:0 0 50px rgba(245,158,11,0.12)}}
        @keyframes slideIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
        @keyframes termBlink{0%,100%{opacity:1}50%{opacity:0}}
        *{box-sizing:border-box}input,textarea{outline:none}
        input::placeholder,textarea::placeholder{color:${C.dim}}
        ::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:${C.surface}}::-webkit-scrollbar-thumb{background:${C.border};border-radius:2px}
      `}</style>

      <div style={{maxWidth:1100,margin:"0 auto"}}>

        {/* HEADER */}
        <div style={{textAlign:"center",marginBottom:24}}>
          <div style={{display:"inline-flex",alignItems:"center",gap:8,padding:"4px 14px",background:`${C.opus}0a`,border:`1px solid ${C.opus}20`,borderRadius:100,marginBottom:10}}>
            <Dot color={C.opus} s={6}/><span style={{fontFamily:mono,fontSize:10,fontWeight:700,color:C.opus,letterSpacing:"0.1em"}}>AGENT OPERATING SYSTEM</span>
          </div>
          <h1 style={{fontFamily:mono,fontSize:42,fontWeight:700,margin:"0 0 4px",letterSpacing:"-0.03em",background:`linear-gradient(135deg, ${C.text} 20%, ${C.opus})`,WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent"}}>CortexOS</h1>
          <p style={{fontSize:12,color:C.muted,margin:"0 0 14px",lineHeight:1.5,maxWidth:580,marginLeft:"auto",marginRight:"auto"}}>Opus 4.6 coordinates the team. 16 self-discovering agents. Videos in, verified intelligence out.</p>

          {/* TAB BAR — 4 tabs */}
          <div style={{display:"inline-flex",background:C.surface,borderRadius:6,border:`1px solid ${C.border}`,overflow:"hidden"}}>
            {[{k:"dashboard",l:"📹 Dashboard"},{k:"architecture",l:"🏗 Architecture"},{k:"diagram",l:"📐 Diagram"},{k:"plugin",l:"🔌 Plugin"}].map((v,i,a)=>(
              <button key={v.k} onClick={()=>{setView(v.k);setActive(null)}} style={{
                fontFamily:mono,fontSize:10,fontWeight:600,padding:"7px 14px",
                background:view===v.k?`${C.opus}12`:"transparent",color:view===v.k?C.opus:C.muted,
                border:"none",cursor:"pointer",transition:"all 0.2s",
                borderRight:i<a.length-1?`1px solid ${C.border}`:"none",
              }}>{v.l}</button>
            ))}
          </div>
        </div>

        {/* ════════════════════════════════════════════════════════════════════ */}
        {/* DASHBOARD VIEW                                                      */}
        {/* ════════════════════════════════════════════════════════════════════ */}
        {view === "dashboard" && (
          <div style={{animation:"slideIn 0.3s ease"}}>
            {/* Stats bar */}
            <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:10,marginBottom:16}}>
              {[{l:"Videos",v:videos.length,c:C.blue},{l:"Indexed",v:videos.filter(v=>v.status==="indexed").length,c:C.green},{l:"Claims",v:totalClaims,c:C.cyan},{l:"Contradictions",v:totalContra,c:C.rose},{l:"Speakers",v:[...new Set(videos.map(v=>v.speaker))].length,c:C.purple}].map((s,i)=>(
                <div key={i} style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:8,padding:"8px 10px",textAlign:"center"}}>
                  <div style={{fontFamily:mono,fontSize:20,fontWeight:700,color:s.c}}>{s.v}</div>
                  <div style={{fontFamily:mono,fontSize:7,color:C.muted,letterSpacing:"0.08em",textTransform:"uppercase"}}>{s.l}</div>
                </div>
              ))}
            </div>

            <div style={{display:"grid",gridTemplateColumns:"320px 1fr",gap:14}}>
              {/* LEFT: INGEST */}
              <div style={{display:"flex",flexDirection:"column",gap:10}}>
                <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:12}}>
                  <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:8}}>
                    <span style={{fontSize:14}}>📹</span>
                    <span style={{fontFamily:mono,fontSize:12,fontWeight:700,color:C.opus}}>Ingest Video</span>
                  </div>
                  {/* Mode toggle */}
                  <div style={{display:"flex",marginBottom:8,background:C.surface,borderRadius:4,border:`1px solid ${C.border}`,overflow:"hidden"}}>
                    {["single","batch","upload"].map(m=>(
                      <button key={m} onClick={()=>setIngestMode(m)} style={{flex:1,padding:"5px 0",fontFamily:mono,fontSize:8,fontWeight:600,background:ingestMode===m?`${C.opus}12`:"transparent",color:ingestMode===m?C.opus:C.muted,border:"none",cursor:"pointer",textTransform:"uppercase",letterSpacing:"0.06em"}}>{m==="single"?"URL":m==="batch"?"Batch":"Upload"}</button>
                    ))}
                  </div>

                  {ingestMode==="single"?(
                    <input value={urlInput} onChange={e=>setUrlInput(e.target.value)} onKeyDown={e=>e.key==="Enter"&&handleIngest()} placeholder="https://youtube.com/watch?v=..." style={{width:"100%",padding:"7px 9px",fontFamily:mono,fontSize:10,background:C.surface,border:`1px solid ${C.border}`,borderRadius:5,color:C.text,marginBottom:6}}/>
                  ):ingestMode==="batch"?(
                    <textarea value={batchUrls} onChange={e=>setBatchUrls(e.target.value)} placeholder={"One URL per line:\nhttps://youtube.com/watch?v=abc\nhttps://youtube.com/watch?v=def"} rows={4} style={{width:"100%",padding:"7px 9px",fontFamily:mono,fontSize:9,background:C.surface,border:`1px solid ${C.border}`,borderRadius:5,color:C.text,marginBottom:6,resize:"vertical",lineHeight:1.6}}/>
                  ):(
                    <div style={{marginBottom:6}}>
                      <input ref={fileInputRef} type="file" accept="video/*,.mp4,.mkv,.avi,.mov,.webm" multiple onChange={e=>{handleFiles(e.target.files);e.target.value=""}} style={{display:"none"}}/>
                      <div onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop} onClick={()=>fileInputRef.current?.click()}
                        style={{border:`2px dashed ${isDragging?C.opus:C.border}`,borderRadius:7,padding:uploadFiles.length?"10px":"16px 10px",background:isDragging?`${C.opus}08`:C.surface,cursor:"pointer",transition:"all 0.2s",textAlign:"center"}}>
                        {uploadFiles.length===0?(
                          <><div style={{fontSize:22,marginBottom:4,opacity:isDragging?1:0.5}}>{isDragging?"📥":"🎬"}</div>
                          <div style={{fontFamily:mono,fontSize:9,color:isDragging?C.opus:C.muted,marginBottom:3}}>{isDragging?"Drop videos here":"Drag & drop video files"}</div>
                          <div style={{fontFamily:mono,fontSize:7,color:C.dim}}>or click to browse · MP4, MKV, AVI, MOV, WebM</div></>
                        ):(
                          <div style={{textAlign:"left"}} onClick={e=>e.stopPropagation()}>
                            {uploadFiles.map((f,i)=>(
                              <div key={i} style={{display:"flex",alignItems:"center",gap:5,padding:"3px 5px",borderRadius:3,background:C.card,border:`1px solid ${C.border}`,marginBottom:3}}>
                                <span style={{fontSize:11}}>🎬</span>
                                <div style={{flex:1,minWidth:0}}>
                                  <div style={{fontFamily:mono,fontSize:8,color:C.text,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{f.name}</div>
                                  <div style={{fontFamily:mono,fontSize:7,color:C.dim}}>{fmtSize(f.size)}</div>
                                </div>
                                <button onClick={e=>{e.stopPropagation();removeFile(i)}} style={{background:"none",border:"none",cursor:"pointer",fontFamily:mono,fontSize:9,color:C.red,padding:"1px 3px",borderRadius:2}}>✕</button>
                              </div>
                            ))}
                            <div onClick={()=>fileInputRef.current?.click()} style={{marginTop:4,textAlign:"center",fontFamily:mono,fontSize:7,color:C.opus,cursor:"pointer",padding:"3px 0",borderRadius:3,border:`1px dashed ${C.opus}30`}}>+ Add more</div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  <button onClick={handleIngest} disabled={isIngesting} style={{width:"100%",padding:"8px 0",fontFamily:mono,fontSize:10,fontWeight:700,background:isIngesting?C.surface:`linear-gradient(135deg, ${C.opus}, #d97706)`,color:isIngesting?C.dim:"#000",border:`1px solid ${isIngesting?C.border:C.opus}`,borderRadius:5,cursor:isIngesting?"default":"pointer",letterSpacing:"0.04em",transition:"all 0.2s"}}>
                    {isIngesting?"⟳ Processing...":ingestMode==="upload"?`▶ Upload${uploadFiles.length?` (${uploadFiles.length})`:""}`:ingestMode==="batch"?"▶ Ingest Batch":"▶ Ingest"}
                  </button>
                </div>

                {/* Terminal Log */}
                <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,flex:1,display:"flex",flexDirection:"column",minHeight:180}}>
                  <div style={{padding:"6px 10px",borderBottom:`1px solid ${C.border}`,display:"flex",alignItems:"center",gap:5}}>
                    <div style={{width:7,height:7,borderRadius:"50%",background:C.red,opacity:0.6}}/><div style={{width:7,height:7,borderRadius:"50%",background:C.opus,opacity:0.6}}/><div style={{width:7,height:7,borderRadius:"50%",background:C.green,opacity:0.6}}/>
                    <span style={{fontFamily:mono,fontSize:8,color:C.dim,marginLeft:6}}>agent-log</span>
                  </div>
                  <div ref={logRef} style={{flex:1,padding:"6px 10px",overflowY:"auto",maxHeight:220}}>
                    {ingestLog.length===0?(
                      <div style={{fontFamily:mono,fontSize:9,color:C.dim,lineHeight:1.8}}><span style={{color:C.green}}>$</span> Waiting for ingest...<span style={{animation:"termBlink 1s infinite",color:C.opus}}>▌</span></div>
                    ):ingestLog.map((line,i)=>(
                      <div key={i} style={{fontFamily:mono,fontSize:8,lineHeight:1.7,color:line.includes("✓")?C.green:line.includes("══")?C.opus:C.muted}}>{line}</div>
                    ))}
                  </div>
                </div>

                {/* Active Jobs */}
                <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:8,padding:10}}>
                  <div style={{fontFamily:mono,fontSize:8,fontWeight:700,color:C.dim,letterSpacing:"0.1em",textTransform:"uppercase",marginBottom:6}}>Active Jobs</div>
                  {MOCK_JOBS.map((j,i)=>(
                    <div key={i} style={{display:"flex",alignItems:"center",gap:5,padding:"3px 0",borderBottom:i<MOCK_JOBS.length-1?`1px solid ${C.border}`:"none"}}>
                      <Dot color={j.status==="running"?C.opus:C.green} s={3}/>
                      <span style={{fontFamily:mono,fontSize:8,color:C.muted,flex:1}}>{j.url.substring(0,26)}...</span>
                      <Tag color={j.status==="running"?C.opus:C.green}>{j.agent}</Tag>
                      <span style={{fontFamily:mono,fontSize:7,color:C.dim}}>{j.elapsed}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* RIGHT: VIDEO LIBRARY */}
              <div>
                <div style={{display:"flex",gap:6,marginBottom:10,alignItems:"center"}}>
                  <div style={{flex:1,position:"relative"}}>
                    <span style={{position:"absolute",left:8,top:"50%",transform:"translateY(-50%)",fontSize:11,color:C.dim}}>🔍</span>
                    <input value={searchQ} onChange={e=>setSearchQ(e.target.value)} placeholder="Search videos, speakers..." style={{width:"100%",padding:"7px 8px 7px 26px",fontFamily:mono,fontSize:10,background:C.card,border:`1px solid ${C.border}`,borderRadius:5,color:C.text}}/>
                  </div>
                  {["all","indexed","processing","contradictions"].map(f=>(
                    <button key={f} onClick={()=>setFilter(f)} style={{padding:"5px 8px",fontFamily:mono,fontSize:8,fontWeight:600,background:filter===f?`${C.opus}12`:C.card,color:filter===f?C.opus:C.muted,border:`1px solid ${filter===f?C.opus:C.border}`,borderRadius:4,cursor:"pointer",textTransform:"capitalize",letterSpacing:"0.04em"}}>{f}</button>
                  ))}
                </div>

                <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:10}}>
                  {filtered.map(v=><VideoThumb key={v.id} video={v} onClick={()=>setSelectedVideo(selectedVideo?.id===v.id?null:v)}/>)}
                </div>
                {filtered.length===0&&<div style={{textAlign:"center",padding:"30px 0",color:C.dim,fontFamily:mono,fontSize:10}}>No videos match filter.</div>}

                {/* Selected Video Detail */}
                {selectedVideo&&(
                  <div style={{marginTop:12,background:C.card,border:`1px solid ${C.borderHi}`,borderRadius:8,padding:14,animation:"slideIn 0.2s ease"}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:8}}>
                      <div>
                        <div style={{fontFamily:sans,fontSize:13,fontWeight:700,color:C.text,marginBottom:3}}>{selectedVideo.title}</div>
                        <div style={{fontFamily:mono,fontSize:9,color:C.muted}}>{selectedVideo.channel} · {selectedVideo.speaker} · {selectedVideo.uploaded}</div>
                      </div>
                      <StatusDot status={selectedVideo.status}/>
                    </div>
                    <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:8,marginBottom:10}}>
                      {[{l:"Claims",v:selectedVideo.claims,c:C.cyan},{l:"Contradictions",v:selectedVideo.contradictions,c:C.rose},{l:"Duration",v:selectedVideo.duration,c:C.blue},{l:"Status",v:selectedVideo.status,c:C.green}].map((s,i)=>(
                        <div key={i} style={{background:C.surface,borderRadius:5,padding:"6px 8px",textAlign:"center"}}>
                          <div style={{fontFamily:mono,fontSize:14,fontWeight:700,color:s.c}}>{s.v}</div>
                          <div style={{fontFamily:mono,fontSize:7,color:C.dim,letterSpacing:"0.06em",textTransform:"uppercase"}}>{s.l}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{display:"flex",gap:6}}>
                      {[{l:"✓ Verify Claims",c:C.green},{l:"⚠ Contradictions",c:C.rose},{l:"📊 Speaker Score",c:C.purple}].map((b,i)=>(
                        <button key={i} style={{flex:1,padding:"7px 0",fontFamily:mono,fontSize:9,fontWeight:600,background:`${b.c}12`,color:b.c,border:`1px solid ${b.c}30`,borderRadius:4,cursor:"pointer"}}>{b.l}</button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ════════════════════════════════════════════════════════════════════ */}
        {/* ARCHITECTURE VIEW                                                   */}
        {/* ════════════════════════════════════════════════════════════════════ */}
        {view === "architecture" && (
          <div style={{animation:"slideIn 0.3s ease"}}>
            <div style={{background:`linear-gradient(135deg, ${C.opus}08, ${C.surface})`,border:`1px solid ${C.opus}25`,borderRadius:12,padding:16,marginBottom:12,textAlign:"center",animation:"kernelGlow 4s ease-in-out infinite",position:"relative"}}>
              <div style={{position:"absolute",top:8,right:12,fontFamily:mono,fontSize:8,color:C.opus,opacity:0.4,letterSpacing:"0.1em"}}>KERNEL / TEAM LEAD</div>
              <div style={{fontSize:22,marginBottom:3}}>🎯</div>
              <div style={{fontFamily:mono,fontSize:15,fontWeight:700,color:C.opus,marginBottom:2}}>Agent Coordinator — Opus 4.6</div>
              <p style={{fontSize:10,color:C.muted,margin:"0 0 8px"}}>Plans missions · assigns parallel tasks · monitors · re-plans · resolves conflicts</p>
              <div style={{display:"flex",justifyContent:"center",gap:5,flexWrap:"wrap"}}>
                {["Planning","Ingestion","Verification","Analysis","Synthesis"].map((l,i)=>(
                  <span key={i} style={{fontSize:8,fontFamily:mono,padding:"2px 7px",borderRadius:3,background:`${C.opus}10`,border:`1px solid ${C.opus}20`,color:C.opus}}>Phase {i+1}: {l}</span>
                ))}
              </div>
            </div>
            <FlowArrow label="delegates to agent teams" color={C.opus}/>
            <SectionLine color={C.opus}>Core Agents — Intelligence Pipeline</SectionLine>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:10,marginBottom:14}}>
              {coreAgents.map(a=><AgentCard key={a.id} {...a} active={active===a.id} onClick={()=>setActive(active===a.id?null:a.id)}/>)}
            </div>
            <FlowArrow label="supported by" color={C.dim}/>
            <SectionLine color={C.red}>Infrastructure — Payments · Observability · QA</SectionLine>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr 1fr",gap:10,marginBottom:14}}>
              {infraAgents.map(a=><AgentCard key={a.id} {...a} active={active===a.id} onClick={()=>setActive(active===a.id?null:a.id)}/>)}
            </div>
            <FlowArrow label="mission execution flow" color={C.opus}/>
            <div style={{display:"grid",gridTemplateColumns:"1.2fr 1fr",gap:12}}>
              <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:12}}>
                <SectionLine color={C.opus}>Coordinated Mission Flow</SectionLine>
                <div style={{display:"flex",flexDirection:"column",gap:5}}>
                  {missionFlow.map((s,i)=>(
                    <div key={i} style={{display:"flex",alignItems:"center",gap:6,opacity:i<=flowIdx?1:0.2,transition:`all 0.3s ease ${i*60}ms`,transform:i<=flowIdx?"none":"translateX(-4px)"}}>
                      <div style={{width:18,height:18,borderRadius:3,background:i<=flowIdx?s.c:C.surface,border:`1px solid ${i<=flowIdx?s.c:C.border}`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:8,fontWeight:700,color:i<=flowIdx?"#000":C.dim,fontFamily:mono,flexShrink:0}}>{i+1}</div>
                      <div><div style={{fontSize:9,fontWeight:600,color:i<=flowIdx?C.text:C.dim,fontFamily:mono}}>{s.l}</div><div style={{fontSize:7,color:C.dim}}>{s.s}</div></div>
                    </div>
                  ))}
                </div>
              </div>
              <div style={{display:"flex",flexDirection:"column",gap:10}}>
                <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:12,flex:1}}>
                  <SectionLine color={C.green}>System Stats</SectionLine>
                  {[{l:"Agents",v:"16",c:C.green},{l:"Registered",v:"16/16",c:C.green},{l:"Endpoints",v:"31",c:C.blue},{l:"Priced",v:"6",c:C.red},{l:"Free",v:"25",c:C.green},{l:"MCP tools",v:"17",c:C.purple}].map((s,i)=>(
                    <div key={i} style={{display:"flex",justifyContent:"space-between",padding:"2px 0",borderBottom:`1px solid ${C.border}`}}>
                      <span style={{fontSize:9,color:C.muted,fontFamily:mono}}>{s.l}</span><span style={{fontSize:10,color:s.c,fontFamily:mono,fontWeight:700}}>{s.v}</span>
                    </div>
                  ))}
                </div>
                <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:12}}>
                  <SectionLine color={C.rose}>Intelligence Output</SectionLine>
                  {[{i:"⚠️",t:"Contradiction: BTC $200K→$150K (9 days)"},{i:"📊",t:"Tom Lee: 43% accuracy · Grade C+"},{i:"📉",t:"Claimed 'outflows' on +45K ETH inflow day"},{i:"⏰",t:"Stale: volume claim disproved in 3 days"}].map((a,i)=>(
                    <div key={i} style={{display:"flex",gap:4,marginBottom:3,fontSize:8,color:C.muted,lineHeight:1.4}}><span style={{flexShrink:0}}>{a.i}</span><span>{a.t}</span></div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ════════ DIAGRAM VIEW ════════ */}
        {view === "diagram" && (
          <div style={{animation:"slideIn 0.3s ease"}}>
            <div style={{display:"flex",gap:6,marginBottom:10,flexWrap:"wrap"}}>
              {archPhases.map(p=><button key={p.id} onClick={()=>setArchPhase(p.id)} style={{background:ap===p.id?`${C.opus}12`:C.surface,border:`1px solid ${ap===p.id?C.opus:C.border}`,color:ap===p.id?C.opus:C.muted,padding:"5px 9px",borderRadius:4,fontSize:8,fontWeight:600,cursor:"pointer",fontFamily:mono,letterSpacing:"0.05em"}}>{p.title}</button>)}
            </div>
            <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,padding:"6px 10px",marginBottom:10,fontSize:9,color:C.muted,lineHeight:1.5}}>{archPhases.find(p=>p.id===ap)?.desc}</div>
            <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{background:C.bg,borderRadius:8,border:`1px solid ${C.border}`}}>
              <defs><marker id="ah" markerWidth="7" markerHeight="5" refX="6" refY="2.5" orient="auto"><polygon points="0 0, 7 2.5, 0 5" fill="#3a4555"/></marker><filter id="glow"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
              <DLabel x={20} y={25} text="API LAYER" color={C.blue}/>
              <DBox x={20} y={32} w={100} h={36} label="User/Agent" sub="HTTP" icon="👤" color={C.blue} glow={C.blueG} active/>
              <DBox x={130} y={32} w={115} h={36} label="/coordinator" sub="mission" icon="🎯" color={C.opus} glow={C.opusG} active={ap==="coordinator"} isNew/>
              <DBox x={255} y={32} w={95} h={36} label="/ingest" sub="url" icon="📹" color={C.blue} glow={C.blueG} active={ap==="ingest"}/>
              <DBox x={360} y={32} w={95} h={36} label="/verify" sub="$0.03" icon="✓" color={C.green} glow={C.greenG} active={ap==="intelligence"}/>
              <DBox x={465} y={32} w={105} h={36} label="/synthesize" sub="$0.03" icon="✨" color={C.cyan} glow={C.cyanG} active={ap==="intelligence"}/>
              <DBox x={580} y={32} w={125} h={36} label="/contradictions" sub="find" icon="🔬" color={C.purple} glow={C.purpleG} active={ap==="intelligence"} isNew/>
              <DBox x={715} y={32} w={115} h={36} label="/speakers" sub="scorecard" icon="📊" color={C.purple} glow={C.purpleG} active={ap==="intelligence"} isNew/>
              <DBox x={840} y={32} w={100} h={36} label="/qa/ask" sub="$0.02" icon="❓" color={C.teal} glow={C.tealG} active={ap==="intelligence"}/>
              <DArrow points="120,50 130,50" color={C.blue}/>
              <DLabel x={20} y={92} text="AGENT COORDINATOR — OPUS 4.6 TEAM LEAD" color={C.opus}/>
              <DBox x={20} y={100} w={920} h={46} label="AgentCoordinator" sub="plan → ingest → verify → analyze → synthesize" color={C.opus} glow={C.opusG} active={ap==="coordinator"} onClick={()=>setArchPhase("coordinator")} isNew/>
              <DLabel x={20} y={172} text="INGESTION" color={C.blue}/>
              <DBox x={20} y={180} w={180} h={38} label="VideoIngest" sub="video_ingest_agent.py" icon="📹" color={C.blue} glow={C.blueG} active={ap==="ingest"} onClick={()=>setArchPhase("ingest")}/>
              <DBox x={20} y={226} w={180} h={30} label="Whisper" sub="Transcribe" color={C.blue} glow={C.blueG} active={ap==="ingest"} small/>
              <DBox x={20} y={262} w={180} h={30} label="CLIP Keyframes" sub="Opus-selective" color={C.blue} glow={C.blueG} active={ap==="ingest"} small/>
              <DBox x={20} y={298} w={180} h={30} label="Weaviate Index" sub="Chunks + Keyframes" color={C.cyan} glow={C.cyanG} active={ap==="ingest"} small/>
              <DArrow points="110,218 110,226" color={C.blue}/><DArrow points="110,256 110,262" color={C.blue}/><DArrow points="110,292 110,298" color={C.cyan}/>
              <DLabel x={225} y={172} text="INTELLIGENCE" color={C.purple}/>
              <DBox x={225} y={180} w={200} h={38} label="IntelligenceLayer" sub="intelligence_layer.py" icon="🔬" color={C.purple} glow={C.purpleG} active={ap==="intelligence"} onClick={()=>setArchPhase("intelligence")} isNew/>
              <DBox x={225} y={226} w={200} h={30} label="FactVerifier" sub="Extract + verify" icon="✓" color={C.green} glow={C.greenG} active={ap==="intelligence"} small/>
              <DBox x={225} y={262} w={200} h={30} label="Contradiction Scan" sub="Self + cross + data" icon="⚠️" color={C.rose} glow={C.roseG} active={ap==="intelligence"} small isNew/>
              <DBox x={225} y={298} w={200} h={30} label="Speaker Scorecard" sub="Grade + patterns" icon="📊" color={C.purple} glow={C.purpleG} active={ap==="intelligence"} small isNew/>
              <DBox x={225} y={334} w={200} h={30} label="External Data X-Ref" sub="YOUR data vs claims" icon="📈" color={C.amber} glow={C.amberG} active={ap==="intelligence"} small isNew/>
              <DArrow points="325,218 325,226" color={C.green}/><DArrow points="325,256 325,262" color={C.rose}/><DArrow points="325,292 325,298" color={C.purple}/><DArrow points="325,328 325,334" color={C.amber}/>
              <DArrow points="200,199 225,199" color={C.blue} label="claims" lx={212} ly={194}/>
              <DLabel x={450} y={172} text="SYNTHESIS" color={C.cyan}/>
              <DBox x={450} y={180} w={190} h={38} label="SynthesisAgent" sub="synthesis_agent.py" icon="✨" color={C.cyan} glow={C.cyanG} active={ap==="intelligence"}/>
              <DBox x={450} y={226} w={190} h={30} label="OpusPlanner" sub="Strategy per video" icon="🧠" color={C.opus} glow={C.opusG} active={ap==="coordinator"} small/>
              <DBox x={450} y={262} w={190} h={30} label="VideoQA" sub="Q&A" icon="❓" color={C.teal} glow={C.tealG} active={ap==="intelligence"} small/>
              <DArrow points="545,218 545,226" color={C.opus}/><DArrow points="545,256 545,262" color={C.teal}/><DArrow points="425,240 450,240" color={C.purple} dashed label="verdicts" lx={436} ly={235}/>
              <DLabel x={665} y={172} text="WEB INTEL" color={C.pink}/>
              <DBox x={665} y={180} w={275} h={38} label="Parallel.ai (optional)" sub="Skip if no key" icon="🌐" color={C.pink} glow={C.pinkG} active={ap==="intelligence"}/>
              <DArrow points="640,226 665,226" color={C.pink} dashed label="enrich" lx={652} ly={221}/>
              <DLabel x={665} y={240} text="WEAVIATE" color={C.cyan}/>
              <DBox x={665} y={248} w={275} h={34} label="VideoChunks" sub="text2vec (384d)" icon="📝" color={C.cyan} glow={C.cyanG} active={ap==="ingest"}/>
              <DBox x={665} y={288} w={275} h={34} label="VideoKeyframes" sub="CLIP (512d)" icon="🖼️" color={C.cyan} glow={C.cyanG} active={ap==="ingest"}/>
              <DLabel x={20} y={355} text="x402 PAYMENTS" color={C.red}/>
              <DBox x={20} y={363} w={190} h={38} label="x402Middleware" sub="x402_middleware.py" icon="💳" color={C.red} glow={C.redG} active={ap==="x402"} onClick={()=>setArchPhase("x402")}/>
              <DBox x={20} y={409} w={90} h={26} label="$0.03" sub="verify" color={C.red} active={ap==="x402"} small/>
              <DBox x={115} y={409} w={95} h={26} label="$0.03" sub="synth" color={C.red} active={ap==="x402"} small/>
              <DBox x={20} y={440} w={90} h={26} label="$0.01" sub="search" color={C.red} active={ap==="x402"} small/>
              <DBox x={115} y={440} w={95} h={26} label="$0.25" sub="report" color={C.red} active={ap==="x402"} small/>
              <DArrow points="115,401 115,409" color={C.red}/>
              <DBox x={225} y={363} w={200} h={38} label="PaymentAgent" sub="Guardrails · Circle" icon="💰" color={C.red} glow={C.redG} active={ap==="x402"}/>
              <DBox x={225} y={409} w={200} h={26} label="Circle Wallets" sub="USDC on Arc" color={C.red} active={ap==="x402"} small/>
              <DArrow points="210,382 225,382" color={C.red}/><DArrow points="325,401 325,409" color={C.red}/>
              <DBox x={450} y={363} w={190} h={38} label="Observability" sub="Opik traces" icon="📊" color={C.purple} glow={C.purpleG} active={ap==="mcp"}/>
              <DBox x={450} y={409} w={190} h={26} label="MongoDB" sub="Tasks · payments" color={C.dim} active={ap==="x402"||ap==="coordinator"} small/>
              <DLabel x={20} y={492} text="MCP + PLUGIN" color={C.blue}/>
              <DBox x={20} y={500} w={290} h={38} label="MCP Server — 17 tools" sub="cortex_on/mcp/server.py" icon="🌐" color={C.blue} glow={C.blueG} active={ap==="mcp"} onClick={()=>setArchPhase("mcp")}/>
              <DBox x={320} y={500} w={300} h={38} label=".claude-plugin/ — 7 agents · 3 skills · 2 hooks" sub="Claude Code" icon="🔌" color={C.opus} glow={C.opusG} active={ap==="mcp"}/>
              <DBox x={630} y={500} w={310} h={38} label="Auto-Discovery — 16 agents" sub="register_routes()" icon="🔄" color={C.green} glow={C.greenG} active={ap==="mcp"}/>
              <DArrow points="110,146 110,180" color={C.opus} label="ingest" lx={85} ly={165}/><DArrow points="325,146 325,180" color={C.opus} label="analyze" lx={350} ly={165}/><DArrow points="545,146 545,180" color={C.opus} label="synth" lx={570} ly={165}/><DArrow points="800,146 800,180" color={C.opus} label="enrich" lx={825} ly={165}/>
              <DBox x={20} y={555} w={920} h={32} label="cortex_on/ — 16 agents · mcp/ · Docker + MongoDB + Weaviate + Opik" sub="" color={C.dim} active={false}/>
              <text x={W/2} y={610} fontSize="8" fill="#3a4555" textAnchor="middle" fontFamily={mono}>CortexOS v3.0 — Opus 4.6 · 16 Agents · x402 · Arc · Circle</text>
            </svg>
            <div style={{display:"flex",gap:12,marginTop:10,flexWrap:"wrap"}}>
              {[{c:C.opus,l:"Coordinator"},{c:C.blue,l:"Ingestion"},{c:C.purple,l:"Intelligence"},{c:C.cyan,l:"Synthesis"},{c:C.pink,l:"Web Intel"},{c:C.red,l:"x402"},{c:C.green,l:"Discovery"}].map(i=>(
                <div key={i.l} style={{display:"flex",alignItems:"center",gap:4}}><div style={{width:7,height:7,borderRadius:2,background:i.c}}/><span style={{fontSize:8,color:C.muted,fontFamily:mono}}>{i.l}</span></div>
              ))}
            </div>
          </div>
        )}

        {/* ════════ PLUGIN VIEW ════════ */}
        {view === "plugin" && (
          <div style={{animation:"slideIn 0.3s ease"}}>
            <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:14,marginBottom:14,textAlign:"center"}}>
              <div style={{fontFamily:mono,fontSize:8,color:C.dim,marginBottom:3}}>.claude-plugin/plugin.json</div>
              <div style={{fontFamily:mono,fontSize:16,fontWeight:700,color:C.text,marginBottom:3}}>cortexos-plugin</div>
              <p style={{fontSize:10,color:C.muted,margin:"0 0 8px"}}>Full CortexOS packaged as a Claude Code plugin</p>
              <div style={{display:"flex",justifyContent:"center",gap:5,flexWrap:"wrap"}}>
                <Tag color={C.opus}>7 Agents</Tag><Tag color={C.cyan}>3 Skills</Tag><Tag color={C.blue}>17 MCP</Tag><Tag color={C.rose}>2 Hooks</Tag><Tag color={C.lime}>16 Auto</Tag>
              </div>
            </div>
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:8,padding:12,marginBottom:14,fontFamily:mono,fontSize:9,lineHeight:1.8}}>
              <SectionLine color={C.dim}>Structure</SectionLine>
              {[{t:"CortexOS/",c:C.text},{t:"├── .mcp.json",c:C.blue,lbl:"scope",lc:C.blue},{t:"├── agents/",c:C.opus,lbl:"7 docs",lc:C.opus},{t:"│   ├── agent-coordinator.md",c:C.opus,lbl:"NEW",lc:C.lime},{t:"│   ├── intelligence-layer.md",c:C.opus,lbl:"NEW",lc:C.lime},{t:"│   └── ... (5 more)",c:C.muted},{t:"├── skills/ · hooks/",c:C.cyan},{t:"└── cortex_on/agents/ (16)",c:C.green,lbl:"Python",lc:C.green}].map((f,i)=>(
                <div key={i} style={{display:"flex",alignItems:"center",gap:5}}><span style={{color:f.c}}>{f.t}</span>{f.lbl&&<Tag color={f.lc}>{f.lbl}</Tag>}</div>
              ))}
            </div>
            <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:12}}>
              <SectionLine color={C.opus}>Mission Example</SectionLine>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:12}}>
                {[{t:"You say",e:'"Analyze 8 Tom Lee videos + ETH data"',c:C.green},{t:"Coordinator",e:"Plan → ingest → verify → contradictions → scorecard → synthesis",c:C.opus},{t:"You get",e:'C+ reliability · "outflows" claim disproved · $0.28',c:C.rose}].map((s,i)=>(
                  <div key={i}><div style={{fontFamily:mono,fontSize:8,fontWeight:700,color:s.c,marginBottom:3,textTransform:"uppercase",letterSpacing:"0.06em"}}>{s.t}</div><div style={{fontSize:9,color:C.muted,lineHeight:1.5}}>{s.e}</div></div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Demo + Footer */}
        <div style={{textAlign:"center",marginTop:18}}>
          <button onClick={runDemo} disabled={running} style={{fontFamily:mono,fontSize:10,fontWeight:600,padding:"8px 20px",background:running?C.surface:`linear-gradient(135deg, ${C.opus}, #d97706)`,color:running?C.dim:"#000",border:`1px solid ${running?C.border:C.opus}`,borderRadius:5,cursor:running?"default":"pointer",letterSpacing:"0.04em",transition:"all 0.3s"}}>{running?"⟳ Running…":"▶ Simulate Mission"}</button>
        </div>
        <div style={{textAlign:"center",marginTop:16,paddingTop:10,borderTop:`1px solid ${C.border}`,fontFamily:mono,fontSize:7,color:C.dim}}>CortexOS v3.0 — Coordinator · 16 Agents · Intelligence · x402 · Arc · MCP</div>
      </div>
    </div>
  );
}
