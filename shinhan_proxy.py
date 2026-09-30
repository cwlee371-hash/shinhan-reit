#!/usr/bin/env python3
"""
신한리츠 실시간 모니터링 — Koscom API 프록시
실행: python shinhan_proxy.py  |  접속: http://localhost:5050
"""
from flask import Flask, request, Response
from flask_cors import CORS
import requests, json, sys, re, os, threading
from datetime import datetime, timezone, timedelta

KOSCOM_BASE  = "https://asp.koscom.co.kr/listservice"
AUTH         = "aeolBLFf3IeMzDdJ2g4zfT75Jd4CPhhd"
PORT         = 5050
KST          = timezone(timedelta(hours=9))

# ── KRX OpenAPI 키 (무료 등록: https://openapi.krx.co.kr) ──────────────────
KRX_API_KEY  = ""

app = Flask(__name__)
CORS(app)

# ── 네이버 외국인·기관 순매매 스크래핑 ────────────────────────────────────
_frgn_cache = {}   # {code: (data, ts)}
FRGN_TTL    = 300  # 5분 캐시

def scrape_naver_frgn(code):
    """Naver Finance frgn 페이지 → 외국인·기관 순매매 20일 JSON"""
    try:
        r = requests.get(
            f"https://finance.naver.com/item/frgn.nhn?code={code}",
            headers={
                "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Referer":     "https://finance.naver.com/",
            },
            timeout=8
        )
        r.encoding = "euc-kr"
        html = r.text

        rows = []
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL):
            tds  = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
            cells = [re.sub(r'<[^>]+>', ' ', td) for td in tds]
            cells = [re.sub(r'\s+', ' ', c).strip() for c in cells]
            cells = [c for c in cells if c and c != '\xa0']

            if not cells or not re.match(r'\d{4}\.\d{2}\.\d{2}', cells[0]):
                continue

            def to_int(s):
                s = s.replace(',', '').replace('+', '').strip()
                m = re.search(r'-?\d+', s)
                return int(m.group()) if m else 0

            try:
                chg_raw  = cells[2] if len(cells) > 2 else ""
                chg_nums = re.findall(r'\d+', chg_raw.replace(',', ''))
                chg_amt  = int(chg_nums[0]) if chg_nums else 0
                chg_dir  = "down" if "하락" in chg_raw or ("-" in chg_raw and chg_amt) else \
                           "up"   if "상승" in chg_raw or "+" in chg_raw else "flat"

                rows.append({
                    "date":        cells[0],
                    "close":       to_int(cells[1]) if len(cells) > 1 else 0,
                    "chg_dir":     chg_dir,
                    "chg_amt":     chg_amt,
                    "chg_rate":    cells[3].strip()  if len(cells) > 3 else "",
                    "volume":      to_int(cells[4])  if len(cells) > 4 else 0,
                    "inst_net":    to_int(cells[5])  if len(cells) > 5 else 0,
                    "frgn_net":    to_int(cells[6])  if len(cells) > 6 else 0,
                    "frgn_shares": to_int(cells[7])  if len(cells) > 7 else 0,
                    "frgn_ratio":  cells[8].strip()  if len(cells) > 8 else "",
                })
            except Exception:
                continue

        return {"rows": rows[:20], "ok": len(rows) > 0}
    except Exception as e:
        return {"rows": [], "ok": False, "error": str(e)}

# ══════════════════════════════════════════════════════════════════
#  DASHBOARD HTML
# ══════════════════════════════════════════════════════════════════
DASHBOARD = r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>신한리츠 모니터링</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0e1a;color:#eceff6;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;font-size:14px}
.num{font-family:'Consolas','D2Coding',monospace}
/* 마스트헤드 */
.mast{background:linear-gradient(135deg,#0d1430,#0a0e1a);border-bottom:1px solid #232e47;padding:12px 22px}
.eyebrow{font-size:10px;color:#3457ff;letter-spacing:.12em;text-transform:uppercase;margin-bottom:3px}
.mast-row{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.mast h1{font-size:19px;font-weight:700}
.mast-right{display:flex;align-items:center;gap:8px;font-size:11px;color:#8993ad}
.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;margin-left:5px;vertical-align:middle;transition:background .3s}
.btn{background:rgba(52,87,255,.15);border:1px solid #3457ff;border-radius:6px;color:#9db4ff;font-size:11px;padding:3px 10px;cursor:pointer}
/* 지수 스트립 */
.strip{background:#111827;border-bottom:1px solid #232e47;display:flex;align-items:center;padding:0 22px;overflow-x:auto}
.idx{display:flex;align-items:center;gap:8px;padding:9px 20px 9px 0;margin-right:16px;border-right:1px solid #1a2338}
.idx-label{font-size:10px;color:#8993ad;width:50px}
/* 매크로 배너 */
#macro-banner{padding:0 20px}
.mac-box{border-radius:10px;padding:10px 16px;margin-top:12px}
.mac-today{background:rgba(255,170,0,.09);border:1px solid rgba(255,170,0,.3)}
.mac-soon{background:rgba(52,87,255,.07);border:1px solid rgba(52,87,255,.22);margin-top:7px}
.mac-ttl{font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;margin-bottom:5px}
.mac-ev{display:flex;align-items:baseline;gap:8px;margin-bottom:3px;font-size:12px}
.mac-badge{font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;flex-shrink:0}
/* ── 메인 카드 그리드 ─────────────────────────────────── */
.cards{padding:14px 20px;display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:16px}
.card{background:#111827;border:1px solid #232e47;border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
.card-stripe{height:3px}
.card-body{padding:18px 20px;display:flex;flex-direction:column;gap:14px;flex:1}
/* 헤더 */
.card-head{display:flex;justify-content:space-between;align-items:flex-start}
.stock-name{font-size:17px;font-weight:700;margin-bottom:2px}
.stock-sub{font-size:11px;color:#8993ad}
.badge{background:rgba(52,87,255,.15);color:#9db4ff;border:1px solid #2a3e8f;border-radius:5px;padding:2px 8px;font-size:10px;font-weight:600}
/* 가격 */
.price-row{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.price-big{font-size:40px;font-weight:700;font-family:'Consolas','D2Coding',monospace;letter-spacing:-.01em}
.chg-badge{font-size:13px;font-weight:700;border-radius:7px;padding:4px 10px;font-family:'Consolas','D2Coding',monospace}
/* 핵심 지표 바 */
.stat-bar{display:flex;gap:8px;flex-wrap:wrap}
.stat-pill{background:#161f33;border-radius:7px;padding:6px 10px;flex:1;min-width:80px}
.stat-pill-l{font-size:9px;color:#5d6680;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px}
.stat-pill-v{font-size:12px;font-weight:600;font-family:'Consolas','D2Coding',monospace}
/* 스파크라인 */
.spark-box{background:#161f33;border-radius:9px;padding:7px 5px 3px;overflow:hidden}
.spark-foot{font-size:9px;color:#5d6680;margin-top:3px;text-align:right}
/* ── 외국인·기관 순매매 표 ────────────────────────────── */
.frgn-section{background:#0c1322;border:1px solid #1e2d45;border-radius:10px;overflow:hidden}
.frgn-header{display:flex;justify-content:space-between;align-items:center;padding:8px 14px;border-bottom:1px solid #1a2338}
.frgn-ttl{font-size:11px;font-weight:700;color:#eceff6}
.frgn-src{font-size:10px;color:#5d6680}
.frgn-scroll{overflow-x:auto;max-height:280px;overflow-y:auto}
.frgn-table{width:100%;border-collapse:collapse;font-size:12px}
.frgn-table thead th{position:sticky;top:0;background:#111827;padding:6px 10px;font-size:10px;font-weight:600;color:#8993ad;text-align:right;border-bottom:1px solid #232e47;white-space:nowrap}
.frgn-table thead th:first-child{text-align:left}
.frgn-table tbody tr{border-bottom:1px solid #131e30}
.frgn-table tbody tr:hover{background:#161f33}
.frgn-table tbody td{padding:5px 10px;text-align:right;font-family:'Consolas','D2Coding',monospace;white-space:nowrap}
.frgn-table tbody td:first-child{text-align:left;color:#8993ad}
.frgn-load{padding:16px;text-align:center;font-size:11px;color:#5d6680}
/* 거래원 */
.broker-wrap{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.broker-side-ttl{font-size:10px;font-weight:700;margin-bottom:5px}
.brow{display:flex;align-items:center;gap:5px;margin-bottom:4px}
.brank{font-size:10px;color:#5d6680;width:13px;text-align:right;flex-shrink:0}
.bname{flex:1;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bbar-wrap{width:30px;height:4px;background:#1a2338;border-radius:2px;flex-shrink:0}
.bbar{height:100%;border-radius:2px}
.bvol{font-size:10px;width:44px;text-align:right;color:#cfd4e4;flex-shrink:0;font-family:'Consolas','D2Coding',monospace}
.btype-f{font-size:9px;background:rgba(255,170,0,.15);color:#ffaa00;border-radius:3px;padding:0 4px;margin-left:2px}
/* 오늘의 해석 (접힘) */
.analysis-toggle{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:#161f33;border-radius:8px;cursor:pointer;font-size:11px;color:#8993ad;user-select:none}
.analysis-body{display:none;background:#0c1322;border:1px solid #1e2d45;border-radius:0 0 8px 8px;border-top:none}
.analysis-body.open{display:block}
.arow{display:flex;border-top:1px solid #131e30}
.atag{width:76px;flex-shrink:0;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:9px 0 9px 14px;display:flex;align-items:flex-start}
.abody{flex:1;padding:9px 14px 9px 0}
.ahl{font-size:12px;font-weight:600;margin-bottom:2px;color:#eceff6;line-height:1.4}
.adt{font-size:11px;color:#8993ad;line-height:1.5}
.s-bull .atag{color:#ff5267}.s-bear .atag{color:#4fa8ff}.s-neu .atag{color:#8993ad}
/* 수급주체 패널 */
.inv-panel{border-top:1px solid #1a2338}
.inv-row{display:flex}
.inv-cell{flex:1;padding:9px 12px;border-right:1px solid #1a2338;display:flex;flex-direction:column;gap:2px}
.inv-cell:last-child{border-right:none}
.inv-lbl{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.inv-val{font-size:15px;font-weight:700;font-family:'Consolas','D2Coding',monospace}
.inv-src{padding:3px 12px 7px;font-size:10px;color:#5d6680}
.inv-setup{padding:9px 14px;font-size:11px;color:#8993ad;line-height:1.6}
.inv-setup a{color:#9db4ff}
/* ── 피어 비교 (심플 바차트) ──────────────────────────── */
/* misc */
.sec-lbl{font-size:9px;color:#5d6680;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
.loading{padding:40px;text-align:center;color:#8993ad}
.sum-bull{color:#ff5267}.sum-bear{color:#4fa8ff}.sum-neu{color:#8993ad}
/* ── 차트 ─────────────────────────────────────────────── */
.chart-box{background:#161f33;border-radius:9px;padding:8px 6px 4px;overflow:hidden}
.chart-legend{display:flex;gap:14px;margin-top:5px;font-size:9px;color:#5d6680}
.chart-legend span{display:flex;align-items:center;gap:3px}
.chart-legend i{display:inline-block;width:10px;height:3px;border-radius:2px;vertical-align:middle}
/* ── AI 브리핑 ───────────────────────────────────────── */
.briefing{margin:12px 20px 0;background:#0f1826;border:1px solid #1e2d45;border-radius:12px;overflow:hidden}
.br-hdr{display:flex;justify-content:space-between;align-items:center;padding:10px 16px;cursor:pointer;user-select:none}
.br-hdr:hover{background:rgba(255,255,255,.02)}
.br-left{display:flex;align-items:center;gap:8px}
.br-title{font-size:13px;font-weight:700}
.br-time{font-size:10px;color:#5d6680;margin-left:4px}
.br-btn{font-size:10px;background:rgba(52,87,255,.15);border:1px solid #3457ff;border-radius:4px;color:#9db4ff;padding:2px 8px;cursor:pointer}
.br-arrow{font-size:11px;color:#5d6680;margin-left:6px;transition:transform .2s}
.br-body{padding:0 18px 16px;display:none}
.br-body.open{display:block}
.br-content{font-size:13px;line-height:1.85;color:#cfd4e4;padding-top:10px}
.br-content strong{color:#eceff6;font-weight:600}
.br-content p{margin-bottom:8px}
.br-content ul,.br-content ol{padding-left:16px;margin-bottom:8px}
.br-content li{margin-bottom:3px}
.br-content h1,.br-content h2,.br-content h3{color:#9db4ff;font-size:12px;font-weight:700;margin:12px 0 5px;text-transform:none}
.br-load{padding:20px 0;text-align:center;font-size:12px;color:#5d6680}
.br-err{padding:14px 0;font-size:12px;color:#8993ad;line-height:1.7}
.br-err a{color:#9db4ff}
.br-disc{font-size:10px;color:#3b4564;margin-top:14px;padding-top:10px;border-top:1px solid #1a2338;line-height:1.6}
.br-date{font-size:11px;color:#5d6680;margin-bottom:8px}
.br-summary-ttl{font-size:12px;font-weight:700;color:#9db4ff;margin-bottom:4px}
.br-stock-ttl{font-size:12px;font-weight:700;color:#eceff6;margin:14px 0 3px;padding-top:12px;border-top:1px solid #1a2338}
.br-price-line{font-size:15px;font-weight:700;font-family:'Consolas','D2Coding',monospace;margin-bottom:8px;color:#eceff6}
.br-paras{display:flex;flex-direction:column;gap:6px}
.br-para{font-size:12.5px;line-height:1.7;color:#cfd4e4;padding:6px 10px;background:rgba(255,255,255,.02);border-radius:6px;border-left:2px solid #232e47}
.br-para strong{color:#eceff6}
</style></head>
<body>

<div class="mast">
  <div class="eyebrow">SHINHAN REIT 운용 · 상장리츠전략부</div>
  <div class="mast-row">
    <h1>실시간 거래 모니터링</h1>
    <div class="mast-right">
      <span class="num" id="clock" style="font-size:13px;color:#eceff6"></span>
      <span id="upd">연결 중<span class="pulse" id="pulse" style="background:#8993ad"></span></span>
      <button class="btn" onclick="refresh()">↺ 갱신</button>
      <button id="btn-peers" class="btn" onclick="updatePeers()"
        style="background:rgba(255,170,0,.12);border-color:rgba(255,170,0,.4);color:#ffaa00">
        📊 리츠 업데이트
      </button>
      <a href="/etf-monitor"
        style="font-size:11px;background:rgba(167,139,250,.1);border:1px solid rgba(167,139,250,.35);border-radius:6px;color:#a78bfa;padding:3px 10px;text-decoration:none;font-weight:600">
        📡 ETF 모니터
      </a>
      <a href="/export-report" id="btn-export"
        style="background:rgba(52,255,130,.1);border:1px solid rgba(52,255,130,.35);border-radius:6px;color:#4ade80;font-size:11px;padding:3px 10px;text-decoration:none;font-weight:600">
        📥 일일 보고서
      </a>
      <a href="/investor-report"
        style="background:rgba(100,160,255,.1);border:1px solid rgba(100,160,255,.35);border-radius:6px;color:#64a0ff;font-size:11px;padding:3px 10px;text-decoration:none;font-weight:600">
        📋 투자자 보고서
      </a>
    </div>
  </div>
</div>

<div class="strip">
  <div class="idx"><span class="idx-label">KOSPI</span>
    <span class="num" id="kospi-p" style="font-size:16px;font-weight:700">–</span>
    <span class="num" id="kospi-c" style="font-size:11px;font-weight:600"></span></div>
  <div class="idx"><span class="idx-label">KOSDAQ</span>
    <span class="num" id="kosdaq-p" style="font-size:16px;font-weight:700">–</span>
    <span class="num" id="kosdaq-c" style="font-size:11px;font-weight:600"></span></div>
  <div id="macro-strip" style="margin-left:auto;display:flex;align-items:center;gap:6px;padding-right:2px"></div>
</div>

<div id="macro-banner"></div>
<div style="background:#0f1826;border:1px solid #1e2d45;border-radius:10px;padding:10px 16px;margin:8px 20px 0;font-size:11px;color:#5d6680;line-height:1.7">
  ⚠️ <b style="color:#8993ad">이용 안내</b> &nbsp;—&nbsp;
  본 페이지는 Koscom API·네이버 증권·DART 공시 데이터를 기반으로 <b>AI가 자동 생성</b>한 참고 자료입니다.
  데이터 지연·오류·누락이 발생할 수 있으며, AI 분석 브리핑은 실제와 다를 수 있습니다.
  <b>투자 권유·자문이 아니며</b>, 정확한 정보는 공식 공시 자료 및 담당자 확인을 권장합니다.
</div>

<!-- AI 브리핑 -->
<div class="briefing" id="briefing-wrap">
  <div class="br-hdr" onclick="toggleBriefing()">
    <div class="br-left">
      <span>🤖</span>
      <span class="br-title">AI 브리핑</span>
      <span class="br-time" id="br-time"></span>
    </div>
    <div style="display:flex;align-items:center;gap:6px">
      <button class="br-btn" id="br-btn"
        onclick="event.stopPropagation();regenerateBriefing()"
        style="display:none">↺ 재생성</button>
      <span class="br-arrow" id="br-arrow">▼</span>
    </div>
  </div>
  <div class="br-body" id="br-body">
    <div class="br-load" id="br-inner">브리핑 불러오는 중…</div>
  </div>
</div>

<div id="cards" class="cards"><div class="loading">연결 중…</div></div>

<script>
const AUTH="aeolBLFf3IeMzDdJ2g4zfT75Jd4CPhhd";
const STOCKS=[
  {code:"293940",name:"신한알파리츠",      type:"오피스"},
  {code:"404990",name:"신한서부티엔디리츠",type:"리테일·호텔"},
];

const BN=["F07505","F07506","F07507","F07508","F07509"];
const BV=["F07510","F07511","F07512","F07513","F07514"];
const SN=["F07530","F07531","F07532","F07533","F07534"];
const SV=["F07535","F07536","F07537","F07538","F07539"];
const FOREIGN_KEYS=["모간","골드만","씨티","UBS","메릴","도이치","CLSA","노무라","맥쿼리","BNP","바클레이","HSBC","모건스탠리"];
const MACRO=[
  {d:"2026-06-10",t:"F",n:"FOMC 금리 결정",i:"HIGH"},
  {d:"2026-07-16",t:"B",n:"한국은행 금통위 ✓ 기준금리 25bp 인상",i:"HIGH",result:"인상"},
  {d:"2026-07-15",t:"C",n:"미국 CPI 발표",i:"MED"},
  {d:"2026-07-29",t:"F",n:"FOMC 금리 결정",i:"HIGH"},
  {d:"2026-07-01",t:"K",n:"한국 CPI 발표",i:"MED"},
  {d:"2026-08-12",t:"C",n:"미국 CPI 발표",i:"MED"},
  {d:"2026-08-28",t:"B",n:"한국은행 금통위",i:"HIGH"},
  {d:"2026-09-09",t:"C",n:"미국 CPI 발표",i:"MED"},
  {d:"2026-09-16",t:"F",n:"FOMC 금리 결정",i:"HIGH"},
  {d:"2026-10-14",t:"C",n:"미국 CPI 발표",i:"MED"},
  {d:"2026-10-16",t:"B",n:"한국은행 금통위",i:"HIGH"},
  {d:"2026-10-28",t:"F",n:"FOMC 금리 결정",i:"HIGH"},
  {d:"2026-11-27",t:"B",n:"한국은행 금통위",i:"HIGH"},
  {d:"2026-12-09",t:"F",n:"FOMC 금리 결정",i:"HIGH"},
];
const TLBL={F:"FOMC",B:"금통위",C:"미국CPI",K:"한국CPI"};
const TCLR={F:"#ffaa00",B:"#ff5267",C:"#4fa8ff",K:"#a78bfa"};

function n(s){return parseFloat((s||"0").toString().replace(/,/g,""))||0;}
function fmt(v){return Math.round(v).toLocaleString("ko-KR");}
function fmtV(v){return v>=1e4?`${Math.round(v/1e4)}만주`:`${fmt(v)}주`;}
function fmtA(v){
  if(v>=1e12)return(v/1e12).toFixed(1)+"조";
  if(v>=1e10)return Math.round(v/1e8)+"억";
  if(v>=1e8)return(v/1e8).toFixed(1)+"억";
  return fmt(v);
}
function dc(d){return d==="2"?"#ff5267":d==="5"?"#4fa8ff":"#8993ad";}
function db(d){return d==="2"?"rgba(255,82,103,.12)":d==="5"?"rgba(79,168,255,.12)":"rgba(137,147,173,.10)";}
function da(d){return d==="2"?"▲":d==="5"?"▼":"–";}
function isForeign(nm){return nm&&FOREIGN_KEYS.some(k=>nm.includes(k));}
function isRetail(nm){return nm&&["키움","토스","카카오페이","나무","이베스트"].some(k=>nm.includes(k));}

/* 스파크라인 */
function spark(intra,prev){
  const pts=[...intra].reverse().filter((_,i)=>i%5===0||i===intra.length-1);
  if(pts.length<3)return'<text x="10" y="24" fill="#5d6680" font-size="11">장 시작 후 축적 중</text>';
  const prices=pts.map(p=>n(p.F20008));
  const W=400,H=48,pad=8;
  const all=[...prices,prev],lo=Math.min(...all),hi=Math.max(...all),rng=hi-lo||prices[0]*0.005;
  const tx=i=>(pad+(i/(prices.length-1))*(W-pad*2)).toFixed(1);
  const ty=p=>(H-pad-((p-lo)/rng)*(H-pad*2)).toFixed(1);
  const poly=prices.map((p,i)=>`${tx(i)},${ty(p)}`).join(" ");
  const prevY=ty(prev);
  const clr=prices[prices.length-1]>=prev?"#ff5267":"#4fa8ff";
  return`<line x1="${pad}" y1="${prevY}" x2="${W-pad}" y2="${prevY}" stroke="#5d6680" stroke-dasharray="3,4" stroke-width="1"/>
  <polyline points="${poly}" fill="none" stroke="${clr}" stroke-width="1.5" stroke-linejoin="round"/>`;
}

/* VWAP */
function vwap(intra){
  if(!intra||!intra.length)return 0;
  const l=intra[0],a=n(l.F20013),v=n(l.F20012);
  return v>0?a/v:0;
}

/* 주가 분석 */
function analyze(st,mem,hist,intra,kospiData){
  const price=n(st.F15001),prev=n(st.F15007),dir=st.F15006||"3";
  const open=n(st.F15009),high=n(st.F15010),low=n(st.F15011);
  const vol=n(st.F15015),amt=n(st.F15023);
  const slice=hist.slice(0,30);
  const avg=slice.length?slice.reduce((s,h)=>s+n(h.F15015),0)/slice.length:0;
  const vr=avg>0?vol/avg:1;
  const rng=high-low||1,cp=(price-low)/rng;
  const buyers=BN.map((k,i)=>({name:mem[k],vol:n(mem[BV[i]])})).filter(b=>b.name&&b.vol>0);
  const sellers=SN.map((k,i)=>({name:mem[k],vol:n(mem[SV[i]])})).filter(s=>s.name&&s.vol>0);
  const net=buyers.reduce((s,b)=>s+b.vol,0)-sellers.reduce((s,b)=>s+b.vol,0);
  const vw=vwap(intra);
  const vd=vw>0?(price-vw)/vw*100:0;
  const kdir=kospiData?.F15006||"3";
  const rows=[];

  if(vw>0)rows.push({s:price>=vw?"bull":"bear",tag:"평균매가",
    hl:price>=vw?"현재가 VWAP 상회":"현재가 VWAP 하회",
    dt:`VWAP ${fmt(Math.round(vw))}원 / 현재 ${fmt(price)}원 (${vd>0?"+":""}${vd.toFixed(2)}%) · 거래대금 ${fmtA(amt)}`});

  const vl=`거래량 ${(vr*100).toFixed(0)}%`;
  rows.push({s:vr<0.6?(dir==="5"?"bear":"neu"):vr>1.2?(dir==="2"?"bull":"bear"):"neu",tag:"거래량·세",
    hl:vr<0.5?(dir==="5"?`저거래량 — 매수공백 하락`:`저거래량 — 수급 부재`):vr>1.3?(dir==="2"?"강한 매수세 유입":"매도 압력 증가"):`${vl}`,
    dt:`오늘 ${fmt(n(st.F15015))}주 / 30일 평균 ${fmt(Math.round(avg))}주`});

  if(buyers.length&&sellers.length){
    const fB=buyers.filter(b=>isForeign(b.name)).reduce((s,b)=>s+b.vol,0);
    const fS=sellers.filter(s=>isForeign(s.name)).reduce((s,b)=>s+b.vol,0);
    rows.push({s:net>=0?"bull":"bear",tag:"수급주체",
      hl:net>=0?`창구 순매수 +${fmt(net)}주`:` 창구 순매도 ${fmt(net)}주`,
      dt:`매수 1위: ${buyers[0].name} ${fmt(buyers[0].vol)}주 · 매도 1위: ${sellers[0].name} ${fmt(sellers[0].vol)}주`+
         (fB>fS?` · 외국계 매수 우위`:(fS>fB?` · 외국계 매도 우위`:""))});
  }

  if(kdir==="2"&&dir==="5")rows.push({s:"bear",tag:"시장 대비",hl:"코스피 강세 속 역행 하락",dt:"섹터 로테이션 또는 금리 민감도 이슈"});
  else if(kdir==="5"&&dir==="2")rows.push({s:"bull",tag:"시장 대비",hl:"코스피 약세 속 역행 강세",dt:"배당 수요 또는 종목 고유 매수세"});
  else rows.push({s:"neu",tag:"시장 대비",hl:dir==="2"?"코스피와 동조 상승":"코스피와 동조 하락",dt:""});

  if(cp<0.12)rows.push({s:"bear",tag:"장중",hl:"저가 마감",dt:`고가 ${fmt(high)} → 저가 ${fmt(low)} 근방 마감`});
  else if(cp>0.88)rows.push({s:"bull",tag:"장중",hl:"고가 근방 마감",dt:`저가 ${fmt(low)} 반등 후 고가 ${fmt(high)} 근방`});

  const bulls=rows.filter(r=>r.s==="bull").length,bears=rows.filter(r=>r.s==="bear").length;
  const overall=bulls>bears?"bull":bears>bulls?"bear":"neu";
  const chgR=parseFloat(st.F15004||"0");
  const summary=`거래량 ${(vr*100).toFixed(0)}%${vw>0?(price>=vw?" · VWAP↑":" · VWAP↓"):""}${net>=0?` · 순매수 +${fmt(net)}`:` · 순매도 ${fmt(net)}`}${kdir==="2"&&dir==="5"?" · 코스피 역행":kdir==="5"&&dir==="2"?" · 아웃퍼폼":""}`;
  return{rows,summary,overall};
}

/* 외국인·기관 표 렌더 */
function renderFrgnTable(frgnData){
  if(!frgnData||!frgnData.ok||!frgnData.rows||!frgnData.rows.length){
    return`<div class="frgn-load">
      ${frgnData?.error?`오류: ${frgnData.error}`:"외국인·기관 데이터를 가져올 수 없습니다."}
      <br><a href="https://finance.naver.com/item/frgn.nhn" target="_blank" style="color:#9db4ff;font-size:10px">네이버 금융에서 직접 확인</a>
    </div>`;
  }
  const rows=frgnData.rows;
  return`<div class="frgn-scroll">
    <table class="frgn-table">
      <thead><tr>
        <th>날짜</th><th>종가</th><th>등락률</th><th>거래량</th>
        <th>기관 순매매</th><th>외국인 순매매</th><th>보유율</th>
      </tr></thead>
      <tbody>
        ${rows.map(r=>{
          const rclr=r.chg_rate.startsWith("-")?"#4fa8ff":"#ff5267";
          const iclr=r.inst_net<0?"#4fa8ff":"#ff5267";
          const fclr=r.frgn_net<0?"#4fa8ff":"#ff5267";
          return`<tr>
            <td>${r.date.replace(/^\d{4}\./,"")}</td>
            <td>${fmt(r.close)}</td>
            <td style="color:${rclr}">${r.chg_rate}</td>
            <td>${fmtV(r.volume)}</td>
            <td style="color:${iclr};font-weight:600">${r.inst_net>=0?"+":""}${fmt(r.inst_net)}</td>
            <td style="color:${fclr};font-weight:600">${r.frgn_net>=0?"+":""}${fmt(r.frgn_net)}</td>
            <td>${r.frgn_ratio}</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>
  </div>`;
}

/* ── 30일 가격·거래량 차트 ─────────────────────────── */
function makeHistChart(hist){
  if(!hist||hist.length<5)return"";
  const data=[...hist].reverse().slice(-30);
  const W=600,H=110,VH=22,PAD=6,PH=H-VH-8;
  const closes=data.map(d=>n(d.F15001));
  const vols=data.map(d=>n(d.F15015));
  const minP=Math.min(...closes),maxP=Math.max(...closes),rng=maxP-minP||1;
  const maxV=Math.max(...vols)||1;
  const bw=(W-PAD*2)/data.length;
  // 가격 라인
  const pts=data.map((d,i)=>{
    const x=(PAD+i*bw+bw/2).toFixed(1);
    const y=(PH-((n(d.F15001)-minP)/rng)*(PH-PAD*2)+PAD).toFixed(1);
    return`${x},${y}`;
  }).join(" ");
  // 거래량 바
  const vbars=data.map((d,i)=>{
    const x=(PAD+i*bw).toFixed(1);
    const vh=((n(d.F15015)/maxV)*VH).toFixed(1);
    const y=(H-vh).toFixed(1);
    const clr=d.F15006==="2"?"#ff5267":"#4fa8ff";
    return`<rect x="${x}" y="${y}" width="${(bw-0.5).toFixed(1)}" height="${vh}" fill="${clr}" opacity="0.55"/>`;
  }).join("");
  // Y축 레이블 (최고/최저)
  const yHi=(PAD+3).toFixed(1), yLo=(PH-3).toFixed(1);
  return`<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    ${vbars}
    <polyline points="${pts}" fill="none" stroke="#3457ff" stroke-width="1.8" stroke-linejoin="round"/>
    <text x="${W-PAD}" y="${yHi}" text-anchor="end" font-size="9" fill="#5d6680">${fmt(maxP)}</text>
    <text x="${W-PAD}" y="${yLo}" text-anchor="end" font-size="9" fill="#5d6680">${fmt(minP)}</text>
  </svg>`;
}

/* ── 기관·외국인 순매매 바차트 ────────────────────────── */
function makeFrgnChart(rows){
  if(!rows||rows.length<3)return"";
  const data=[...rows].reverse();
  const W=600,H=90,PAD=6;
  const instV=data.map(r=>r.inst_net||0);
  const frgnV=data.map(r=>r.frgn_net||0);
  const maxA=Math.max(...instV.map(Math.abs),...frgnV.map(Math.abs),1);
  const bw=(W-PAD*2)/data.length;
  const halfBw=bw*0.42;
  const zero=H/2;
  const toY=v=>zero-(v/maxA)*(H/2-PAD);

  const bars=data.map((d,i)=>{
    const ix=PAD+i*bw, iy=Math.min(toY(d.inst_net||0),zero);
    const ih=Math.abs(toY(d.inst_net||0)-zero);
    const fx=ix+halfBw+1, fy=Math.min(toY(d.frgn_net||0),zero);
    const fh=Math.abs(toY(d.frgn_net||0)-zero);
    return`<rect x="${ix.toFixed(1)}" y="${iy.toFixed(1)}" width="${halfBw.toFixed(1)}" height="${ih.toFixed(1)}" fill="${(d.inst_net||0)>=0?"#ff5267":"#4fa8ff"}" opacity="0.8"/>
    <rect x="${fx.toFixed(1)}" y="${fy.toFixed(1)}" width="${halfBw.toFixed(1)}" height="${fh.toFixed(1)}" fill="${(d.frgn_net||0)>=0?"#ff9944":"#88aaff"}" opacity="0.8"/>`;
  }).join("");

  return`<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    <line x1="${PAD}" y1="${zero}" x2="${W-PAD}" y2="${zero}" stroke="#2a3654" stroke-width="1"/>
    ${bars}
  </svg>`;
}

/* 카드 렌더 */
function renderCard(meta,raw,kospi){
  const r=raw.results||{};
  const st=r.STOCK||{},mem=r.MEMBER||{};
  const hist=r.HIST||[],intra=r.INTRA||[];
  const price=n(st.F15001),prev=n(st.F15007),chg=n(st.F15472);
  const chgR=parseFloat(st.F15004||"0"),dir=st.F15006||"3";
  const vol=n(st.F15015),amt=n(st.F15023),cap=n(st.F15028);
  const hi52=n(st.F02133),lo52=n(st.F02155);
  const slice=hist.slice(0,30);
  const avg=slice.length?slice.reduce((s,h)=>s+n(h.F15015),0)/slice.length:0;
  const vr=avg>0?vol/avg:1;
  const p52=Math.max(0,Math.min(100,(price-lo52)/(hi52-lo52||1)*100));
  const vw=vwap(intra),vd=vw>0?(price-vw)/vw*100:0;
  const buyers=BN.map((k,i)=>({name:mem[k],vol:n(mem[BV[i]])})).filter(b=>b.name&&b.vol>0);
  const sellers=SN.map((k,i)=>({name:mem[k],vol:n(mem[SV[i]])})).filter(s=>s.name&&s.vol>0);
  const net=buyers.reduce((s,b)=>s+b.vol,0)-sellers.reduce((s,b)=>s+b.vol,0);
  const maxB=Math.max(0,...buyers.map(b=>b.vol),...sellers.map(s=>s.vol))||1;
  const sp=spark(intra,prev);
  const {rows:ins,summary,overall}=analyze(st,mem,hist,intra,kospi);
  const sumCls=overall==="bull"?"sum-bull":overall==="bear"?"sum-bear":"sum-neu";
  const cid=`c-${meta.code}`;

  function brow(items,color){
    return items.slice(0,3).map((item,i)=>`
      <div class="brow">
        <span class="brank">${i+1}</span>
        <span class="bname">${item.name}${isForeign(item.name)?`<span class="btype-f">외</span>`:""}</span>
        <div class="bbar-wrap"><div class="bbar" style="width:${(item.vol/maxB*100).toFixed(0)}%;background:${color}"></div></div>
        <span class="bvol">${fmt(item.vol)}</span>
      </div>`).join("");
  }

  return`<div class="card">
  <div class="card-stripe" style="background:${dc(dir)}"></div>
  <div class="card-body">
    <div class="card-head">
      <div><div class="stock-name">${meta.name}</div>
        <div class="stock-sub">KOSPI ${meta.code} · ${meta.type}</div></div>
      <span class="badge">당사 관리종목</span>
    </div>

    <div class="price-row">
      <span class="price-big">${fmt(price)}</span>
      <span class="chg-badge" style="background:${db(dir)};color:${dc(dir)}">
        ${da(dir)} ${fmt(Math.abs(chg))} (${Math.abs(chgR).toFixed(2)}%)</span>
    </div>

    <div class="stat-bar">
      ${[
        ["VWAP",vw>0?`${fmt(Math.round(vw))}원 (${vd>0?"+":""}${vd.toFixed(1)}%)`:"–"],
        ["거래량",`평균 대비 ${(vr*100).toFixed(0)}%`],
        ["거래대금",fmtA(amt)],
        ["52주",`${p52.toFixed(0)}% 위치`],
      ].map(([l,v])=>`<div class="stat-pill">
        <div class="stat-pill-l">${l}</div>
        <div class="stat-pill-v">${v}</div>
      </div>`).join("")}
    </div>

    <div>
      <div class="sec-lbl">장중 체결 추이 (오늘)</div>
      <div class="spark-box">
        <svg viewBox="0 0 400 48" width="100%" height="48" preserveAspectRatio="none">${sp}</svg>
      </div>
      <div class="spark-foot">─ ─ 전일종가 ${fmt(prev)}</div>
    </div>

    <div>
      <div class="sec-lbl">30일 가격 추이</div>
      <div class="chart-box">${makeHistChart(hist)}</div>
      <div class="chart-legend">
        <span><i style="background:#3457ff;height:2px"></i>종가</span>
        <span><i style="background:#ff5267"></i>상승일 거래량</span>
        <span><i style="background:#4fa8ff"></i>하락일 거래량</span>
      </div>
    </div>

    <!-- 외국인·기관 순매매 표 -->
    <div class="frgn-section">
      <div class="frgn-header">
        <span class="frgn-ttl">외국인·기관 순매매 거래량 (최근 20일)</span>
        <span class="frgn-src" id="frgn-src-${meta.code}">로딩 중…</span>
      </div>
      <div id="frgn-${meta.code}"><div class="frgn-load">네이버 증권에서 데이터 불러오는 중…</div></div>
    </div>

    <!-- 기관·외국인 순매매 바차트 -->
    <div id="frgn-chart-${meta.code}"></div>

    <!-- 수급주체 -->
    <div class="frgn-section" style="margin-top:0">
      <div id="inv-${meta.code}">
        <div class="frgn-load">수급주체(외국인·기관·개인) 조회 중…</div>
      </div>
    </div>

    <!-- 거래원 -->
    <div>
      <div class="sec-lbl">거래원 창구 (매수/매도 상위 3위)</div>
      <div class="broker-wrap">
        <div>
          <div class="broker-side-ttl" style="color:#ff5267">▲ 매수</div>
          ${brow(buyers,"#ff5267")||'<div style="font-size:11px;color:#5d6680">없음</div>'}
        </div>
        <div>
          <div class="broker-side-ttl" style="color:#4fa8ff">▼ 매도</div>
          ${brow(sellers,"#4fa8ff")||'<div style="font-size:11px;color:#5d6680">없음</div>'}
        </div>
      </div>
      <div style="margin-top:6px;font-size:10px;text-align:right;font-family:monospace;color:${net>=0?"#ff5267":"#4fa8ff"}">
        ${net>=0?"▲":"▼"} 창구 합산 순${net>=0?"매수":"매도"} ${fmt(Math.abs(net))}주
      </div>
    </div>

    <!-- 오늘의 해석 (접힘) -->
    <div>
      <div class="analysis-toggle" onclick="toggleAnalysis('${cid}')">
        <span>📊 오늘의 해석 <span class="${sumCls}" style="margin-left:6px;font-size:11px">${summary}</span></span>
        <span id="${cid}-arrow" style="font-size:12px;color:#5d6680">▼ 펼치기</span>
      </div>
      <div id="${cid}" class="analysis-body">
        ${ins.map(row=>`<div class="arow s-${row.s}">
          <div class="atag">${row.tag}</div>
          <div class="abody"><div class="ahl">${row.hl}</div>
            <div class="adt">${row.dt}</div></div>
        </div>`).join("")}
      </div>
    </div>

    <div style="font-size:10px;color:#5d6680">기준: ${st.UPDATE_TIME||"–"}</div>
  </div>
</div>`;
}

function toggleAnalysis(id){
  const el=document.getElementById(id);
  const arrow=document.getElementById(id+"-arrow");
  el.classList.toggle("open");
  if(arrow) arrow.textContent=el.classList.contains("open")?"▲ 접기":"▼ 펼치기";
}

/* 피어 바차트 (심플) */


/* 매크로 배너 */
function renderMacro(){
  const today=new Date(); today.setHours(0,0,0,0);
  const events=MACRO.map(e=>{const d=new Date(e.d);d.setHours(0,0,0,0);return{...e,diff:Math.round((d-today)/864e5)};})
    .filter(e=>e.diff>=-14&&e.diff<=7).sort((a,b)=>a.diff-b.diff);
  if(!events.length){document.getElementById("macro-banner").innerHTML="";return;}
  const past=events.filter(e=>e.diff<=0&&e.diff>=-14),soon=events.filter(e=>e.diff>0);
  let h='<div id="macro-banner" style="padding:0 20px">';
  if(past.length)h+=`<div class="mac-box mac-today">
    <div class="mac-ttl" style="color:#ffaa00">⚡ 최근 매크로 이벤트</div>
    ${past.map(e=>`<div class="mac-ev" style="${e.result?'border-left:2px solid #ff5267;padding-left:8px;':''}">
      <span class="mac-badge" style="background:rgba(255,170,0,.18);color:${TCLR[e.t]}">${TLBL[e.t]}</span>
      <b>${e.n}</b><span style="color:#8993ad;font-size:11px">${e.diff===0?"오늘":e.diff===-1?"어제":`${Math.abs(e.diff)}일 전`}</span>
      ${e.i==="HIGH"?'<span style="font-size:10px;color:#ff5267;font-weight:700">HIGH</span>':""}
    </div>`).join("")}</div>`;
  if(soon.length)h+=`<div class="mac-box mac-soon" style="margin-top:7px">
    <div class="mac-ttl" style="color:#9db4ff">📅 예정 이벤트</div>
    ${soon.map(e=>`<div class="mac-ev">
      <span class="mac-badge" style="background:rgba(52,87,255,.18);color:${TCLR[e.t]}">${TLBL[e.t]}</span>
      <span>${e.n}</span><span style="color:#8993ad;font-size:11px">D-${e.diff}</span>
      ${e.i==="HIGH"?'<span style="font-size:10px;color:#ff5267;font-weight:700">HIGH</span>':""}
    </div>`).join("")}</div>`;
  document.getElementById("macro-banner").outerHTML=h+"</div>";
  if(past.length)document.getElementById("macro-strip").innerHTML=
    `<span style="font-size:10px;font-weight:700;color:#ffaa00;background:rgba(255,170,0,.1);border:1px solid rgba(255,170,0,.3);border-radius:5px;padding:2px 8px">⚡${past.length}건</span>`;
}

/* 수급주체 비동기 로드 */
async function fetchInvestor(code){
  const el=document.getElementById(`inv-${code}`);
  if(!el)return;
  try{
    const r=await fetch(`/investor?code=${code}`);
    const d=await r.json();
    if(d.source&&d.foreign!==undefined){
      const items=[d.foreign,d.institution,d.individual];
      const maxAbs=Math.max(...items.map(i=>Math.abs(i.net)),1);
      el.innerHTML=`<div class="frgn-header" style="border-bottom:1px solid #1a2338">
        <span class="frgn-ttl">수급주체 순매수 (오늘)</span>
        <span class="frgn-src">${d.source} · ${d.date}</span>
      </div>
      <div class="inv-row">
        ${items.map(item=>{
          const clr=item.net>=0?"#ff5267":"#4fa8ff";
          const pct=(Math.abs(item.net)/maxAbs*80).toFixed(0);
          return`<div class="inv-cell">
            <div class="inv-lbl" style="color:${clr}">${item.label}</div>
            <div class="inv-val" style="color:${clr}">${item.net>=0?"+":""}${fmt(item.net)}<span style="font-size:10px">주</span></div>
            <div style="height:3px;background:#1a2338;border-radius:2px;margin-top:4px">
              <div style="height:100%;width:${pct}%;background:${clr};border-radius:2px"></div>
            </div>
          </div>`;
        }).join("")}
      </div>`;
    } else {
      const s=d.setup||{};
      el.innerHTML=`<div class="inv-setup">
        <b style="color:#9db4ff">수급주체 데이터 소스 연동 필요</b><br>
        <a href="${s.url||"https://openapi.krx.co.kr"}" target="_blank">${s.url||"https://openapi.krx.co.kr"}</a>
        무료 가입 → '투자자별 거래실적' 신청 (1-2일) → <code>shinhan_proxy.py</code> 상단 <code>KRX_API_KEY</code> 입력
      </div>`;
    }
  }catch(e){if(el)el.innerHTML=`<div class="frgn-load">수급주체 조회 실패</div>`;}
}

/* 외국인·기관 비동기 로드 */
async function fetchFrgn(code){
  try{
    const r=await fetch(`/frgn?code=${code}`);
    const d=await r.json();
    const el=document.getElementById(`frgn-${code}`);
    const srcEl=document.getElementById(`frgn-src-${code}`);
    const chartEl=document.getElementById(`frgn-chart-${code}`);
    if(el){el.innerHTML=renderFrgnTable(d);}
    if(srcEl){srcEl.textContent=d.ok?`네이버 증권 · ${d.rows?.length||0}일`:"불러오기 실패";}
    if(chartEl&&d.ok&&d.rows?.length>2){
      chartEl.innerHTML=`<div class="sec-lbl" style="margin-top:6px">기관·외국인 순매매 추이</div>
        <div class="chart-box">${makeFrgnChart(d.rows)}</div>
        <div class="chart-legend">
          <span><i style="background:#ff5267"></i>기관 순매수</span>
          <span><i style="background:#4fa8ff"></i>기관 순매도</span>
          <span><i style="background:#ff9944"></i>외국인 순매수</span>
          <span><i style="background:#88aaff"></i>외국인 순매도</span>
        </div>`;
    }
    window[`_frgnData_${code}`]=d;
    return d;
  }catch(e){ return null; }
}

/* 메인 갱신 */
/* ── 거래 분석 브리핑 (데이터 기반, API 불필요) ─────── */
let _briefingOpen = true;
let _briefingGenerated = false;

function toggleBriefing(){
  const body  = document.getElementById("br-body");
  const arrow = document.getElementById("br-arrow");
  _briefingOpen = !_briefingOpen;
  body.classList.toggle("open", _briefingOpen);
  if(arrow) arrow.textContent = _briefingOpen ? "▲" : "▼";
}

/* ── 단기 전망 & 체크포인트 (규칙 기반) ─────────────── */
function generateOutlook(mainData, kospiData){
  const today=new Date(); today.setHours(0,0,0,0);
  const upcoming=MACRO.map(e=>{
    const d=new Date(e.d); d.setHours(0,0,0,0);
    return{...e,diff:Math.round((d-today)/864e5)};
  }).filter(e=>e.diff>0&&e.diff<=10).sort((a,b)=>a.diff-b.diff);

  if(!upcoming.length&&!mainData["293940"]) return"";

  const parts=[];

  // ① 매크로 이벤트별 리츠 영향 ──────────────────────────
  if(upcoming.length){
    const evRows=upcoming.map(e=>{
      const dTag=`D-${e.diff}`;
      let impact="";
      if(e.t==="F"){
        impact=`<strong>FOMC 금리 결정 (${dTag})</strong> — 미국 기준금리 방향에 따라 글로벌 리츠 변동성 확대. 동결 시 리츠 중립~소폭 긍정, 인상 시 밸류에이션 압박, 인하 시 배당 매력 부각.`;
      } else if(e.t==="B"){
        impact=e.n&&e.n.includes("인상")?`<strong>한국은행 금통위 결과 (${dTag})</strong> — 기준금리 25bp 인상 확정. 자본비용 증가로 리츠 밸류에이션 압박 지속. 배당수익률 상대 매력 저하 — 금리 고점 여부 및 추가 인상 가능성 주시 필요.`:e.n&&e.n.includes("인하")?`<strong>한국은행 금통위 결과 (${dTag})</strong> — 기준금리 인하 확정. 배당수익률 매력 회복, 리츠 섹터 강한 반등 모멘텀 기대.`:`<strong>한국은행 금통위 (${dTag})</strong> — 기준금리 결정이 국내 리츠 배당수익률 매력에 직접 영향. 결과에 따라 방향성 결정.`;
      } else if(e.t==="C"){
        impact=`<strong>미국 CPI (${dTag})</strong> — 예상 상회 시 Fed 긴축 장기화 우려 → 리츠 부정적. 예상 하회 시 금리 인하 기대 강화 → 리츠 긍정. 전월 대비 방향성 주목.`;
      } else if(e.t==="K"){
        impact=`<strong>한국 CPI (${dTag})</strong> — 물가 지속 상승 시 한은 긴축 유지 → 리츠 배당 매력 상대적 약화. 둔화 시 금리 인하 기대 형성.`;
      } else if(e.t==="E"){
        impact=`<strong>옵션만기 (${dTag})</strong> — 프로그램 매매 청산으로 지수 변동성 확대 가능. 리츠 직접 영향은 제한적이나 시장 급변 시 동반 출렁임 주의.`;
      }
      return impact?`<div class="br-para">${impact}</div>`:"";
    }).filter(Boolean).join("");

    parts.push(`<div class="br-stock-ttl">🔮 단기 전망 — 예정 매크로 이벤트 영향</div>
<div class="br-paras">${evRows}</div>`);
  }

  // ② 종목별 기술적 체크포인트 ──────────────────────────
  const stockParts=[];
  for(const [code,name] of [["293940","신한알파리츠"],["404990","신한서부티엔디리츠"]]){
    const raw=mainData[code]; if(!raw) continue;
    const st=raw.results?.STOCK||{};
    const hist=raw.results?.HIST||[];
    const price=n(st.F15001);
    const hi52=n(st.F02133),lo52=n(st.F02155);
    const p52=Math.max(0,Math.min(100,(price-lo52)/(hi52-lo52||1)*100));

    // 최근 5일 추세
    const rec=hist.slice(0,5);
    const up5=rec.filter(h=>h.F15006==="2").length;
    const dn5=rec.filter(h=>h.F15006==="5").length;
    const ret5=hist.length>=5?(n(hist[0].F15001)-n(hist[4].F15001))/n(hist[4].F15001)*100:0;

    // 최근 20일 외국인 추세 (frgn 데이터 있으면)
    const fd=(typeof window!=='undefined'&&window[`_frgnData_${code}`])||null;
    let frgnTrend="";
    if(fd&&fd.rows&&fd.rows.length>=5){
      const frgnSum=fd.rows.slice(0,5).reduce((s,r)=>s+(r.frgn_net||0),0);
      frgnTrend=frgnSum>0?"최근 5일 외국인 순매수 우위 지속 중":"최근 5일 외국인 순매도 우위 지속 중";
    }

    const pts=[];

    // 52주 위치
    if(p52<20){
      pts.push(`52주 저점 근방(저점 대비 +${p52.toFixed(0)}%) — 역사적 저점 구간으로 낙폭 과대 여부 주목. 거래량 동반 시 기술적 반등 가능성.`);
    } else if(p52>80){
      pts.push(`52주 고점 근방(${p52.toFixed(0)}% 위치) — 단기 차익 실현 압력 존재. 추가 상승 위해 거래량 뒷받침 필요.`);
    } else if(p52<40){
      pts.push(`52주 하단권(${p52.toFixed(0)}% 위치) — 추세 전환 신호 확인 전 하락 모멘텀 지속 가능성.`);
    } else {
      pts.push(`52주 중간 구간(${p52.toFixed(0)}% 위치) — 뚜렷한 기술적 지지·저항 없음.`);
    }

    // 단기 모멘텀
    if(ret5<-4){
      pts.push(`최근 5거래일 ${ret5.toFixed(1)}% 급락 — 단기 과매도 구간 진입 가능. 반등 시 저항선 확인 필요.`);
    } else if(ret5>4){
      pts.push(`최근 5거래일 +${ret5.toFixed(1)}% 급등 — 단기 피로감 주의. 거래량 감소 시 차익 실현 가능성.`);
    } else if(dn5>=4){
      pts.push(`최근 5거래일 중 ${dn5}일 하락 — 하락 추세 지속. 지지선 이탈 여부 모니터링 필요.`);
    } else if(up5>=4){
      pts.push(`최근 5거래일 중 ${up5}일 상승 — 단기 상승 추세. 이벤트 결과에 따라 추세 연장 또는 되돌림.`);
    }

    // 외국인 추세
    if(frgnTrend) pts.push(frgnTrend+".");

    // HIGH 임팩트 이벤트 D-5 이내
    const highNear=upcoming.filter(e=>e.diff<=5&&e.i==="HIGH");
    if(highNear.length){
      pts.push(`향후 ${highNear[0].diff}일 내 HIGH 임팩트 이벤트(${TLBL[highNear[0].t]}) — 이벤트 결과 확인 전 변동성 확대 가능. 대응 시나리오 사전 준비 권장.`);
    }

    stockParts.push(`<div class="br-stock-ttl">📌 ${name} — 단기 체크포인트</div>
<div class="br-paras">${pts.map(p=>`<div class="br-para">${p}</div>`).join("")}</div>`);
  }

  if(stockParts.length) parts.push(stockParts.join(""));
  return parts.join("");
}

/* ── 당일/전일 매크로 이벤트 → 리츠 영향 분석 ──────────── */
function generateMacroImpact(){
  const todayBase=new Date();todayBase.setHours(0,0,0,0);
  const recent=MACRO.map(e=>{
    const d=new Date(e.d);d.setHours(0,0,0,0);
    return{...e,diff:Math.round((d-todayBase)/864e5)};
  }).filter(e=>e.diff>=-1&&e.diff<=0&&e.i==="HIGH");

  if(!recent.length) return "";

  const impacts=recent.map(e=>{
    const when=e.diff===0?"오늘":"어제";
    const name=e.n;
    let title="",body="";

    if(e.t==="B"){ // 한국은행 금통위
      if(e.result==="인상"||name.includes("인상")){
        const bpMatch=name.match(/(\d+)bp/);
        const bp=bpMatch?bpMatch[1]:"N";
        title=`🏦 한국은행 기준금리 ${bp}bp 인상 (${when})`;
        body=`<div class="br-para"><strong>리츠 밸류에이션 영향</strong> `
          +`무위험 금리 상승으로 리츠 요구수익률(Cap Rate) 상향 압력이 가해집니다. `
          +`배당수익률 대비 국채금리 스프레드가 축소되며 상대적 매력도가 단기적으로 약화될 수 있습니다.</div>`
          +`<div class="br-para"><strong>자본비용 영향</strong> `
          +`리츠 차입금(변동금리 대출·리파이낸싱) 이자비용 증가 → FFO(운용자금흐름) 하방 압력. `
          +`부채비율이 높은 종목일수록 영향이 큽니다. 신한알파리츠·서부리츠의 LTV 및 고정/변동 금리 비중 확인 권장.</div>`
          +`<div class="br-para"><strong>주가 영향 시나리오</strong> `
          +`금리 인상 초기 리츠 주가는 통상 약세를 보이나, `
          +`임대료 상승(인플레이션 연동)으로 장기 실물 가치는 방어됩니다. `
          +`단기 하방 리스크 주시, 금리 고점 도달 시 매력 재부각 가능.</div>`;
      } else if(e.result==="인하"||name.includes("인하")){
        const bpMatch=name.match(/(\d+)bp/);
        const bp=bpMatch?bpMatch[1]:"N";
        title=`🏦 한국은행 기준금리 ${bp}bp 인하 (${when})`;
        body=`<div class="br-para"><strong>리츠 밸류에이션 영향</strong> `
          +`무위험 금리 하락 → 리츠 배당수익률 상대 매력 강화. Cap Rate 하향 압력으로 자산가치 상승 기대.</div>`
          +`<div class="br-para"><strong>자본비용 영향</strong> `
          +`차입 이자비용 경감 → FFO 개선 기대. 리파이낸싱 유리한 환경. 신규 편입 ETF 수요 증가 가능.</div>`
          +`<div class="br-para"><strong>주가 영향 시나리오</strong> `
          +`금리 인하 확정 시 리츠 섹터 강한 반등 모멘텀 형성. `
          +`배당 매력 부각으로 외국인 및 기관 유입 가능성 높아집니다.</div>`;
      } else {
        title=`🏦 한국은행 금통위 (${when}) — 금리 동결`;
        body=`<div class="br-para"><strong>리츠 영향</strong> `
          +`기준금리 동결로 현 수준의 자본비용 유지. 리츠 주가에 중립적 영향. `
          +`다음 금통위 전까지 글로벌 금리 흐름(미 연준) 및 국내 물가 동향 주시 필요.</div>`;
      }
    } else if(e.t==="F"){ // FOMC
      const isHike=name.includes("인상"); const isCut=name.includes("인하");
      title=`🇺🇸 FOMC 금리 결정 (${when})`;
      body=`<div class="br-para"><strong>글로벌 리츠 영향</strong> `
        +(isHike?"미국 금리 인상으로 글로벌 자본이탈 압력, 외국인 매도 강화 가능. 원/달러 환율 상승 시 수입물가 부담."
          :isCut?"미국 금리 인하로 글로벌 유동성 개선, 외국인 자금 유입 기대. 리츠 섹터 전반 매력 회복."
          :"금리 동결. 글로벌 리츠 시장 현 수준 유지.")
        +` 국내 리츠의 경우 해외 투자자 비중과 환헤지 여부에 따라 영향 차별화.</div>`;
    } else if(e.t==="C"){ // CPI
      title=`📊 미국 CPI 발표 (${when})`;
      body=`<div class="br-para"><strong>리츠 영향</strong> `
        +(name.includes("상회")||name.includes("서프라이즈")
          ?"예상 상회 → 연준 긴축 장기화 우려 재부각. 글로벌 금리 상승 → 리츠 밸류에이션 압박."
          :name.includes("하회")?"예상 하회 → 금리 인하 기대 강화. 리츠 섹터 반등 계기."
          :"물가 지표 결과에 따라 연준 통화정책 방향성에 영향. 장기 금리 변동 주시.")
        +`</div>`;
    }

    if(!title) return "";
    return `<div style="background:rgba(255,82,103,.06);border:1px solid rgba(255,82,103,.25);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="font-size:13px;font-weight:700;color:#ff5267;margin-bottom:8px">${title}</div>
      <div class="br-paras">${body}</div>
    </div>`;
  }).filter(Boolean).join("");

  return impacts?`<div style="margin:10px 0">${impacts}</div>`:"";
}

/* ── 단기 전망 & 체크포인트 ─────────────────────────────── */
function generateBriefing(mainData, kospiData, frgnData, peerData){
  const now=new Date();
  const wd=["일","월","화","수","목","금","토"][now.getDay()];
  const dateStr=`${now.getFullYear()}. ${now.getMonth()+1}. ${now.getDate()}. (${wd}) ${String(now.getHours()).padStart(2,"0")}:${String(now.getMinutes()).padStart(2,"0")}`;

  const bullets=[], sections=[];
  const peers=(peerData&&peerData.stocks)||[];
  const peerAvg=peers.length?peers.reduce((s,p)=>s+(p.chgR||0),0)/peers.length:null;
  const kospiR=parseFloat(kospiData?.F15004||"0");

  for(const [code,name] of [["293940","신한알파리츠"],["404990","신한서부티엔디리츠"]]){
    const raw=mainData[code]; if(!raw) continue;
    const r=raw.results||{};
    const st=r.STOCK||{}, mem=r.MEMBER||{};
    const hist=r.HIST||[], intra=r.INTRA||[];

    const price=n(st.F15001), prev=n(st.F15007);
    const chg=n(st.F15472), chgR=parseFloat(st.F15004||"0");
    const dir=st.F15006||"3";
    const high=n(st.F15010), low=n(st.F15011), vol=n(st.F15015), amt=n(st.F15023);
    const sign=dir==="2"?"+":"";

    // ── 거래량 분석 ────────────────────────────────────────
    const slice=hist.slice(0,30);
    const avgVol=slice.length?slice.reduce((s,h)=>s+n(h.F15015),0)/slice.length:0;
    const vr=avgVol>0?vol/avgVol*100:0;
    let volSentence=`오늘 ${fmt(vol)}주 거래됐습니다`;
    if(avgVol>0){
      volSentence+=`, 30일 평균(${fmt(Math.round(avgVol))}주)의 ${vr.toFixed(0)}%`;
      if(vr<55) volSentence+=" 수준으로 매수세가 뚜렷하지 않은 공백장세였습니다";
      else if(vr<80) volSentence+=" 수준으로 거래가 다소 부진했습니다";
      else if(vr<=120) volSentence+=" 수준으로 평이한 거래였습니다";
      else volSentence+=` 수준으로 평소보다 활발하게 거래됐습니다`;
    }
    volSentence+=`. 거래대금 ${fmtA(amt)}.`;

    // ── 장중 흐름 분석 ─────────────────────────────────────
    const chrono=[...intra].reverse();
    const prices=chrono.map(t=>n(t.F20008)).filter(p=>p>0);
    let flowSentence="";
    if(prices.length>=5){
      const openP=prices[0], closeP=prices[prices.length-1];
      const halfIdx=Math.floor(prices.length/2);
      const firstAvg=prices.slice(0,halfIdx).reduce((s,p)=>s+p,0)/halfIdx;
      const secondAvg=prices.slice(halfIdx).reduce((s,p)=>s+p,0)/(prices.length-halfIdx);
      const openVsPrev=(openP-prev)/prev*100;
      const secondVsFirst=(secondAvg-firstAvg)/firstAvg*100;
      const closePos=(closeP-low)/(high-low||1);

      // 시가
      if(Math.abs(openVsPrev)>0.3){
        flowSentence=`시가 ${fmt(Math.round(openP))}원, 전일 대비 ${openVsPrev.toFixed(1)}% 갭${openVsPrev>0?"업":"다운"} 출발.`;
      } else {
        flowSentence=`시가 ${fmt(Math.round(openP))}원으로 전일 근방 출발.`;
      }

      // 장중 흐름 패턴
      if(secondVsFirst>0.5 && closePos>0.7){
        flowSentence+=" 오전 약세 후 오후 들어 매수세 유입되며 강세 전환.";
      } else if(secondVsFirst<-0.5 && closePos<0.3){
        flowSentence+=" 오전 대비 오후 들어 매도 압력 강해지며 저가 마감.";
      } else if(closePos<0.12){
        flowSentence+=" 장 내내 하락 압력이 이어지며 저가 근방 마감. 장 마감까지 매수 유입 제한.";
      } else if(closePos>0.88){
        flowSentence+=" 장 내내 강세 유지하며 고가 근방에서 마감.";
      } else {
        flowSentence+=` 특별한 방향성 없이 ${fmt(low)}~${fmt(high)}원 박스권 흐름.`;
      }

      // VWAP
      const lt=intra[0]||{};
      const cumAmt=n(lt.F20013), cumVol=n(lt.F20012);
      if(cumVol>0){
        const vwap=cumAmt/cumVol;
        const vd=(price-vwap)/vwap*100;
        flowSentence+=` 장중 평균매가(VWAP) ${fmt(Math.round(vwap))}원 대비 현재가 ${Math.abs(vd).toFixed(1)}% ${vd>=0?"상회":"하회"}.`;
      }
    }

    // ── 외국인·기관·개인 분석 ─────────────────────────────
    const frgn=frgnData&&frgnData[code];
    let investorSentence="";
    let instNet=null, frgnNet=null, frgnRatio=null, frgnRatioChg=null;

    if(frgn&&frgn.rows&&frgn.rows.length>0){
      const row0=frgn.rows[0];
      instNet=row0.inst_net;
      frgnNet=row0.frgn_net;
      frgnRatio=parseFloat(row0.frgn_ratio)||null;
      if(frgn.rows.length>1) frgnRatioChg=frgnRatio-(parseFloat(frgn.rows[1].frgn_ratio)||0);
      const indivNet=(instNet!=null&&frgnNet!=null)?-(instNet+frgnNet):null;

      const instTxt=`기관 ${instNet>=0?"+":""}${fmt(instNet)}주 ${instNet>=0?"순매수":"순매도"}`;
      const frgnTxt=`외국인 ${frgnNet>=0?"+":""}${fmt(frgnNet)}주 ${frgnNet>=0?"순매수":"순매도"}${frgnRatio!==null?` (보유율 ${frgnRatio}%${frgnRatioChg!==null?`, 전일비 ${frgnRatioChg>=0?"+":""}${frgnRatioChg.toFixed(2)}%p`:""})` : ""}`;
      const indivTxt=indivNet!==null?`개인 추정 ${indivNet>=0?"+":""}${fmt(Math.round(indivNet))}주 ${indivNet>=0?"순매수":"순매도"} (기관+외국인 역방향 추정)`:"";

      investorSentence=[instTxt, frgnTxt, indivTxt].filter(Boolean).join(" · ")+".";

      // 주목할 흐름
      if(frgnNet<0&&frgnRatioChg<-0.03){
        investorSentence+=" 외국인 보유율 빠른 감소 — 리츠 ETF 환매 또는 기관 유출 가능성 주목.";
      } else if(frgnNet>0&&frgnRatioChg>0.03){
        investorSentence+=" 외국인 보유율 증가세 — 리츠 ETF 설정 또는 외국인 기관 유입 신호.";
      }
    } else {
      // 거래원 기반 추정
      const buyers=BN.map((k,i)=>({name:mem[k],vol:n(mem[BV[i]])})).filter(b=>b.name&&b.vol>0);
      const sellers=SN.map((k,i)=>({name:mem[k],vol:n(mem[SV[i]])})).filter(s=>s.name&&s.vol>0);
      const net=buyers.reduce((s,b)=>s+b.vol,0)-sellers.reduce((s,b)=>s+b.vol,0);
      const fS=sellers.length&&isForeign(sellers[0].name);
      const fB=buyers.length&&isForeign(buyers[0].name);
      investorSentence=`창구 합산 ${net>=0?"순매수":"순매도"} ${fmt(Math.abs(net))}주.`
        +(fS&&net<0?" 외국계 창구 매도 상위 — 외국인 기관 유출 가능성.":"")
        +(fB&&net>0?" 외국계 창구 매수 상위 — 외국인 기관 유입 가능성.":"")
        +" (정확한 외국인·기관·개인 수치는 KRX 투자자별 거래실적 확인 권장)";
    }


    // ── 요약 불릿 ──────────────────────────────────────────
    const volTag=vr<55?"저거래량":vr>130?"거래량 급증":"평이한 거래";
    let invTag="";
    if(instNet!==null&&frgnNet!==null){
      const abs_i=Math.abs(instNet), abs_f=Math.abs(frgnNet);
      const big=abs_i>abs_f?"기관":"외국인";
      const bigVal=abs_i>abs_f?instNet:frgnNet;
      invTag=`, ${big} ${bigVal>=0?"순매수":"순매도"} 우위`;
    }
    bullets.push(`${name}: ${volTag}${invTag}, ${sign}${chgR.toFixed(2)}%`);

    sections.push(`<div class="br-stock-ttl">● ${name} (${code})</div>
<div class="br-price-line" style="color:${dc(dir)}">${fmt(price)}원 ${da(dir)} ${fmt(Math.abs(chg))}원 (${sign}${chgR.toFixed(2)}%)</div>
<div class="br-paras">
  ${flowSentence?`<div class="br-para"><strong>장중 흐름</strong> ${flowSentence}</div>`:""}
  <div class="br-para"><strong>거래량</strong> ${volSentence}</div>
  <div class="br-para"><strong>외국인·기관·개인</strong> ${investorSentence}</div>

</div>`);
  }


  // ── 오늘/어제 매크로 이벤트 영향 분석 ─────────────────────
  const macroImpact = generateMacroImpact();
  const outlook = generateOutlook(mainData, kospiData);

  return `<div class="br-date">${dateStr} 기준</div>
<div class="br-summary-ttl">📋 핵심 요약</div>
<ul>${bullets.map(b=>`<li>${b}</li>`).join("")}</ul>
${macroImpact}
${sections.join("")}
${outlook}
<div class="br-disc">장중 흐름·거래량·VWAP: Koscom API 실시간 / 외국인·기관: 네이버 증권 frgn 기준 (장마감 후 확정) / 개인: 기관+외국인 역방향 추정 / 단기 전망: 과거 데이터 및 규칙 기반 참고 자료이며 투자 권유가 아닙니다.</div>`;
}

async function renderBriefing(mainData, kospiData, frgnData){
  const inner=document.getElementById("br-inner");
  const timeEl=document.getElementById("br-time");
  const btn=document.getElementById("br-btn");
  const body=document.getElementById("br-body");
  if(!inner) return;

  try{
    // 피어 캐시 (실패해도 브리핑 계속)
    let peerData=null;
    try{
      const cr=await fetch("/peer-cache");
      if(cr.ok) peerData=await cr.json();
    }catch(_){}

    // 브리핑 생성
    let html="";
    try{
      html=generateBriefing(mainData,kospiData,frgnData||{},peerData);
    }catch(e){
      // generateBriefing 오류 시 상세 로그 + 기본 메시지
      console.error("generateBriefing 오류:", e);
      inner.className="br-err";
      inner.textContent="브리핑 생성 오류: "+e.message+" (콘솔에서 상세 확인)";
      return;
    }

    inner.className="br-content";
    inner.innerHTML=html;
    if(timeEl) timeEl.textContent=new Date().toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"})+" 기준";
    if(btn) btn.style.display="inline";
    if(body) body.classList.toggle("open",_briefingOpen);
    _briefingGenerated=true;
  }catch(e){
    console.error("renderBriefing 오류:", e);
    if(inner){ inner.className="br-err"; inner.textContent="브리핑 오류: "+e.message; }
  }
}

async function regenerateBriefing(){
  _briefingGenerated=false;
  const inner=document.getElementById("br-inner");
  if(inner){inner.className="br-load";inner.textContent="분석 중…";}
  await refresh();
}

async function updatePeers(){
  const btn=document.getElementById("btn-peers");
  if(btn){btn.textContent="수집 중…";btn.disabled=true;}
  try{
    await fetch("/update-peers",{method:"POST"});
    let secs=35;
    const timer=setInterval(()=>{
      secs--;
      if(btn) btn.textContent=`수집 중… (${secs}초)`;
      if(secs<=0){
        clearInterval(timer);
        refresh();
        if(btn){btn.textContent="✓ 완료";btn.disabled=false;
          setTimeout(()=>{btn.textContent="📊 리츠 업데이트";},3000);}
      }
    },1000);
  }catch(e){
    if(btn){btn.textContent="📊 리츠 업데이트";btn.disabled=false;}
  }
}

async function refresh(){
  document.getElementById("pulse").style.background="#facc15";
  try{
    // 1. 당사 종목 (상세 데이터)
    const [r1,r2]=await Promise.all(
      STOCKS.map(s=>fetch(`/getStockInfo?code=${s.code}&auth_key=${AUTH}&gubun=K`).then(r=>r.json()))
    );
    const mainData={"293940":r1,"404990":r2};
    const kospi=r1?.results?.INDEX?.KOSPI;
    const kosdaq=r1?.results?.INDEX?.KOSDAQ;

        document.getElementById("cards").innerHTML=
      STOCKS.map(s=>mainData[s.code]?renderCard(s,mainData[s.code],kospi):"").join("");
    if(kospi){
      document.getElementById("kospi-p").textContent=fmt(n(kospi.F15001));
      const e=document.getElementById("kospi-c");
      e.textContent=`${da(kospi.F15006)} ${fmt(Math.abs(n(kospi.F15472)))} (${kospi.F15004}%)`;
      e.style.color=dc(kospi.F15006);
    }
    if(kosdaq){
      document.getElementById("kosdaq-p").textContent=fmt(n(kosdaq.F15001));
      const e=document.getElementById("kosdaq-c");
      e.textContent=`${da(kosdaq.F15006)} ${fmt(Math.abs(n(kosdaq.F15472)))} (${kosdaq.F15004}%)`;
      e.style.color=dc(kosdaq.F15006);
    }
    renderMacro();
    // 브리핑: 첫 로드 또는 수동 재생성 시만 업데이트 (30초마다 갱신 불필요)
    if(!_briefingGenerated){
      renderBriefing(mainData, kospi, null);
    }
    // 외국인·기관 표 + 수급주체 비동기 로드 (완료되면 브리핑에도 보유율 반영)
    STOCKS.forEach(s=>{
      fetchFrgn(s.code).then(()=>{
        // frgn 데이터 도착 후 브리핑 업데이트
        const frgnMap={};
        STOCKS.forEach(ss=>{
          const el=document.getElementById(`frgn-${ss.code}`);
          frgnMap[ss.code]=window[`_frgnData_${ss.code}`];
        });
        renderBriefing(mainData, kospi, frgnMap);
      });
      fetchInvestor(s.code);
    });

    document.getElementById("upd").innerHTML=
      new Date().toLocaleTimeString("ko-KR")+" 업데이트"
      +'<span class="pulse" id="pulse" style="background:#4ade80"></span>';
  }catch(e){
    document.getElementById("pulse").style.background="#4fa8ff";
    if(!document.querySelector(".card"))
      document.getElementById("cards").innerHTML=`<div class="loading" style="color:#ff5267">오류: ${e.message}</div>`;
  }
}

refresh();
setInterval(refresh,30000);
setInterval(()=>{document.getElementById("clock").textContent=
  new Date().toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",second:"2-digit"});},1000);
</script></body></html>"""

# ══════════════════════════════════════════════════════════════════
#  COMPARE PAGE (심플 — 전체 리츠 바차트)
# ══════════════════════════════════════════════════════════════════
COMPARE = r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>상장리츠 전체 비교</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0e1a;color:#eceff6;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;font-size:14px}
.num{font-family:'Consolas','D2Coding',monospace}
a{text-decoration:none;color:inherit}
.hdr{background:linear-gradient(135deg,#0d1430,#0a0e1a);border-bottom:1px solid #232e47;padding:12px 22px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.back{font-size:11px;color:#9db4ff;background:rgba(52,87,255,.15);border:1px solid #3457ff;border-radius:5px;padding:3px 9px}
.btn{background:rgba(52,87,255,.15);border:1px solid #3457ff;border-radius:6px;color:#9db4ff;font-size:11px;padding:3px 10px;cursor:pointer}
.upd-btn{background:rgba(255,170,0,.12);border-color:rgba(255,170,0,.4);color:#ffaa00}
.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;margin-left:5px;vertical-align:middle}
/* kpi */
.kpi-strip{background:#111827;border-bottom:1px solid #232e47;display:flex;padding:0 22px;overflow-x:auto;gap:0}
.kpi{padding:9px 20px 9px 0;margin-right:18px;border-right:1px solid #1a2338;display:flex;flex-direction:column;gap:2px}
.kpi-l{font-size:10px;color:#5d6680;text-transform:uppercase}
.kpi-v{font-size:15px;font-weight:700;font-family:'Consolas','D2Coding',monospace}
/* sector pills */
.sec-strip{padding:12px 22px 0;display:flex;gap:7px;flex-wrap:wrap}
.sp{background:#161f33;border-radius:7px;padding:7px 11px;min-width:80px}
.sp-l{font-size:10px;color:#8993ad;margin-bottom:2px}
.sp-v{font-size:12px;font-weight:700;font-family:'Consolas','D2Coding',monospace}
.sp-n{font-size:10px;color:#5d6680}
/* filter */
.filters{padding:10px 22px 0;display:flex;gap:6px;flex-wrap:wrap}
.fchip{font-size:11px;font-weight:600;padding:3px 11px;border-radius:16px;cursor:pointer;border:1px solid #232e47;background:#161f33;color:#8993ad;transition:all .1s;user-select:none}
.fchip.active{background:rgba(52,87,255,.2);border-color:#3457ff;color:#9db4ff}
/* chart */
.chart-wrap{padding:12px 22px 4px}
.chart-hdr{display:grid;grid-template-columns:145px 1fr 62px 70px 76px;gap:6px;
  padding:0 4px 7px;font-size:10px;color:#5d6680;text-transform:uppercase;
  border-bottom:1px solid #1a2338;margin-bottom:3px}
.crow{display:grid;grid-template-columns:145px 1fr 62px 70px 76px;gap:6px;
  align-items:center;padding:3px 4px;border-radius:5px;transition:background .1s}
.crow:hover{background:#161f33}
.crow.ours{background:rgba(52,87,255,.07);outline:1px solid rgba(52,87,255,.22)}
.cname{font-size:12px;display:flex;align-items:center;gap:4px;overflow:hidden}
.star{color:#ffaa00;flex-shrink:0}
.cname-txt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sdot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.bar-wrap{height:9px;background:#1a2338;border-radius:5px;overflow:hidden}
.bar-fill{height:100%;border-radius:5px;transition:width .4s}
.cpct{font-size:12px;font-weight:700;text-align:right;font-family:'Consolas','D2Coding',monospace}
.ccap{font-size:11px;text-align:right;color:#cfd4e4;font-family:'Consolas','D2Coding',monospace}
.csec{font-size:10px;color:#5d6680;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sep{text-align:center;font-size:10px;color:#3b4564;padding:5px 0;letter-spacing:.06em}
.fnote{padding:12px 22px 18px;font-size:10px;color:#5d6680;line-height:1.7}
.loading{padding:40px;text-align:center;color:#8993ad}
const SC={
  "오피스":"#3457ff","오피스(해외)":"#7b9aff","리테일":"#ff9500",
  "물류":"#34c759","물류(해외)":"#5ac878","복합":"#ff5267",
  "인프라":"#a78bfa","호텔":"#ffcc00","주거":"#4fa8ff",
};
</style></head>
<body>
<div class="hdr">
  <div style="display:flex;align-items:center;gap:12px">
    <a class="back" href="/">← 대시보드</a>
    <div><div style="font-size:17px;font-weight:700">📈 상장리츠 전체 비교</div>
      <div id="hdr-sub" style="font-size:11px;color:#8993ad">로딩 중…</div></div>
  </div>
  <div style="display:flex;align-items:center;gap:7px;font-size:11px;color:#8993ad">
    <span class="num" id="clock" style="font-size:12px;color:#eceff6"></span>
    <span id="upd">연결 중<span class="pulse" id="pulse" style="background:#8993ad"></span></span>
    <button class="btn" onclick="load()">↺ 갱신</button>
    <button id="btn-upd" class="btn upd-btn" onclick="triggerUpdate()">🔄 장마감 업데이트</button>
  </div>
</div>

<div class="kpi-strip" id="kpi-strip"><div class="kpi"><div class="kpi-l">로딩</div><div class="kpi-v">–</div></div></div>
<div class="sec-strip" id="sec-strip"></div>
<div class="filters" id="filters"></div>
<div class="chart-wrap">
  <div class="chart-hdr"><div>종목</div><div>전날 대비 등락률</div><div style="text-align:right">등락률</div><div style="text-align:right">시총</div><div>섹터</div></div>
  <div id="chart-rows"><div class="loading">데이터 불러오는 중…</div></div>
</div>
<div class="fnote" id="fnote"></div>

<script>
const AUTH="aeolBLFf3IeMzDdJ2g4zfT75Jd4CPhhd";
const ALL=[
  {code:"088980",short:"맥쿼리인프라",  sector:"인프라"},
  {code:"145270",short:"케이탑리츠",    sector:"오피스"},
  {code:"204210",short:"모두투어리츠",  sector:"호텔"},
  {code:"293940",short:"신한알파리츠",  sector:"오피스",   ours:true},
  {code:"330590",short:"롯데리츠",      sector:"리테일"},
  {code:"334890",short:"이지스밸류",    sector:"오피스"},
  {code:"338100",short:"NH프라임리츠",  sector:"오피스"},
  {code:"348950",short:"JR글로벌리츠",  sector:"오피스(해외)"},
  {code:"357120",short:"마스턴프리미어",sector:"복합"},
  {code:"365550",short:"ESR켄달스퀘어", sector:"물류"},
  {code:"370920",short:"이지스레지던스",sector:"주거"},
  {code:"377190",short:"D&D플랫폼",     sector:"복합"},
  {code:"395400",short:"SK리츠",        sector:"복합"},
  {code:"396690",short:"미래에셋글로벌",sector:"물류(해외)"},
  {code:"404990",short:"신한서부T&D",   sector:"복합",   ours:true},
  {code:"417180",short:"코람코라이프",  sector:"인프라"},
  {code:"432320",short:"KB스타리츠",    sector:"오피스(해외)"},
  {code:"448730",short:"삼성FN리츠",    sector:"오피스"},
  {code:"451190",short:"코람코더원",    sector:"오피스"},
  {code:"451800",short:"한화리츠",      sector:"오피스"},
];
const SC={
  "오피스":"#3457ff","오피스(해외)":"#7b9aff","리테일":"#ff9500",
  "물류":"#34c759","물류(해외)":"#5ac878","복합":"#ff5267",
  "인프라":"#a78bfa","호텔":"#ffcc00","주거":"#4fa8ff",
};
function n(s){return parseFloat((s||"0").toString().replace(/,/g,""))||0;}
function fmt(v){return Math.round(v).toLocaleString("ko-KR");}
function fmtA(v){
  if(v>=1e12)return(v/1e12).toFixed(1)+"조";
  if(v>=1e10)return Math.round(v/1e8)+"억";
  if(v>=1e8)return(v/1e8).toFixed(1)+"억";
  return fmt(v);
}
function dc(r){return r>=0?"#ff5267":"#4fa8ff";}
function pct(r){return`${r>=0?"+":""}${r.toFixed(2)}%`;}

let allStocks=[],activeFilter="전체",kospiR=0,dataSource="";

async function triggerUpdate(){
  const b=document.getElementById("btn-upd");
  if(b){b.textContent="수집 중 (약 30초)…";b.disabled=true;}
  try{
    await fetch("/update-peers",{method:"POST"});
    setTimeout(async()=>{await load();if(b){b.textContent="✓ 완료";setTimeout(()=>{b.textContent="🔄 장마감 업데이트";b.disabled=false;},3000);}},36000);
  }catch(e){if(b){b.textContent="오류";b.disabled=false;}}
}

async function load(){
  document.getElementById("pulse").style.background="#facc15";
  try{
    // 캐시 우선
    try{
      const cr=await fetch("/peer-cache");
      if(cr.ok){
        const cache=await cr.json();
        if((cache.stocks||[]).length>0){
          allStocks=cache.stocks.map(s=>{const meta=ALL.find(a=>a.code===s.code)||{};return{...meta,...s};});
          dataSource=`캐시 · ${cache.updated_at_str||""}`;
          const ki=await fetch(`/getStockInfo?code=293940&auth_key=${AUTH}&gubun=K`).then(r=>r.json()).catch(()=>null);
          kospiR=parseFloat(ki?.results?.INDEX?.KOSPI?.F15004||"0");
          render(); setUpd(cache.updated_at_str||"캐시 로드됨");
          return;
        }
      }
    }catch(e){}
    // 실시간 폴백
    const res=await Promise.all(ALL.map(p=>
      fetch(`/getStockBasic?code=${p.code}&auth_key=${AUTH}&gubun=K`).then(r=>r.json()).catch(()=>null)));
    allStocks=ALL.map((p,i)=>{
      const raw=res[i];if(!raw)return null;
      const st=Array.isArray(raw.results)?raw.results[0]:raw.results;
      if(!st||!st.F15001)return null;
      return{...p,price:n(st.F15001),chgR:parseFloat(st.F15004||"0"),cap:n(st.F15028)};
    }).filter(Boolean);
    const ki=await fetch(`/getStockInfo?code=293940&auth_key=${AUTH}&gubun=K`).then(r=>r.json()).catch(()=>null);
    kospiR=parseFloat(ki?.results?.INDEX?.KOSPI?.F15004||"0");
    dataSource="Koscom 실시간";
    render(); setUpd(new Date().toLocaleTimeString("ko-KR")+" 업데이트");
  }catch(e){
    document.getElementById("chart-rows").innerHTML=`<div class="loading" style="color:#ff5267">오류: ${e.message}</div>`;
    document.getElementById("pulse").style.background="#4fa8ff";
  }
}

function setUpd(msg){
  document.getElementById("upd").innerHTML=msg+'<span class="pulse" id="pulse" style="background:#4ade80"></span>';
  const hs=document.getElementById("hdr-sub");
  if(hs)hs.textContent=`${allStocks.length}개 종목 · ${dataSource}`;
}

function render(){
  if(!allStocks.length)return;
  const filtered=activeFilter==="전체"?allStocks:allStocks.filter(s=>s.sector===activeFilter||s.sector.startsWith(activeFilter.split("(")[0]));
  const sorted=[...filtered].sort((a,b)=>b.chgR-a.chgR);
  const maxAbs=Math.max(...sorted.map(s=>Math.abs(s.chgR)),0.5);
  const up=sorted.filter(s=>s.chgR>0).length,dn=sorted.filter(s=>s.chgR<0).length;
  const avg=sorted.reduce((s,p)=>s+p.chgR,0)/sorted.length;
  const gap=avg-kospiR,best=sorted[0],worst=sorted[sorted.length-1];

  document.getElementById("kpi-strip").innerHTML=`
    <div class="kpi"><div class="kpi-l">상승/하락</div>
      <div class="kpi-v"><span style="color:#ff5267">${up}▲</span> / <span style="color:#4fa8ff">${dn}▼</span></div>
      <div style="font-size:10px;color:#5d6680">${sorted.length}개 종목</div></div>
    <div class="kpi"><div class="kpi-l">리츠 평균</div>
      <div class="kpi-v" style="color:${dc(avg)}">${pct(avg)}</div></div>
    <div class="kpi"><div class="kpi-l">vs KOSPI</div>
      <div class="kpi-v" style="color:${dc(gap)}">${gap>=0?"+":""}${gap.toFixed(2)}%p</div>
      <div style="font-size:10px;color:#5d6680">KOSPI ${pct(kospiR)}</div></div>
    <div class="kpi"><div class="kpi-l">최강</div>
      <div class="kpi-v" style="color:#ff5267">${best.short}</div>
      <div style="font-size:10px;color:#ff5267">${pct(best.chgR)}</div></div>
    <div class="kpi"><div class="kpi-l">최약</div>
      <div class="kpi-v" style="color:#4fa8ff">${worst.short}</div>
      <div style="font-size:10px;color:#4fa8ff">${pct(worst.chgR)}</div></div>`;

  // 섹터 평균
  const secMap={};
  allStocks.forEach(s=>{if(!secMap[s.sector])secMap[s.sector]={sum:0,cnt:0};secMap[s.sector].sum+=s.chgR;secMap[s.sector].cnt++;});
  const secs=Object.entries(secMap).sort((a,b)=>b[1].sum/b[1].cnt-a[1].sum/a[1].cnt);
  document.getElementById("sec-strip").innerHTML=
    `<div style="font-size:10px;color:#5d6680;width:100%;padding-bottom:3px">섹터별 평균</div>`+
    secs.map(([k,v])=>{const a=v.sum/v.cnt;return`<div class="sp">
      <div class="sp-l" style="color:${SC[k]||"#8993ad"}">${k}</div>
      <div class="sp-v" style="color:${dc(a)}">${pct(a)}</div>
      <div class="sp-n">${v.cnt}개</div>
    </div>`;}).join("")+
    `<div class="sp" style="background:#0a0e1a;border:1px solid #3457ff30">
      <div class="sp-l" style="color:#3457ff">KOSPI</div>
      <div class="sp-v" style="color:${dc(kospiR)}">${pct(kospiR)}</div>
    </div>`;

  // 섹터 필터
  const sectors=["전체",...new Set(allStocks.map(s=>s.sector))];
  document.getElementById("filters").innerHTML=sectors.map(s=>`
    <div class="fchip ${s===activeFilter?"active":""}" onclick="setFilter('${s}')"
      style="${s!=="전체"&&s===activeFilter?`background:${SC[s]||"#3457ff"}20;border-color:${SC[s]||"#3457ff"};color:${SC[s]||"#3457ff"};`:""}">
      ${s!=="전체"?`<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${SC[s]||"#8993ad"};margin-right:3px;vertical-align:middle"></span>`:""}${s}
    </div>`).join("");

  // 바차트
  const pos=sorted.filter(s=>s.chgR>=0),neg=sorted.filter(s=>s.chgR<0);
  function rowHtml(s){
    const w=(Math.abs(s.chgR)/maxAbs*100).toFixed(1);
    const clr=dc(s.chgR);
    return`<div class="crow ${s.ours?"ours":""}">
      <div class="cname">
        ${s.ours?'<span class="star">★</span>':""}
        <span class="sdot" style="background:${SC[s.sector]||"#8993ad"}"></span>
        <span class="cname-txt" title="${s.short}">${s.short}</span>
      </div>
      <div class="bar-wrap"><div class="bar-fill" style="width:${w}%;background:${clr}"></div></div>
      <div class="cpct" style="color:${clr}">${pct(s.chgR)}</div>
      <div class="ccap">${s.cap>0?fmtA(s.cap):"–"}</div>
      <div class="csec">${s.sector}</div>
    </div>`;
  }
  document.getElementById("chart-rows").innerHTML=
    pos.map(rowHtml).join("")+
    (pos.length&&neg.length?'<div class="sep">── 0% 기준선 ──</div>':"")
    +neg.map(rowHtml).join("");

  document.getElementById("fnote").innerHTML=
    `★ 당사 관리종목 &nbsp;·&nbsp; 점 색상 = 섹터 &nbsp;·&nbsp; ${allStocks.length}/${ALL.length}개 종목`
    +`<br>장마감 업데이트 버튼으로 전일 종가 기준 스냅샷 저장 → 다음날 아침부터 즉시 로드`;
}

function setFilter(f){activeFilter=f;render();}
setInterval(()=>{document.getElementById("clock").textContent=
  new Date().toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",second:"2-digit"});},1000);
load(); setInterval(load,60000);
</script></body></html>"""

# ══════════════════════════════════════════════════════════════════
#  피어 스냅샷 수집 (네이버 → peer_cache.json)
# ══════════════════════════════════════════════════════════════════
PEER_CODES = [
    ("088980","맥쿼리인프라","인프라"),    ("145270","케이탑리츠","오피스"),
    ("204210","모두투어리츠","호텔"),       ("293940","신한알파리츠","오피스"),
    ("330590","롯데리츠","리테일"),         ("334890","이지스밸류리츠","오피스"),
    ("338100","NH프라임리츠","오피스"),     ("348950","제이알글로벌리츠","오피스(해외)"),
    ("357120","마스턴프리미어리츠","복합"), ("365550","ESR켄달스퀘어리츠","물류"),
    ("370920","이지스레지던스리츠","주거"), ("377190","디앤디플랫폼리츠","복합"),
    ("395400","SK리츠","복합"),             ("396690","미래에셋글로벌리츠","물류(해외)"),
    ("404990","신한서부티엔디리츠","복합"), ("417180","코람코라이프인프라","인프라"),
    ("432320","KB스타리츠","오피스(해외)"),("448730","삼성FN리츠","오피스"),
    ("451190","코람코더원리츠","오피스"),   ("451800","한화리츠","오피스"),
]

def _run_peer_snapshot():
    now = datetime.now(KST)
    print(f"\n🔄 피어 스냅샷 수집 ({now.strftime('%Y-%m-%d %H:%M KST')})")
    stocks, failed = [], []
    for code, name, sector in PEER_CODES:
        try:
            r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic",
                headers={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
                         "Referer":f"https://m.stock.naver.com/domestic/stock/{code}/home"},
                timeout=6)
            if r.ok:
                d = r.json()
                def nv(*k):
                    for key in k:
                        v=d.get(key)
                        if v is not None: return float(str(v).replace(",","").replace("%","") or 0)
                    return 0.0
                price=nv("closePrice","currentPrice")
                chgR =nv("fluctuationsRatio","changeRate")
                cap  =nv("marketValue","marketCap")
                if price>0:
                    stocks.append({"code":code,"name":name,"short":name,"sector":sector,
                                   "price":price,"chgR":chgR,"cap":cap,"source":"naver"})
                    print(f"   ✓ {name} {chgR:+.2f}%")
                    continue
        except Exception: pass
        failed.append(code)
        print(f"   ✗ {name} 실패")
    cache = {"updated_at":now.isoformat(),
             "updated_at_str":now.strftime("%Y-%m-%d %H:%M KST"),
             "stocks":stocks,"failed":failed,"count":len(stocks)}
    with open("peer_cache.json","w",encoding="utf-8") as f:
        json.dump(cache,f,ensure_ascii=False,indent=2)
    print(f"✅ {len(stocks)}/{len(PEER_CODES)}개 저장 → peer_cache.json\n")

# ══════════════════════════════════════════════════════════════════
#  FLASK ROUTES
# ══════════════════════════════════════════════════════════════════
@app.route("/")
def dashboard():
    resp = Response(DASHBOARD, mimetype="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

@app.route("/compare")
def compare():
    return Response(COMPARE, mimetype="text/html; charset=utf-8")

@app.route("/update-peers", methods=["GET","POST"])
def update_peers():
    threading.Thread(target=_run_peer_snapshot, daemon=True).start()
    return Response(
        json.dumps({"status":"started","message":"수집 시작. 약 30초 후 /peer-cache 에서 확인하세요."}, ensure_ascii=False),
        mimetype="application/json; charset=utf-8"
    )

@app.route("/peer-cache")
def peer_cache():
    if not os.path.exists("peer_cache.json"):
        return Response(json.dumps({"error":"no_cache","stocks":[]}), status=404, mimetype="application/json")
    with open("peer_cache.json", encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/json; charset=utf-8")

@app.route("/frgn")
def frgn():
    code = request.args.get("code","")
    # 캐시 확인
    now = datetime.now().timestamp()
    if code in _frgn_cache:
        data, ts = _frgn_cache[code]
        if now - ts < FRGN_TTL:
            return Response(json.dumps(data, ensure_ascii=False), mimetype="application/json; charset=utf-8")
    data = scrape_naver_frgn(code)
    _frgn_cache[code] = (data, now)
    return Response(json.dumps(data, ensure_ascii=False), mimetype="application/json; charset=utf-8")

_frgn_cache = {}
FRGN_TTL    = 300



@app.route("/investor")
def get_investor():
    from datetime import datetime as DT
    code   = request.args.get("code","")
    today  = DT.now().strftime("%Y%m%d")
    errors = []

    def ok(source, fg, inst, indiv):
        return Response(json.dumps({
            "source":source,"date":today,
            "foreign":{"net":fg,"label":"외국인"},
            "institution":{"net":inst,"label":"기관합계"},
            "individual":{"net":indiv,"label":"개인"},
        },ensure_ascii=False), mimetype="application/json")

    if KRX_API_KEY:
        try:
            r=requests.get("https://openapi.krx.co.kr/contents/OPApplication.do",
                params={"AUTH_KEY":KRX_API_KEY,"bld":"dbms/MDC/STAT/standard/MDCSTAT02202",
                        "strtDd":today,"endDd":today,"isuCd":code,"share":"1","money":"1"},
                headers={"User-Agent":"Mozilla/5.0"},timeout=10)
            if r.ok:
                rows=r.json().get("OutBlock_1",r.json().get("output",[]))
                inv={}
                for row in (rows if isinstance(rows,list) else []):
                    tp=str(row.get("INVST_TP_CD",""))
                    net=int(str(row.get("NETBUY_QTY",0)).replace(",","") or 0)
                    if tp=="1":inv["fg"]=net
                    if tp=="2":inv["inst"]=net
                    if tp=="3":inv["indiv"]=net
                if inv: return ok("KRX OpenAPI",inv.get("fg",0),inv.get("inst",0),inv.get("indiv",0))
        except Exception as e: errors.append(f"KRX:{e}")

    try:
        r=requests.get(f"https://m.stock.naver.com/api/stock/{code}/investor",
            headers={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                     "Referer":f"https://m.stock.naver.com/domestic/stock/{code}/investor"},timeout=6)
        if r.ok and r.text.strip().startswith("{"):
            raw=r.json()
            def pick(d,*keys):
                for k in keys:
                    if k in d: return int(str(d[k]).replace(",","") or 0)
                return None
            fg=pick(raw,"foreignNetBuyVolume","foreignerNetBuyVolume","frgnNetBuyVolume")
            inst=pick(raw,"institutionNetBuyVolume","orgNetBuyVolume","instNetBuyVolume")
            indiv=pick(raw,"individualNetBuyVolume","privNetBuyVolume","indivNetBuyVolume")
            if fg is not None: return ok("네이버 증권",fg or 0,inst or 0,indiv or 0)
    except Exception as e: errors.append(f"Naver:{e}")

    return Response(json.dumps({
        "source":None,"error":"unavailable","tried":errors,
        "setup":{"url":"https://openapi.krx.co.kr",
                 "config":"shinhan_proxy.py 상단 KRX_API_KEY 에 발급 키 입력"}
    },ensure_ascii=False),status=503,mimetype="application/json")




# ══════════════════════════════════════════════════════════════════
#  일일 보고서 HTML 생성 (장마감 후 정적 파일 다운로드)
# ══════════════════════════════════════════════════════════════════

def _s(v):
    """CSS inline style helper: dict → style string"""
    return ";".join(f"{k}:{v2}" for k,v2 in v.items())


# ══════════════════════════════════════════════════════════════════
#  DART API — 배당 정보 조회
# ══════════════════════════════════════════════════════════════════
DART_API_KEY = "777aff14b08a827d6029db99bcfe13846127b0b9"
_dart_corp_codes = {}  # 캐시

def _get_hist_price_naver(code, date_str):
    """Naver Finance 일별시세에서 특정 날짜 종가 조회"""
    import re as _re
    target = date_str.replace("-",".")  # "2024.09.30"
    try:
        for page in range(1, 60):
            r = requests.get(
                f"https://finance.naver.com/item/sise_day.naver?code={code}&page={page}",
                headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                         "Referer":"https://finance.naver.com/",
                         "Accept-Language":"ko-KR,ko;q=0.9"},
                timeout=8
            )
            r.encoding = "euc-kr"
            html = r.text
            if target not in html:
                # 이 페이지의 가장 오래된 날짜 확인
                dates = _re.findall(r'(\d{4}\.\d{2}\.\d{2})', html)
                if dates and min(dates) < target:
                    break  # 이미 지나침
                continue
            # 날짜 행에서 종가(두 번째 숫자) 추출
            # 행 구조: 날짜 | 종가 | 전일비 | 시가 | 고가 | 저가 | 거래량
            m = _re.search(
                target.replace(".",r"\.") + r'</td>.*?<td[^>]*>\s*<span[^>]*>([\d,]+)</span>',
                html, _re.DOTALL
            )
            if m:
                price = int(m.group(1).replace(",",""))
                print(f"[DART] {code} {date_str} 종가: {price:,}원")
                return price
    except Exception as e:
        print(f"[DART] 종가 조회 오류 {code} {date_str}: {e}")
    return None
    """신한알파리츠·서부리츠 DART 고유번호 (확인된 값 하드코딩)"""
    global _dart_corp_codes
    if not _dart_corp_codes:
        _dart_corp_codes = {
            "293940": {"corp_code": "01276594", "corp_name": "신한알파리츠"},
            "404990": {"corp_code": "01436558", "corp_name": "신한서부티엔디리츠"},
        }
    return _dart_corp_codes

def _get_dart_dividend(stock_code):
    """최근 배당 정보 조회 — 주당배당금·배당수익률 중심"""
    codes = _get_dart_corp_codes()
    if stock_code not in codes: return []
    corp_code = codes[stock_code]["corp_code"]

    # 표시할 항목
    SHOW_SE = {"주당 현금배당금(원)", "현금배당수익률(%)", "현금배당금총액(백만원)"}

    results = []
    seen = set()
    for year in ["2026","2025","2024","2023"]:
        for reprt_code in ["11011","11012"]:
            try:
                r = requests.get(
                    "https://opendart.fss.or.kr/api/alotMatter.json",
                    params={"crtfc_key": DART_API_KEY, "corp_code": corp_code,
                            "bsns_year": year, "reprt_code": reprt_code},
                    timeout=10
                )
                if not r.ok: continue
                d = r.json()
                if d.get("status") != "000": continue
                for item in d.get("list", []):
                    se  = item.get("se","")
                    knd = item.get("stock_knd","")
                    val = item.get("thstrm","")
                    dt  = item.get("stlm_dt","")
                    if se not in SHOW_SE: continue
                    if val in ("-","",None): continue
                    # 보통주만 (종류주 제외)
                    if knd and knd not in ("보통주","-"): continue
                    key = (se, dt)
                    if key in seen: continue
                    seen.add(key)
                    # 주당배당금이면 결산일 종가로 수익률 직접 계산
                    calc_yield = None
                    if "주당 현금배당금" in se and knd in ("보통주","-") and val not in ("-",""):
                        try:
                            close_px = _get_hist_price_naver(stock_code, dt)
                            if close_px and close_px > 0:
                                dps = float(val.replace(",",""))
                                calc_yield = round(dps / close_px * 2 * 100, 2)
                        except: pass
                    results.append({"se": se, "knd": knd, "val": val, "dt": dt,
                                    "year": year, "reprt_code": reprt_code,
                                    "calc_yield": calc_yield})
                if results: break
            except Exception as e:
                print(f"DART 배당 오류 {year}: {e}")
        if results: break
    return results


def _make_report_briefing(sd, fd, now):
    """보고서용 AI 브리핑 HTML 생성 (Python 버전)"""
    FOREIGN_KEYS = ["모간","골드만","씨티","UBS","메릴","도이치","CLSA","노무라","맥쿼리","BNP","바클레이"]
    BN = ["F07505","F07506","F07507","F07508","F07509"]
    BV = ["F07510","F07511","F07512","F07513","F07514"]
    SN = ["F07530","F07531","F07532","F07533","F07534"]
    SV = ["F07535","F07536","F07537","F07538","F07539"]
    MACRO = [
        ("2026-07-16","금통위","한국은행 금통위 → 기준금리 25bp 인상"),("2026-07-15","미국CPI","미국 CPI 발표"),
        ("2026-07-29","FOMC","FOMC 금리 결정"),("2026-08-12","미국CPI","미국 CPI 발표"),
        ("2026-08-28","금통위","한국은행 금통위"),("2026-09-16","FOMC","FOMC 금리 결정"),
    ]
    import datetime as _dt

    def nf(s): return float(str(s or "0").replace(",","")) if s else 0.0
    def fmtN(v): return f"{round(v):,}"
    def fmtA(v):
        v=float(v)
        if v>=1e12: return f"{v/1e12:.1f}조"
        if v>=1e8: return f"{v/1e8:.0f}억"
        return f"{round(v):,}"

    bullets, sections = [], []
    today = now.date()

    for code, name in [("293940","알파리츠"),("404990","서부리츠")]:
        raw = sd.get(code,{})
        r = raw.get("results",{})
        st = r.get("STOCK",{}); mem = r.get("MEMBER",{})
        hist = r.get("HIST",[]); intra = r.get("INTRA",[])
        if not st: continue

        price = nf(st.get("F15001")); prev = nf(st.get("F15007"))
        chg   = nf(st.get("F15472")); chgR = float(st.get("F15004",0))
        dirv  = st.get("F15006","3")
        highv = nf(st.get("F15010")); lowv = nf(st.get("F15011"))
        vol   = nf(st.get("F15015")); amt  = nf(st.get("F15023"))

        # 거래량 평균
        hvols = [nf(h.get("F15015")) for h in hist[:30]]
        avgvol = sum(hvols)/len(hvols) if hvols else 0
        vr = vol/avgvol*100 if avgvol > 0 else 0

        # VWAP
        lt = intra[0] if intra else {}
        ca = nf(lt.get("F20013")); cv = nf(lt.get("F20012"))
        vwap = ca/cv if cv > 0 else 0
        vd   = (price-vwap)/vwap*100 if vwap > 0 else 0

        # 장중 흐름
        chrono = list(reversed(intra))
        prices = [nf(t.get("F20008")) for t in chrono if nf(t.get("F20008")) > 0]
        flow = ""
        if len(prices) >= 5:
            half = len(prices)//2
            fa = sum(prices[:half])/half; sa = sum(prices[half:])/(len(prices)-half)
            cp = (price-lowv)/(highv-lowv or 1)
            if sa > fa*1.002 and cp > 0.7: flow = "오전 약세 후 오후 강세 전환"
            elif sa < fa*0.998 and cp < 0.3: flow = "오전 대비 오후 매도 압력 강화, 저가 마감"
            elif cp < 0.12: flow = "장 내내 하락 압력, 저가 마감"
            elif cp > 0.88: flow = "강세 유지, 고가 마감"
            else: flow = f"{fmtN(round(lowv))}~{fmtN(round(highv))}원 박스권"

        # 거래원
        buyers  = [(mem.get(k,""), nf(mem.get(BV[i]))) for i,k in enumerate(BN) if mem.get(k) and nf(mem.get(BV[i]))>0]
        sellers = [(mem.get(k,""), nf(mem.get(SV[i]))) for i,k in enumerate(SN) if mem.get(k) and nf(mem.get(SV[i]))>0]
        net = sum(v for _,v in buyers)-sum(v for _,v in sellers)

        # 외국인·기관 (frgn)
        fr = (fd.get(code,{}) or {}).get("rows",[])
        frgn_line = ""
        if fr:
            row0 = fr[0]
            inst_n = row0.get("inst_net",0); frgn_n = row0.get("frgn_net",0)
            ratio  = row0.get("frgn_ratio","")
            indiv  = -(inst_n+frgn_n)
            iclr   = "#ff5267" if inst_n>=0 else "#4fa8ff"
            fclr   = "#ff5267" if frgn_n>=0 else "#4fa8ff"
            inclr  = "#ff5267" if indiv>=0 else "#4fa8ff"
            frgn_line = (
                f'기관 <span style="color:{iclr};font-weight:600">{inst_n:+,}주</span> · '
                f'외국인 <span style="color:{fclr};font-weight:600">{frgn_n:+,}주</span> (보유율 {ratio}) · '
                f'개인(추정) <span style="color:{inclr};font-weight:600">{indiv:+,.0f}주</span>'
            )
        else:
            ftype = "외국계 창구 매도" if sellers and any(k in (sellers[0][0] or "") for k in FOREIGN_KEYS) and net<0 else \
                    "외국계 창구 매수" if buyers and any(k in (buyers[0][0] or "") for k in FOREIGN_KEYS) and net>0 else "창구 혼조"
            frgn_line = f'창구 합산 {("순매수" if net>=0 else "순매도")} {fmtN(round(abs(net)))}주 ({ftype})'

        sign = "+" if dirv=="2" else ""
        dirkor = "상승" if dirv=="2" else "하락" if dirv=="5" else "보합"
        clr_dir = "#ff5267" if dirv=="2" else "#4fa8ff"

        # 불릿
        vt = "저거래량" if vr<55 else "활발한 거래" if vr>130 else "평이한 거래"
        bullets.append(f'{name}: {vt}, {sign}{chgR:.2f}%')

        sec = f"""<div style="margin-top:14px;padding-top:14px;border-top:1px solid #1a2338">
  <div style="font-size:13px;font-weight:700;margin-bottom:6px">
    ● {name} (KOSPI {code})
    <span style="font-size:13px;font-family:monospace;color:{clr_dir};margin-left:8px">{fmtN(round(price))}원 {sign}{chgR:.2f}%</span>
  </div>
  <div style="display:flex;flex-direction:column;gap:5px">"""

        if flow:
            vwap_str = f" · VWAP {fmtN(round(vwap))}원 대비 {abs(vd):.1f}% {'상회' if vd>=0 else '하회'}" if vwap>0 else ""
            open_p = fmtN(round(prices[0] if prices else prev))
            sec += f'<div style="font-size:12px;line-height:1.7;padding:5px 10px;background:rgba(255,255,255,.02);border-left:2px solid #232e47;border-radius:4px"><b>장중 흐름</b> 시가 {open_p}원 출발 · {flow}{vwap_str}</div>'

        sec += f'<div style="font-size:12px;line-height:1.7;padding:5px 10px;background:rgba(255,255,255,.02);border-left:2px solid #232e47;border-radius:4px"><b>거래량</b> {fmtN(round(vol))}주 — 30일 평균 대비 {vr:.0f}%{"  · 매수세 부재" if vr<55 else " · 평균 수준" if vr<=120 else " · 활발"} · 거래대금 {fmtA(amt)}</div>'
        sec += f'<div style="font-size:12px;line-height:1.7;padding:5px 10px;background:rgba(255,255,255,.02);border-left:2px solid #232e47;border-radius:4px"><b>외국인·기관·개인</b> {frgn_line}</div>'
        sec += "</div></div>"
        sections.append(sec)

    # 매크로 전망
    outlook_rows = []
    for d,t,n in MACRO:
        diff = (_dt.date.fromisoformat(d)-today).days
        if 0 < diff <= 14:
            outlook_rows.append(f'<div style="font-size:12px;padding:4px 0;border-top:1px solid #1a2338"><b style="color:#9db4ff">{t} D-{diff}</b> {n} · {"HIGH 임팩트 — 대응 시나리오 사전 준비 권장" if t in ("FOMC","금통위") else "물가 지표 결과에 따라 금리 방향성 영향"}</div>')

    outlook_html = ""
    if outlook_rows:
        outlook_html = f"""<div style="margin-top:12px;padding-top:12px;border-top:1px solid #1a2338">
  <div style="font-size:12px;font-weight:700;color:#9db4ff;margin-bottom:6px">🔮 단기 전망 — 예정 매크로</div>
  {''.join(outlook_rows)}
</div>"""

    # ── 오늘/전일 매크로 이벤트 영향 분석 ────────────────────
    macro_impact_html = ""
    for d, t, n in MACRO:
        diff = (_dt.date.fromisoformat(d) - today).days
        if -1 <= diff <= 0 and t in ("금통위","FOMC"):
            when = "오늘" if diff==0 else "어제"
            if t == "금통위":
                if "인상" in n:
                    bp = "25"
                    macro_impact_html = f"""<div style="background:rgba(255,82,103,.06);border:1px solid rgba(255,82,103,.25);border-radius:10px;padding:12px 14px;margin-bottom:14px">
  <div style="font-size:13px;font-weight:700;color:#ff5267;margin-bottom:8px">🏦 한국은행 기준금리 {bp}bp 인상 ({when})</div>
  <div style="display:flex;flex-direction:column;gap:5px">
    <div style="font-size:12px;line-height:1.7;padding:6px 10px;background:rgba(255,255,255,.02);border-left:2px solid #232e47;border-radius:4px">
      <b>리츠 밸류에이션 영향</b> 무위험 금리 상승으로 리츠 요구수익률(Cap Rate) 상향 압력. 배당수익률 대비 국채금리 스프레드 축소로 상대적 매력도 단기 약화.
    </div>
    <div style="font-size:12px;line-height:1.7;padding:6px 10px;background:rgba(255,255,255,.02);border-left:2px solid #232e47;border-radius:4px">
      <b>자본비용 영향</b> 변동금리 차입금 이자비용 증가 → FFO 하방 압력. 신한알파리츠·서부리츠의 LTV 및 고정/변동 금리 비중 확인 권장.
    </div>
    <div style="font-size:12px;line-height:1.7;padding:6px 10px;background:rgba(255,255,255,.02);border-left:2px solid #232e47;border-radius:4px">
      <b>주가 영향 시나리오</b> 금리 인상 초기 리츠 주가 통상 약세. 임대료 상승(인플레이션 연동)으로 장기 실물가치 방어. 금리 고점 도달 시 매력 재부각 가능.
    </div>
  </div>
</div>"""
                elif "인하" in n:
                    macro_impact_html = f"""<div style="background:rgba(74,222,128,.06);border:1px solid rgba(74,222,128,.25);border-radius:10px;padding:12px 14px;margin-bottom:14px">
  <div style="font-size:13px;font-weight:700;color:#4ade80;margin-bottom:8px">🏦 한국은행 기준금리 인하 ({when})</div>
  <div style="font-size:12px;line-height:1.7;padding:6px 10px;background:rgba(255,255,255,.02);border-left:2px solid #232e47;border-radius:4px">
    <b>리츠 영향</b> 무위험 금리 하락 → 배당수익률 매력 강화. 차입 이자비용 경감 → FFO 개선 기대. 리츠 섹터 강한 반등 모멘텀 형성.
  </div>
</div>"""

    if not bullets:
        return ""

    return f"""<div style="background:#0f1826;border:1px solid #1e2d45;border-radius:12px;padding:16px 18px;margin-bottom:20px">
  <div style="font-size:13px;font-weight:700;margin-bottom:10px">🤖 거래 분석 브리핑</div>
  <div style="font-size:12px;font-weight:700;color:#9db4ff;margin-bottom:6px">📋 핵심 요약</div>
  <ul style="padding-left:16px;margin-bottom:10px">
    {''.join(f'<li style="font-size:12px;margin-bottom:3px">{b}</li>' for b in bullets)}
  </ul>
  {macro_impact_html}
  {''.join(sections)}
  {outlook_html}
  <div style="font-size:10px;color:#3b4564;margin-top:12px;padding-top:10px;border-top:1px solid #1a2338">
    Koscom API 실시간 데이터 기반 자동 생성 · 투자 권유 아님
  </div>
</div>"""


def _build_report_html(sd, fd, now):
    import json as _j, datetime as _dt

    wd  = ["월","화","수","목","금","토","일"][now.weekday()]
    dts = now.strftime("%Y. %m. %d.") + f" ({wd})"
    tms = now.strftime("%H:%M KST")

    def nf(s):
        return float(str(s or "0").replace(",","")) if s else 0.0
    def nc(v):
        v = float(v) if v else 0
        if v >= 1e12: return f"{v/1e12:.1f}조"
        if v >= 1e10: return f"{round(v/1e8)}억"
        if v >= 1e8:  return f"{v/1e8:.1f}억"
        return f"{round(v):,}"

    STOCKS = [("293940","신한알파리츠","오피스"), ("404990","신한서부티엔디리츠","리테일·호텔")]
    BN = ["F07505","F07506","F07507","F07508","F07509"]
    BV = ["F07510","F07511","F07512","F07513","F07514"]
    SN = ["F07530","F07531","F07532","F07533","F07534"]
    SV = ["F07535","F07536","F07537","F07538","F07539"]
    FK = ["모간","골드만","씨티","UBS","메릴","도이치","CLSA","노무라","맥쿼리","BNP"]
    def isf(nm): return nm and any(k in nm for k in FK)

    kospi  = sd.get("293940",{}).get("results",{}).get("INDEX",{}).get("KOSPI",{})
    kosdaq = sd.get("293940",{}).get("results",{}).get("INDEX",{}).get("KOSDAQ",{})

    cards_html = ""
    charts_js  = ""

    for code, name, stype in STOCKS:
        r    = sd.get(code,{}).get("results",{})
        st   = r.get("STOCK",{}); mem = r.get("MEMBER",{})
        hist = r.get("HIST",[]); intra = r.get("INTRA",[])

        price  = nf(st.get("F15001")); prev = nf(st.get("F15007"))
        chg    = nf(st.get("F15472")); chgR = float(st.get("F15004",0))
        dirv   = st.get("F15006","3")
        openv  = nf(st.get("F15009")); highv = nf(st.get("F15010")); lowv = nf(st.get("F15011"))
        vol    = nf(st.get("F15015")); amt   = nf(st.get("F15023"))
        hi52   = nf(st.get("F02133")); lo52  = nf(st.get("F02155"))
        p52    = round(max(0, min(100, (price-lo52)/(hi52-lo52 or 1)*100)), 1)

        lt = intra[0] if intra else {}
        ca = nf(lt.get("F20013")); cv = nf(lt.get("F20012"))
        vwap = ca/cv if cv > 0 else 0

        hvols = [nf(h.get("F15015")) for h in hist[:30]]
        avgvol = sum(hvols)/len(hvols) if hvols else 0
        vr = round(vol/avgvol*100, 1) if avgvol > 0 else 0

        buyers  = [(mem.get(k,""), nf(mem.get(BV[i]))) for i,k in enumerate(BN) if mem.get(k) and nf(mem.get(BV[i]))>0]
        sellers = [(mem.get(k,""), nf(mem.get(SV[i]))) for i,k in enumerate(SN) if mem.get(k) and nf(mem.get(SV[i]))>0]
        net = sum(v for _,v in buyers) - sum(v for _,v in sellers)

        frgnrows = (fd.get(code,{}) or {}).get("rows",[])[:20]

        up = dirv=="2"; dn = dirv=="5"
        clr  = "#ff5267" if up else "#4fa8ff" if dn else "#8993ad"
        upbg = "rgba(255,82,103,.12)" if up else "rgba(79,168,255,.12)"
        sign = "+" if up else ""; arr = "▲" if up else "▼" if dn else "–"

        # ── 거래원 rows ──
        def brow_html(items, color):
            if not items:
                return '<div style="font-size:11px;color:#5d6680">없음</div>'
            maxv = max(v for _,v in items) or 1
            out = ""
            for i,(nm,v) in enumerate(items[:5]):
                fw   = round(v/maxv*100)
                tag  = '<b style="font-size:9px;color:#fbbf24">[외]</b>' if isf(nm) else ""
                out += (
                    '<div style="display:flex;align-items:center;gap:6px;margin-bottom:5px">'
                    f'<span style="font-size:10px;color:#5d6680;width:13px">{i+1}</span>'
                    f'<span style="flex:1;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{nm} {tag}</span>'
                    f'<div style="width:30px;height:4px;background:#1a2338;border-radius:2px">'
                    f'<div style="height:100%;width:{fw}%;background:{color};border-radius:2px"></div></div>'
                    f'<span style="font-size:10px;width:50px;text-align:right;color:#cfd4e4;font-family:monospace">{round(v):,}</span>'
                    '</div>'
                )
            return out

        # ── frgn 표 ──
        if frgnrows:
            fh = ["날짜","종가","등락률","거래량","기관","외국인","보유율"]
            frgn_tbl = (
                '<table style="width:100%;border-collapse:collapse;font-size:11px">'
                '<thead><tr style="border-bottom:1px solid #232e47">'
                + "".join(f'<th style="text-align:left;padding:5px 6px 5px 0;color:#5d6680;font-weight:500">{h}</th>' for h in fh)
                + "</tr></thead><tbody>"
            )
            for row in frgnrows:
                rate   = row.get("chg_rate","")
                inst_n = row.get("inst_net",0); frgn_n = row.get("frgn_net",0)
                volv   = row.get("volume",0)
                rclr   = "#4fa8ff" if rate.startswith("-") else "#ff5267"
                iclr   = "#ff5267" if inst_n >= 0 else "#4fa8ff"
                fclr   = "#ff5267" if frgn_n >= 0 else "#4fa8ff"
                frgn_tbl += (
                    '<tr style="border-bottom:1px solid #131e30">'
                    f'<td style="padding:4px 6px 4px 0;color:#8993ad">{row.get("date","")[5:]}</td>'
                    f'<td style="padding:4px 6px 4px 0;font-family:monospace">{row.get("close",0):,}</td>'
                    f'<td style="padding:4px 6px 4px 0;color:{rclr};font-family:monospace">{rate}</td>'
                    f'<td style="padding:4px 6px 4px 0;color:#cfd4e4;font-family:monospace">{round(volv/1e4) if volv else 0}만주</td>'
                    f'<td style="padding:4px 6px 4px 0;font-weight:600;color:{iclr};font-family:monospace">{inst_n:+,}</td>'
                    f'<td style="padding:4px 6px 4px 0;font-weight:600;color:{fclr};font-family:monospace">{frgn_n:+,}</td>'
                    f'<td style="padding:4px 0;color:#8993ad;font-family:monospace">{row.get("frgn_ratio","")}</td>'
                    '</tr>'
                )
            frgn_tbl += "</tbody></table>"
        else:
            frgn_tbl = '<p style="font-size:11px;color:#5d6680">외국인·기관 데이터 없음</p>'

        # ── 차트 데이터 ──
        hchr = list(reversed(hist[-30:])) if hist else []
        hl = _j.dumps([h.get("F12507","").replace("26/","") for h in hchr])
        hc = _j.dumps([nf(h.get("F15001")) for h in hchr])
        hv = _j.dumps([nf(h.get("F15015")) for h in hchr])
        hd = _j.dumps([h.get("F15006","3") for h in hchr])
        fl = _j.dumps([row.get("date","")[5:] for row in reversed(frgnrows)])
        fi = _j.dumps([row.get("inst_net",0) for row in reversed(frgnrows)])
        ff = _j.dumps([row.get("frgn_net",0) for row in reversed(frgnrows)])

        charts_js += f"""(function(){{
  var c1=document.getElementById('cp-{code}');
  if(c1) new Chart(c1,{{data:{{labels:{hl},datasets:[
    {{type:'bar',data:{hv},yAxisID:'y1',
     backgroundColor:{hd}.map(d=>d==='2'?'rgba(255,82,103,0.45)':'rgba(79,168,255,0.45)'),
     borderWidth:0,borderRadius:2}},
    {{type:'line',label:'종가',data:{hc},yAxisID:'y',
     borderColor:'#3457ff',borderWidth:2,tension:0.3,pointRadius:0}}
  ]}},options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{display:false}}}},
    scales:{{x:{{ticks:{{color:'#5d6680',font:{{size:9}}}},grid:{{color:'rgba(50,60,80,0.4)'}}}},
      y:{{position:'left',ticks:{{color:'#8993ad',font:{{size:9}}}},grid:{{color:'rgba(50,60,80,0.4)'}}}},
      y1:{{position:'right',ticks:{{color:'#5d6680',font:{{size:9}},callback:function(v){{return v>=10000?Math.round(v/10000)+'만':v;}}}},
           grid:{{drawOnChartArea:false}}}}}}}}}});
  var c2=document.getElementById('cf-{code}');
  if(c2) new Chart(c2,{{type:'bar',data:{{labels:{fl},datasets:[
    {{label:'기관',data:{fi},backgroundColor:{fi}.map(v=>v>=0?'rgba(255,82,103,0.7)':'rgba(79,168,255,0.7)'),borderWidth:0,borderRadius:2}},
    {{label:'외국인',data:{ff},backgroundColor:{ff}.map(v=>v>=0?'rgba(255,153,68,0.7)':'rgba(136,170,255,0.7)'),borderWidth:0,borderRadius:2}}
  ]}},options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{labels:{{color:'#8993ad',font:{{size:10}}}}}}}},
    scales:{{x:{{ticks:{{color:'#5d6680',font:{{size:9}}}},grid:{{color:'rgba(50,60,80,0.4)'}}}},
      y:{{ticks:{{color:'#8993ad',font:{{size:9}}}},grid:{{color:'rgba(50,60,80,0.4)'}},
          title:{{display:true,text:'순매매(주)',color:'#5d6680',font:{{size:9}}}}}}}}}}}});
}})();
"""
        # ── 지표 셀 ──
        vwap_str = f"{round(vwap):,}원" if vwap > 0 else "–"
        stat_items = [("시가",f"{round(openv):,}"), ("고가",f"{round(highv):,}"),
                      ("저가",f"{round(lowv):,}"),  ("전일종가",f"{round(prev):,}"),
                      ("VWAP",vwap_str), ("거래량",f"평균 {vr:.0f}%"),
                      ("거래대금",nc(amt)), ("52주",f"{p52:.0f}%")]
        stat_cells = "".join(
            '<div style="background:#161f33;border-radius:8px;padding:8px 10px">'
            f'<div style="font-size:9px;color:#8993ad;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px">{l}</div>'
            f'<div style="font-size:13px;font-weight:600;font-family:monospace">{v}</div>'
            '</div>'
            for l,v in stat_items
        )

        net_clr = "#ff5267" if net >= 0 else "#4fa8ff"
        net_arr = "▲" if net >= 0 else "▼"

        cards_html += (
            f'<div style="background:#111827;border:1px solid #232e47;border-radius:14px;overflow:hidden">'
            f'<div style="height:3px;background:{clr}"></div>'
            f'<div style="padding:20px 22px;display:flex;flex-direction:column;gap:16px">'

            # 헤더
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div><div style="font-size:17px;font-weight:700;margin-bottom:3px">{name}</div>'
            f'<div style="font-size:12px;color:#8993ad">KOSPI {code} · {stype}</div></div>'
            f'<span style="background:rgba(52,87,255,.15);color:#9db4ff;border:1px solid #2a3e8f;'
            f'border-radius:5px;padding:2px 8px;font-size:10px;font-weight:600">당사 관리종목</span>'
            f'</div>'

            # 가격
            f'<div style="display:flex;align-items:baseline;gap:12px">'
            f'<span style="font-size:40px;font-weight:700;font-family:monospace">{round(price):,}</span>'
            f'<span style="font-size:13px;font-weight:700;border-radius:8px;padding:4px 10px;'
            f'background:{upbg};color:{clr};font-family:monospace">'
            f'{arr} {round(abs(chg)):,} ({sign}{chgR:.2f}%)</span>'
            f'</div>'

            # 지표
            f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px">{stat_cells}</div>'

            # 30일 차트
            f'<div><div style="font-size:9px;color:#5d6680;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">30일 가격 추이</div>'
            f'<div style="background:#161f33;border-radius:9px;padding:8px 6px 4px;height:130px">'
            f'<canvas id="cp-{code}"></canvas></div></div>'

            # 기관/외국인 바차트
            f'<div><div style="font-size:9px;color:#5d6680;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">기관·외국인 순매매 추이 (20일)</div>'
            f'<div style="background:#161f33;border-radius:9px;padding:8px 6px 4px;height:110px">'
            f'<canvas id="cf-{code}"></canvas></div>'
            f'<div style="display:flex;gap:12px;margin-top:4px;font-size:9px;color:#5d6680">'
            f'<span><span style="color:#ff5267">■</span> 기관순매수 &nbsp;<span style="color:#4fa8ff">■</span> 기관순매도</span>'
            f'<span><span style="color:#ff9944">■</span> 외국인순매수 &nbsp;<span style="color:#88aaff">■</span> 외국인순매도</span>'
            f'</div></div>'

            # frgn 표
            f'<div><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
            f'<div style="font-size:9px;color:#5d6680;text-transform:uppercase;letter-spacing:.05em">외국인·기관 순매매 (최근 20일)</div>'
            f'<span style="font-size:9px;color:#5d6680">네이버 증권</span></div>'
            f'<div style="background:#0c1322;border:1px solid #1e2d45;border-radius:10px;padding:10px 12px;overflow-x:auto">'
            f'{frgn_tbl}</div></div>'

            # 거래원
            f'<div><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<div style="font-size:9px;color:#5d6680;text-transform:uppercase;letter-spacing:.05em">거래원 (창구별 5위)</div>'
            f'<div style="font-size:11px;font-weight:700;font-family:monospace;color:{net_clr}">{net_arr} {round(abs(net)):,}주</div></div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
            f'<div><div style="font-size:10px;font-weight:700;color:#ff5267;margin-bottom:6px">▲ 매수 상위</div>{brow_html(buyers,"#ff5267")}</div>'
            f'<div><div style="font-size:10px;font-weight:700;color:#4fa8ff;margin-bottom:6px">▼ 매도 상위</div>{brow_html(sellers,"#4fa8ff")}</div>'
            f'</div></div>'

            f'</div></div>'
        )

    # ── KOSPI/KOSDAQ ──
    ki_p  = f"{nf(kospi.get('F15001')):,.0f}"  if kospi  else "–"
    ki_c  = f"{nf(kospi.get('F15472')):+,.0f} ({kospi.get('F15004','0')}%)" if kospi else ""
    ki_up = kospi.get("F15006","3")=="2" if kospi else True
    kd_p  = f"{nf(kosdaq.get('F15001')):,.0f}" if kosdaq else "–"
    kd_c  = f"{nf(kosdaq.get('F15472')):+,.0f} ({kosdaq.get('F15004','0')}%)" if kosdaq else ""
    kd_up = kosdaq.get("F15006","3")=="2" if kosdaq else True

    # ── 매크로 이벤트 ──
    MACRO = [
        ("2026-07-16","B","한국은행 금통위 → 기준금리 25bp 인상","HIGH"),  # 7/16 확정
        ("2026-07-15","C","미국 CPI","MED"),
        ("2026-07-29","F","FOMC 금리 결정","HIGH"),
        ("2026-08-12","C","미국 CPI","MED"),
        ("2026-08-28","B","한국은행 금통위","HIGH"),
        ("2026-09-16","F","FOMC 금리 결정","HIGH"),
    ]
    TLBL = {"F":"FOMC","B":"금통위","C":"미국CPI"}
    today = now.date()
    macro_rows = ""
    for d,t,n_,imp in MACRO:
        diff = (_dt.date.fromisoformat(d) - today).days
        if 0 < diff <= 14:
            col = "#ff5267" if imp=="HIGH" else "#ffaa00"
            hi  = f'<span style="font-size:10px;font-weight:700;color:#ff5267">HIGH</span>' if imp=="HIGH" else ""
            macro_rows += (
                f'<div style="display:flex;align-items:center;gap:10px;padding:6px 0;'
                f'border-bottom:1px solid #1a2338;font-size:12px">'
                f'<span style="font-size:10px;padding:1px 7px;border-radius:4px;font-weight:700;'
                f'background:{col}22;color:{col}">{TLBL.get(t,t)}</span>'
                f'<span>{n_}</span>'
                f'<span style="margin-left:auto;color:#5d6680">D-{diff}</span>{hi}</div>'
            )

    macro_sec = ""
    if macro_rows:
        macro_sec = (
            '<div style="margin-bottom:20px;background:#0f1826;border:1px solid #1e2d45;'
            'border-radius:12px;padding:14px 16px">'
            '<div style="font-size:10px;color:#9db4ff;font-weight:700;text-transform:uppercase;'
            'letter-spacing:.08em;margin-bottom:8px">📅 예정 매크로 이벤트 (D-14 이내)</div>'
            + macro_rows + '</div>'
        )

    ki_color = "#ff5267" if ki_up else "#4fa8ff"
    kd_color = "#ff5267" if kd_up else "#4fa8ff"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>신한리츠 일일 보고서 {now.strftime('%Y.%m.%d')}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0e1a;color:#eceff6;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;font-size:14px}}
</style>
</head>
<body>
<div style="max-width:1280px;margin:0 auto;padding:0 20px 48px">

  <div style="border-bottom:1px solid #232e47;padding:16px 0 14px;margin-bottom:16px">
    <div style="font-size:10px;color:#3457ff;letter-spacing:.12em;text-transform:uppercase;margin-bottom:3px">SHINHAN REIT 운용 · 상장리츠전략부</div>
    <div style="display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px">
      <div>
        <h1 style="font-size:20px;font-weight:700">일일 거래 모니터링 보고서</h1>
        <div style="font-size:12px;color:#8993ad;margin-top:2px">알파리츠 (293940) · 서부리츠 (404990)</div>
      </div>
      <div style="text-align:right">
        <div style="font-weight:600">{dts}</div>
        <div style="font-size:11px;color:#8993ad">장마감 기준 · {tms} 생성</div>
      </div>
    </div>
  </div>

  <div style="display:flex;gap:24px;margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid #1a2338">
    <div style="display:flex;align-items:center;gap:10px">
      <span style="font-size:11px;color:#8993ad;width:52px">KOSPI</span>
      <span style="font-size:17px;font-weight:700;font-family:monospace">{ki_p}</span>
      <span style="font-size:12px;font-weight:600;font-family:monospace;color:{ki_color}">{ki_c}</span>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <span style="font-size:11px;color:#8993ad;width:52px">KOSDAQ</span>
      <span style="font-size:17px;font-weight:700;font-family:monospace">{kd_p}</span>
      <span style="font-size:12px;font-weight:600;font-family:monospace;color:{kd_color}">{kd_c}</span>
    </div>
  </div>

  {macro_sec}

  <!-- AI 브리핑 -->
  {_make_report_briefing(sd, fd, now)}

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:20px;margin-bottom:20px">
    {cards_html}
  </div>

  <div style="font-size:10px;color:#3b4564;border-top:1px solid #1a2338;padding-top:14px;line-height:1.7">
    본 보고서는 Koscom API(실시간 주가·거래원·VWAP) 및 네이버 증권(외국인·기관 순매매) 데이터를 기반으로 자동 생성됩니다.
    투자 권유나 자문에 해당하지 않으며, 투자 판단의 최종 책임은 이용자 본인에게 있습니다.
    생성: {now.strftime('%Y-%m-%d %H:%M KST')}
  </div>
</div>

<script>
{charts_js}
</script>
</body>
</html>"""



def _build_investor_report_html(sd, fd, div_data, now):
    """외부 투자자용 보고서 HTML"""
    import datetime as _dt
    wd  = ["월","화","수","목","금","토","일"][now.weekday()]
    dts = now.strftime("%Y. %m. %d.") + f" ({wd})"

    def nf(s): return float(str(s or "0").replace(",","")) if s else 0.0
    def nc(v):
        v=float(v) if v else 0
        if v>=1e12: return f"{v/1e12:.1f}조"
        if v>=1e8:  return f"{round(v/1e8):,}억"
        return f"{round(v):,}"

    STOCKS = [("293940","신한알파리츠","오피스"), ("404990","신한서부티엔디리츠","리테일·호텔")]

    cards = ""
    for code, name, stype in STOCKS:
        r   = sd.get(code,{}).get("results",{})
        st  = r.get("STOCK",{}); hist = r.get("HIST",[])
        intra = r.get("INTRA",[])

        price  = nf(st.get("F15001")); prev  = nf(st.get("F15007"))
        chg    = nf(st.get("F15472")); chgR  = float(st.get("F15004",0))
        dirv   = st.get("F15006","3")
        hi52   = nf(st.get("F02133")); lo52  = nf(st.get("F02155"))
        vol    = nf(st.get("F15015")); amt   = nf(st.get("F15023"))
        mktcap = nf(st.get("F15028"))
        p52    = round(max(0,min(100,(price-lo52)/(hi52-lo52 or 1)*100)),1)

        up  = dirv=="2"; dn = dirv=="5"
        clr = "#e05252" if up else "#4a7fd4" if dn else "#888"
        sign= "+" if up else ""
        arr = "▲" if up else "▼" if dn else "–"

        # 30일 차트 데이터
        hchr = list(reversed(hist[-30:])) if hist else []
        hl = [h.get("F12507","").replace("26/","") for h in hchr]
        hc = [nf(h.get("F15001")) for h in hchr]
        hd = [h.get("F15006","3") for h in hchr]
        import json as _j
        hl_js = _j.dumps(hl); hc_js = _j.dumps(hc); hd_js = _j.dumps(hd)

        # frgn
        frgnrows = (fd.get(code,{}) or {}).get("rows",[])[:10]
        frgn_html = ""
        if frgnrows:
            frgn_html = '<table style="width:100%;border-collapse:collapse;font-size:11px;margin-top:8px"><thead><tr style="border-bottom:1px solid #ddd">'
            for h in ["날짜","종가","등락","거래량","기관","외국인","보유율"]:
                frgn_html += f'<th style="text-align:left;padding:4px 6px 4px 0;color:#888;font-weight:500">{h}</th>'
            frgn_html += "</tr></thead><tbody>"
            for row in frgnrows:
                rate=row.get("chg_rate","")
                rclr="#e05252" if not rate.startswith("-") else "#4a7fd4"
                inst_n=row.get("inst_net",0); frgn_n=row.get("frgn_net",0)
                iclr="#e05252" if inst_n>=0 else "#4a7fd4"
                fclr="#e05252" if frgn_n>=0 else "#4a7fd4"
                frgn_html += (f'<tr style="border-bottom:1px solid #f0f0f0">'
                    f'<td style="padding:4px 6px 4px 0;color:#666">{row.get("date","")[5:]}</td>'
                    f'<td style="padding:4px 6px 4px 0;font-family:monospace">{row.get("close",0):,}</td>'
                    f'<td style="padding:4px 6px 4px 0;color:{rclr};font-family:monospace">{rate}</td>'
                    f'<td style="padding:4px 6px 4px 0;font-family:monospace">{round(row.get("volume",0)/1e4) if row.get("volume") else 0}만주</td>'
                    f'<td style="padding:4px 6px 4px 0;font-weight:600;color:{iclr};font-family:monospace">{inst_n:+,}</td>'
                    f'<td style="padding:4px 6px 4px 0;font-weight:600;color:{fclr};font-family:monospace">{frgn_n:+,}</td>'
                    f'<td style="padding:4px 0;color:#888;font-family:monospace">{row.get("frgn_ratio","")}</td></tr>')
            frgn_html += "</tbody></table>"

        # 배당 정보
        div = div_data.get(code,[])
        div_html = ""
        if div:
            div_html = '<div style="margin-top:16px"><div style="font-size:11px;font-weight:700;color:#444;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em">배당 정보 (DART 공시 기준)</div>'
            dps_rows   = [d for d in div if "주당 현금배당금" in d.get("se","") and d.get("knd") in ("보통주","-")]
            yield_rows = [d for d in div if "현금배당수익률" in d.get("se","") and d.get("knd") in ("보통주","-")]

            # 주당 현금배당금
            if dps_rows:
                div_html += '<div style="font-size:11px;font-weight:600;color:#666;margin:8px 0 4px">주당 현금배당금 (보통주, 원)</div>'
                for d in dps_rows[:3]:
                    div_html += (f'<div style="display:flex;justify-content:space-between;padding:4px 0;border-top:1px solid #f0f0f0;font-size:12px">'
                        f'<span style="color:#888">{d.get("dt","")}</span>'
                        f'<span style="font-weight:700;color:#1a1a1a">{d.get("val","")}원</span></div>')

            # 현금배당수익률 (연환산 = DPS/결산일종가×2)
            if yield_rows:
                div_html += '<div style="font-size:11px;font-weight:600;color:#666;margin:8px 0 4px">현금배당수익률 (연환산, 결산일 종가 기준)</div>'
                for d in dps_rows[:3]:  # DPS 행에서 calc_yield 사용
                    cy = d.get("calc_yield")
                    if cy is None: continue
                    div_html += (f'<div style="display:flex;justify-content:space-between;padding:4px 0;border-top:1px solid #f0f0f0;font-size:12px">'
                        f'<span style="color:#888">{d.get("dt","")}</span>'
                        f'<span style="font-weight:700;color:#3457cc">{cy:.2f}%</span></div>')
                div_html += '<div style="font-size:10px;color:#aaa;margin-top:4px">※ 해당 결산기말 종가 기준으로 산출된 배당수익률이며, 연환산 기준입니다. (DPS ÷ 결산일 종가 × 2)</div>'

            div_html += "</div>"
        else:
            div_html = '<div style="margin-top:16px;font-size:11px;color:#aaa">배당 공시 데이터 없음</div>'

        # ETF 보유 현황 (하드코딩, 2026-06-30 기준)
        ETF_HOLD = {
            "293940": [("TIGER",5.81),("KODEX",6.52),("ACE",5.58),("WON",2.86),("PLUS",10.99)],
            "404990": [("TIGER",2.19),("KODEX",1.44),("ACE",3.50),("WON",3.03),("PLUS",2.57)],
        }
        etf_bars = ""
        for etf_name, w in ETF_HOLD.get(code,[]):
            bar_w = round(min(100, w*5))
            etf_bars += (f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12px">'
                f'<span style="width:50px;color:#666;font-size:11px">{etf_name}</span>'
                f'<div style="flex:1;height:8px;background:#f0f0f0;border-radius:4px">'
                f'<div style="height:100%;width:{bar_w}%;background:#3457cc;border-radius:4px"></div></div>'
                f'<span style="width:40px;text-align:right;font-weight:600;color:#3457cc">{w:.2f}%</span></div>')

        cards += f"""
<div style="background:#fff;border:1px solid #e8e8e8;border-radius:12px;overflow:hidden;margin-bottom:24px">
  <div style="height:4px;background:{clr}"></div>
  <div style="padding:22px 24px">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px">
      <div>
        <div style="font-size:18px;font-weight:700;color:#1a1a1a">{name}</div>
        <div style="font-size:12px;color:#888;margin-top:2px">KOSPI {code} · {stype}</div>
      </div>
    </div>

    <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:16px">
      <span style="font-size:36px;font-weight:700;font-family:monospace;color:#1a1a1a">{round(price):,}</span>
      <span style="font-size:13px;font-weight:700;padding:3px 10px;border-radius:6px;
        background:{"rgba(224,82,82,.08)" if up else "rgba(74,127,212,.08)"};color:{clr};font-family:monospace">
        {arr} {round(abs(chg)):,} ({sign}{chgR:.2f}%)</span>
    </div>

    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px">
      {''.join(f'<div style="background:#f8f9fa;border-radius:8px;padding:10px"><div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px">{l}</div><div style="font-size:13px;font-weight:600;font-family:monospace">{v}</div></div>' for l,v in [("시가총액",nc(mktcap)),("52주 고가",f"{round(hi52):,}"),("52주 저가",f"{round(lo52):,}"),("52주위치",f"{p52:.0f}%")])}
    </div>

    <div style="margin-bottom:16px">
      <div style="font-size:11px;font-weight:700;color:#444;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em">30일 주가 추이</div>
      <div style="height:120px;background:#f8f9fa;border-radius:8px;padding:8px">
        <canvas id="ch-{code}" style="width:100%;height:100%"></canvas>
      </div>
    </div>

    <div style="margin-bottom:16px">
      <div style="font-size:11px;font-weight:700;color:#444;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em">외국인·기관 순매매 (최근 10일)</div>
      {frgn_html if frgn_html else '<div style="font-size:11px;color:#aaa">데이터 없음</div>'}
    </div>

    <div style="margin-bottom:16px">
      <div style="font-size:11px;font-weight:700;color:#444;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em">리츠 ETF 편입 비중 (2026-06-30)</div>
      {etf_bars}
    </div>

    {div_html}
  </div>
</div>

<script>
(function(){{
  var c=document.getElementById('ch-{code}');
  if(c&&window.Chart){{
    var hl={hl_js},hc={hc_js},hd={hd_js};
    new Chart(c,{{type:'line',data:{{labels:hl,datasets:[{{data:hc,
      borderColor:'#3457cc',borderWidth:2,tension:0.3,pointRadius:0,
      fill:true,backgroundColor:'rgba(52,87,204,0.06)'}}]}},
      options:{{responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{display:false}}}},
        scales:{{x:{{ticks:{{color:'#999',font:{{size:9}}}},grid:{{color:'rgba(0,0,0,.04)'}}}},
          y:{{ticks:{{color:'#999',font:{{size:9}}}},grid:{{color:'rgba(0,0,0,.04)'}}}}}}}}
    }});
  }}
}})();
</script>
"""

    # 매크로 환경
    MACRO_PY = [
        ("2026-07-16","금통위","한국은행 기준금리 25bp 인상"),
        ("2026-07-29","FOMC","FOMC 금리 결정"),
        ("2026-08-12","CPI","미국 CPI 발표"),
        ("2026-08-28","금통위","한국은행 금통위"),
    ]
    today = now.date()
    macro_items = ""
    for d, t, n in MACRO_PY:
        diff = (_dt.date.fromisoformat(d)-today).days
        if -7 <= diff <= 30:
            label = "완료" if diff<0 else f"D-{diff}"
            clr2 = "#e05252" if diff<0 else "#3457cc"
            macro_items += (f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:7px 0;border-top:1px solid #f0f0f0;font-size:12px">'
                f'<span><b style="color:#444">{t}</b> <span style="color:#666">{n}</span></span>'
                f'<span style="font-weight:700;color:{clr2}">{label}</span></div>')

    macro_html = f"""
<div style="background:#fff;border:1px solid #e8e8e8;border-radius:12px;padding:22px 24px;margin-bottom:24px">
  <div style="font-size:14px;font-weight:700;color:#1a1a1a;margin-bottom:12px">매크로 환경</div>
  <div style="font-size:12px;color:#555;line-height:1.7;margin-bottom:12px;padding:12px;background:#f8f9fa;border-radius:8px">
    한국은행은 2026년 7월 16일 기준금리를 25bp 인상하였습니다. 금리 인상은 리츠 섹터의 자본비용 증가 및 요구수익률 상향 요인으로 작용할 수 있으나, 우량 자산 기반 배당 안정성은 유지될 것으로 판단됩니다.
  </div>
  {macro_items}
</div>""" if macro_items else ""

    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>신한리츠 투자자 보고서 {now.strftime('%Y.%m.%d')}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#f5f6f8;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;font-size:14px;color:#1a1a1a}}
</style></head><body>
<div style="max-width:900px;margin:0 auto;padding:32px 20px">

  <div style="border-bottom:2px solid #1a1a1a;padding-bottom:16px;margin-bottom:24px">
    <div style="font-size:11px;color:#888;letter-spacing:.1em;text-transform:uppercase;margin-bottom:4px">Shinhan REIT Management</div>
    <h1 style="font-size:24px;font-weight:700">상장리츠 투자 현황 보고서</h1>
    <div style="font-size:13px;color:#666;margin-top:4px">신한알파리츠 (293940) · 신한서부티엔디리츠 (404990) · {dts}</div>
  </div>

  {cards}
  {macro_html}

  <div style="font-size:10px;color:#aaa;border-top:1px solid #e8e8e8;padding-top:14px;line-height:1.8">
    본 보고서는 투자 권유를 목적으로 하지 않으며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.
    수록된 정보는 Koscom API, 네이버 증권, DART 전자공시 기반으로 자동 생성되었으며 실제 값과 차이가 있을 수 있습니다.
    생성: {now.strftime('%Y-%m-%d %H:%M KST')}
  </div>
</div>
</body></html>"""




@app.route("/send-report", methods=["POST"])
def send_report_now():
    """수동 이메일 발송 버튼"""
    threading.Thread(target=_auto_send_reports, daemon=True).start()
    return Response('{"status":"sending","msg":"발송 중... 약 20초 후 이메일 확인"}',
                    mimetype="application/json")

@app.route("/dart-test")
def dart_test():
    """DART API 연결 및 배당 데이터 테스트"""
    import zipfile, io, re as _re
    results = {"step": [], "corp_codes": {}, "dividend": {}}

    # 1. XML 다운로드 시도
    try:
        results["step"].append("XML 다운로드 시작")
        r = requests.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": DART_API_KEY},
            timeout=60
        )
        results["step"].append(f"응답: {r.status_code} / {len(r.content)}bytes")

        if r.ok and len(r.content) > 1000:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            xml = z.read(z.namelist()[0]).decode("utf-8")
            results["step"].append(f"XML 파싱: {len(xml)}자")
            results["xml_sample"] = xml[:300]

            # 293940, 404990 검색
            for sc in ["293940","404990"]:
                idx = xml.find(sc)
                if idx >= 0:
                    results["step"].append(f"{sc} 발견 at {idx}")
                    results[f"around_{sc}"] = xml[max(0,idx-150):idx+150]
                else:
                    results["step"].append(f"{sc} 없음")
        else:
            results["error"] = r.text[:200]
    except Exception as e:
        results["step"].append(f"오류: {str(e)}")

    # 2. corp_codes 재조회
    _dart_corp_codes.clear()
    codes = _get_dart_corp_codes()
    results["corp_codes"] = codes

    # 3. 배당 조회
    if codes:
        for sc, info in codes.items():
            try:
                r2 = requests.get(
                    "https://opendart.fss.or.kr/api/alotMatter.json",
                    params={"crtfc_key": DART_API_KEY,
                            "corp_code": info["corp_code"],
                            "bsns_year": "2024", "reprt_code": "11011"},
                    timeout=10
                )
                d = r2.json() if r2.ok else {"error": r2.status_code}
                results["dividend"][sc] = d
            except Exception as e:
                results["dividend"][sc] = {"error": str(e)}

    return Response(json.dumps(results, ensure_ascii=False, indent=2),
                    mimetype="application/json; charset=utf-8")

@app.route("/investor-report")
def investor_report():
    """외부 투자자용 HTML 보고서 생성"""
    import traceback
    now = datetime.now(KST)
    try:
        sd = {}
        for code in ["293940","404990"]:
            try:
                r = requests.get(f"{KOSCOM_BASE}/getStockInfo",
                    params={"code":code,"auth_key":AUTH,"gubun":"K"}, timeout=10)
                if r.ok: sd[code] = r.json()
            except: pass

        fd = {}
        for code in ["293940","404990"]:
            fd[code] = scrape_naver_frgn(code)

        div_data = {}
        for code in ["293940","404990"]:
            div_data[code] = _get_dart_dividend(code)

        html = _build_investor_report_html(sd, fd, div_data, now)
        from urllib.parse import quote
        resp = Response(html, mimetype="text/html; charset=utf-8")
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="shinhan_reit_investor.html"; '
            f'filename*=UTF-8\'\'{quote("신한리츠_투자자보고서.html")}'
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        err = traceback.format_exc()
        print(err)
        return Response(f"<pre>오류:\n{err}</pre>", status=500, mimetype="text/html; charset=utf-8")


@app.route("/export-report")
def export_report():
    """장마감 후 정적 HTML 보고서 다운로드 (Vercel 등에 배포 가능)"""
    now = datetime.now(KST)
    sd = {}
    for code in ["293940","404990"]:
        try:
            r = requests.get(f"{KOSCOM_BASE}/getStockInfo",
                params={"code":code,"auth_key":AUTH,"gubun":"K"}, timeout=10)
            if r.ok: sd[code] = r.json()
        except Exception: pass
    fd = {}
    for code in ["293940","404990"]:
        fd[code] = scrape_naver_frgn(code)
    html = _build_report_html(sd, fd, now)
    from urllib.parse import quote
    fname_ascii = "shinhan_reit_report.html"
    fname_utf8  = "신한리츠_일일보고서.html"
    resp = Response(html, mimetype="text/html; charset=utf-8")
    resp.headers["Content-Disposition"] = (
        f"attachment; filename=\"{fname_ascii}\"; "
        f"filename*=UTF-8''{quote(fname_utf8)}"
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ══════════════════════════════════════════════════════════════════
#  ETF 편입 모니터링 페이지
# ══════════════════════════════════════════════════════════════════

# ── ETF 정적 데이터 (업로드 파일 기준 26.1Q) ──────────────────────
ETF_META = {
    "TIGER": {
        "name": "TIGER 리츠 부동산 인프라",
        "code": "329200",
        "index": "FnGuide 리츠부동산인프라",
        "aum": 1_488_688_396_234,
        "rebalance_months": [6, 12],
        "selection_months": [5, 11],
        "selection_rule": "5·11월 마지막 영업일 종가 기준",
        "exec_rule": "선물옵션 만기일 D+2 ~ D+6 (5영업일)",
        "mktcap_include": 200_000_000_000,   # 2,000억 (신규편입)
        "mktcap_buffer":  150_000_000_000,   # 1,500억 (기존 편입 유지 Buffer)
        "vol_days": 20,
        "vol_threshold": 100_000_000,
        "alpha": {"shares": 13_892_912, "weight": 0.0526,
                  "hist": [("25.1Q",0.0703),("25.2Q",0.0604),("25.3Q",0.0579),("25.4Q",0.0532),("26.1Q",0.0526)]},
        "seobu": {"shares": 6_615_984, "weight": 0.0195,
                  "hist": [("25.1Q",0.0194),("25.2Q",0.0174),("25.3Q",0.0163),("25.4Q",0.0163),("26.1Q",0.0195)]},
    },
    "KODEX": {
        "name": "KODEX 한국부동산리츠인프라",
        "code": "432320",
        "index": "KRX 부동산리츠인프라",
        "aum": 567_748_099_500,
        "rebalance_months": [6, 12],
        "selection_months": [6, 12],
        "selection_rule": "6·12월 선물 최종거래일 기준 (편입일: 정기변경일, 편출일: T+2)",
        "exec_rule": "KOSPI200 선물 6·12월 최종거래일 다음 매매거래일",
        "mktcap_include": 200_000_000_000,
        "mktcap_buffer":  200_000_000_000,   # Buffer Rule 없음
        "vol_days": 20,
        "vol_threshold": 100_000_000,
        "alpha": {"shares": 5_850_674, "weight": 0.0593,
                  "hist": [("25.1Q",0.0695),("25.2Q",0.0783),("25.3Q",0.0598),("25.4Q",0.0584),("26.1Q",0.0593)]},
        "seobu": {"shares": 0, "weight": 0.0,
                  "hist": [("25.1Q",0.0129),("25.2Q",0.0),("25.3Q",0.0),("25.4Q",0.0),("26.1Q",0.0)],
                  "note": "26.6월 정기변경 신규편입 완료 (KRX REITs 지수 편입)"},
    },
    "ACE": {
        "name": "ACE 리츠부동산인프라액티브",
        "code": "396500",
        "index": "FnGuide 맥쿼리인프라 배당 리츠(PR)",
        "aum": 17_223_718_972,
        "rebalance_months": [3, 9],
        "selection_months": [3, 9],
        "selection_rule": "선물옵션만기일 기준",
        "exec_rule": "선물옵션만기일 기준 +2영업일",
        "mktcap_include": 100_000_000_000,   # 1,000억
        "mktcap_buffer":  100_000_000_000,
        "vol_days": 20,
        "vol_threshold": 100_000_000,
        "alpha": {"shares": 200_475, "weight": 0.0660,
                  "hist": [("26.1Q",0.0660)]},
        "seobu": {"shares": 53_724, "weight": 0.0135,
                  "hist": [("26.1Q",0.0135)]},
    },
    "WON": {
        "name": "WON 한국부동산TOP3플러스",
        "code": "396510",
        "index": "DeepSearch 한국부동산 TOP3 플러스",
        "aum": 10_308_716_895,
        "rebalance_months": [4, 10],
        "selection_months": [4, 10],
        "selection_rule": "옵션 만기일 기준",
        "exec_rule": "옵션만기일 익주 첫 영업일~5영업일",
        "mktcap_include": 200_000_000_000,
        "mktcap_buffer":  200_000_000_000,
        "vol_days": 20,
        "vol_threshold": 100_000_000,
        "alpha": {"shares": 174_120, "weight": 0.0956,
                  "hist": [("25.1Q",0.1263),("25.2Q",0.1956),("25.3Q",0.1941),("25.4Q",0.0958),("26.1Q",0.0956)]},
        "seobu": {"shares": 0, "weight": 0.0,
                  "hist": [("25.1Q",0.0522),("25.2Q",0.0295),("25.3Q",0.0),("25.4Q",0.0),("26.1Q",0.0)]},
    },
    "PLUS": {
        "name": "PLUS K리츠",
        "code": "466920",
        "index": "FnGuide 리츠",
        "aum": 9_163_235_761,
        "rebalance_months": [6, 12],
        "selection_months": [5, 11],
        "selection_rule": "5·11월 마지막 영업일 종가 기준",
        "exec_rule": "선물옵션만기일(D) 익주 첫 영업일(D+2)~5영업일",
        "mktcap_include": 150_000_000_000,   # 1,500억
        "mktcap_buffer":  150_000_000_000,
        "vol_days": 60,
        "vol_threshold": 100_000_000,
        "alpha": {"shares": 161_070, "weight": 0.0992,
                  "hist": [("25.1Q",0.1126),("25.2Q",0.1103),("25.3Q",0.1015),("25.4Q",0.0979),("26.1Q",0.0992)]},
        "seobu": {"shares": 47_346, "weight": 0.0223,
                  "hist": [("25.1Q",0.0186),("25.2Q",0.0193),("25.3Q",0.0183),("25.4Q",0.0182),("26.1Q",0.0223)]},
    },
}

# ── 다음 정기변경일 계산 ────────────────────────────────────────────
def _next_rebalance(months, from_date):
    """다음 정기변경 달의 첫날 반환 (세부 날짜는 선물만기일에 따라 다름)"""
    import datetime as _dt
    y, m = from_date.year, from_date.month
    for target_m in sorted(months):
        if target_m > m:
            return _dt.date(y, target_m, 1)
        if target_m == m:
            return _dt.date(y, target_m, 1)
    return _dt.date(y + 1, sorted(months)[0], 1)

def _next_selection(sel_months, from_date):
    """다음 종목선정 기준일 (해당 달 마지막 영업일 근사)"""
    import datetime as _dt
    import calendar
    y, m = from_date.year, from_date.month
    for sm in sorted(sel_months):
        if sm >= m:
            last_day = calendar.monthrange(y, sm)[1]
            return _dt.date(y, sm, last_day)
    sm = sorted(sel_months)[0]
    last_day = calendar.monthrange(y + 1, sm)[1]
    return _dt.date(y + 1, sm, last_day)



# ── ETF Naver 종목코드 설정 (비중 자동 업데이트용) ────────────────────────
# 확인: https://finance.naver.com/etf/ 에서 검색 후 URL의 code= 값 입력
ETF_NAVER_CODES = {
    "TIGER": "329200",   # TIGER 리츠부동산인프라
    "KODEX": "476800",   # KODEX 한국부동산리츠인프라
    "ACE":   "0153P0",   # ACE 리츠부동산인프라액티브
    "WON":   "480460",   # WON 한국부동산TOP3플러스
    "PLUS":  "429740",   # PLUS K리츠
}

ETF_HOLDINGS_CACHE = "etf_holdings_cache.json"

def _fetch_etf_holdings(etf_key, etf_code):
    """Naver에서 ETF 구성종목 중 293940·404990 비중/수량 조회"""
    result = {"etf_key": etf_key, "etf_code": etf_code, "alpha": None, "seobu": None}
    targets = {"293940": "alpha", "404990": "seobu"}

    # ① Naver 모바일 API 여러 endpoint 시도
    endpoints = [
        f"https://m.stock.naver.com/api/stock/{etf_code}/etfPortfolioDetail",
        f"https://m.stock.naver.com/api/stock/{etf_code}/etfPortfolio",
        f"https://m.stock.naver.com/api/etf/{etf_code}/portfolio",
    ]
    for url in endpoints:
        try:
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
                "Accept": "application/json",
                "Referer": f"https://m.stock.naver.com/domestic/stock/{etf_code}/home",
            }, timeout=7)
            if not (r.ok and r.text.strip().startswith(('{',' ['))): continue
            raw = r.json()
            # 응답이 list인 경우와 dict인 경우 모두 처리
            items = raw if isinstance(raw, list) else raw.get("portfolioList", raw.get("items", raw.get("holdings", [])))
            if not isinstance(items, list): continue
            for item in items:
                code = str(item.get("itemCode", item.get("stockCode", item.get("code", ""))))
                if code not in targets: continue
                key = targets[code]
                # 다양한 필드명 처리
                def nv(*keys):
                    for k in keys:
                        v = item.get(k)
                        if v is not None:
                            try: return float(str(v).replace(",","").replace("%",""))
                            except: pass
                    return None
                shares = nv("quantity","shares","amount","holdingQuantity")
                weight = nv("weight","ratio","percent","holdingRatio","pct")
                if weight and weight > 1: weight = weight / 100
                result[key] = {
                    "shares": int(shares) if shares else 0,
                    "weight": round(weight, 4) if weight else 0,
                }
            if result["alpha"] or result["seobu"]: return result
        except Exception: pass

    # ② Naver 증권 PC 페이지 스크래핑
    try:
        r = requests.get(
            f"https://finance.naver.com/item/coinfo.nhn?code={etf_code}",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Referer": "https://finance.naver.com/",
            }, timeout=8)
        r.encoding = "euc-kr"
        html = r.text
        for code, key in targets.items():
            if code not in html: continue
            idx = html.find(code)
            nearby = html[max(0, idx-300):idx+600]
            # 비중 추출
            import re
            w_m = re.findall(r'([\d]+\.\d+)\s*%', nearby)
            s_m = re.findall(r'([\d,]{4,})', nearby)
            if w_m:
                w = float(w_m[0])
                s = int(s_m[0].replace(",","")) if s_m else 0
                result[key] = {"shares": s, "weight": round(w/100, 4)}
        if result["alpha"] or result["seobu"]: return result
    except Exception: pass

    return result

def _run_etf_holdings_snapshot():
    """전체 ETF 비중 스냅샷 수집 → etf_holdings_cache.json 저장"""
    now = datetime.now(KST)
    print(f"\n📡 ETF 구성종목 비중 수집 ({now.strftime('%H:%M KST')})")
    cache = {"updated_at": now.isoformat(), "updated_at_str": now.strftime("%Y-%m-%d %H:%M KST"), "etfs": {}}
    for key, code in ETF_NAVER_CODES.items():
        r = _fetch_etf_holdings(key, code)
        cache["etfs"][key] = r
        alpha_str = f"알파 {r['alpha']['shares']:,}주({r['alpha']['weight']*100:.2f}%)" if r["alpha"] else "알파 없음"
        seobu_str = f"서부 {r['seobu']['shares']:,}주({r['seobu']['weight']*100:.2f}%)" if r["seobu"] else "서부 없음/미편입"
        print(f"   {key}: {alpha_str} | {seobu_str}")
    with open(ETF_HOLDINGS_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"✅ ETF 비중 캐시 저장 완료 → {ETF_HOLDINGS_CACHE}\n")
    return cache

@app.route("/update-etf", methods=["GET","POST"])
def update_etf():
    threading.Thread(target=_run_etf_holdings_snapshot, daemon=True).start()
    return Response(json.dumps({"status":"started","msg":"ETF 비중 수집 시작. 약 20초 후 /etf-cache 에서 확인"}),
                    mimetype="application/json")

@app.route("/upload-etf", methods=["POST"])
def upload_etf():
    """ETF 자산구성내역 엑셀 파일 업로드 → 293940·404990 자동 추출"""
    import tempfile, openpyxl
    etf_key  = request.form.get("etf_key","")   # TIGER / KODEX / ACE / WON / PLUS
    file     = request.files.get("file")
    if not file or not etf_key:
        return Response('{"error":"etf_key와 파일 모두 필요"}', status=400, mimetype="application/json")
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            file.save(tmp.name)
            wb = openpyxl.load_workbook(tmp.name, data_only=True)
            ws = wb.active
            rows = [[c.value for c in row] for row in ws.iter_rows()]

        result = {"etf_key": etf_key, "alpha": None, "seobu": None, "rows_found": len(rows)}
        for row in rows:
            strs = [str(c).strip() if c is not None else "" for c in row]
            for i, s in enumerate(strs):
                if s in ("293940","404990"):
                    code = s
                    key  = "alpha" if code == "293940" else "seobu"
                    # 수량, 평가금액, 비중 파싱
                    nums = [c for c in row if isinstance(c,(int,float))]
                    shares = int(nums[0]) if nums else 0
                    weight = nums[-1]/100 if nums and nums[-1]>1 else (nums[-1] if nums else 0)
                    result[key] = {"shares": shares, "weight": round(weight,4)}
                    break

        # ETF 캐시에 저장
        import os
        cache = {}
        if os.path.exists(ETF_HOLDINGS_CACHE):
            with open(ETF_HOLDINGS_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        if "etfs" not in cache: cache["etfs"] = {}
        cache["etfs"][etf_key] = result
        cache["updated_at"] = datetime.now(KST).isoformat()
        cache["updated_at_str"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        with open(ETF_HOLDINGS_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        print(f"📂 ETF 파일 업로드: {etf_key} → 알파 {result['alpha']} / 서부 {result['seobu']}")
        return Response(json.dumps(result, ensure_ascii=False), mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), status=500, mimetype="application/json")

@app.route("/etf-cache")
def get_etf_cache():
    if not os.path.exists(ETF_HOLDINGS_CACHE):
        return Response('{"error":"no_cache"}', status=404, mimetype="application/json")
    with open(ETF_HOLDINGS_CACHE, encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/json; charset=utf-8")

@app.route("/etf-monitor")
def etf_monitor():
    import json as _j
    ETF_JS = _j.dumps({
"TIGER": {
"name": "TIGER 리츠 부동산 인프라",
"index": "FnGuide 리츠부동산인프라",
"aum": 1488688396234,
"rebalance_months": [
6,
12
],
"selection_months": [
5,
11
],
"exec_rule": "선물옵션 만기일 D+2~D+6 (5영업일)",
"mktcap_include": 200000000000,
"mktcap_buffer": 150000000000,
"vol_days": 20,
"vol_threshold": 100000000,
"alpha": {
"shares": 2238,
"weight": 0.0581
},
"seobu": {
"shares": 1133,
"weight": 0.0219
}
},
"KODEX": {
"name": "KODEX 한국부동산리츠인프라",
"index": "KRX 부동산리츠인프라",
"aum": 567748099500,
"rebalance_months": [
6,
12
],
"selection_months": [
6,
12
],
"exec_rule": "KOSPI200 선물 6·12월 최종거래일 익일",
"mktcap_include": 200000000000,
"mktcap_buffer": 200000000000,
"vol_days": 20,
"vol_threshold": 100000000,
"alpha": {
"shares": 2588,
"weight": 0.0652
},
"seobu": {
"shares": 763,
"weight": 0.0144,
"note": "26.6월 정기변경 신규편입 완료"
}
},
"ACE": {
"name": "ACE 리츠부동산인프라액티브",
"index": "FnGuide 맥쿼리인프라 배당 리츠(PR)",
"aum": 17223718972,
"rebalance_months": [
3,
9
],
"selection_months": [
3,
9
],
"exec_rule": "선물옵션만기일 기준 +2영업일",
"mktcap_include": 100000000000,
"mktcap_buffer": 100000000000,
"vol_days": 20,
"vol_threshold": 100000000,
"alpha": {
"shares": 4951,
"weight": 0.0558
},
"seobu": {
"shares": 4148,
"weight": 0.035
}
},
"WON": {
"name": "WON 한국부동산TOP3플러스",
"index": "DeepSearch 한국부동산 TOP3 플러스",
"aum": 460956667,
"rebalance_months": [
4,
10
],
"selection_months": [
4,
10
],
"exec_rule": "익주 월요일~5영업일",
"mktcap_include": 200000000000,
"mktcap_buffer": 200000000000,
"vol_days": 20,
"vol_threshold": 100000000,
"alpha": {
"shares": 2518,
"weight": 0.0286
},
"seobu": {
"shares": 3571,
"weight": 0.0303
}
},
"PLUS": {
"name": "PLUS K리츠",
"index": "FnGuide 리츠",
"aum": 9163235761,
"rebalance_months": [
6,
12
],
"selection_months": [
5,
11
],
"exec_rule": "선물옵션만기일(D) 익주 첫 영업일~5영업일",
"mktcap_include": 150000000000,
"mktcap_buffer": 150000000000,
"vol_days": 60,
"vol_threshold": 100000000,
"alpha": {
"shares": 6354,
"weight": 0.1099
},
"seobu": {
"shares": 1986,
"weight": 0.0257
}
}
}, ensure_ascii=False)
    AUTH_KEY = AUTH
    html = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>ETF 편입 모니터링</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0e1a;color:#eceff6;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;font-size:14px}
.num{font-family:'Consolas','D2Coding',monospace}
a{text-decoration:none;color:inherit}
.hdr{background:linear-gradient(135deg,#0d1430,#0a0e1a);border-bottom:1px solid #232e47;padding:14px 22px}
.eyebrow{font-size:10px;color:#3457ff;letter-spacing:.12em;text-transform:uppercase;margin-bottom:3px}
.hdr-row{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px}
.btn{background:rgba(52,87,255,.15);border:1px solid #3457ff;border-radius:6px;color:#9db4ff;font-size:11px;padding:3px 10px;cursor:pointer;text-decoration:none}
.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;margin-left:5px;vertical-align:middle;background:#4ade80}
.section{margin:16px 20px 0}
.sec-title{font-size:13px;font-weight:700;margin-bottom:10px}
.dday-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.dcard{background:#111827;border:1px solid #232e47;border-radius:10px;padding:12px 14px}
.dcard-etf{font-size:12px;font-weight:700;margin-bottom:2px}
.dcard-idx{font-size:10px;color:#5d6680;margin-bottom:8px}
.dday-item{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-top:1px solid #131e30;font-size:11px}
.dday-num{font-size:18px;font-weight:700;font-family:monospace}
.dday-urgent{color:#ff5267}.dday-soon{color:#facc15}.dday-ok{color:#4ade80}
.tgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.tcard{background:#111827;border:1px solid #232e47;border-radius:10px;padding:14px 16px}
.trow{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:12px}
.trow-label{width:140px;color:#8993ad;flex-shrink:0;font-size:11px}
.trow-bar-wrap{flex:1;height:6px;background:#1a2338;border-radius:3px}
.trow-bar{height:100%;border-radius:3px}
.trow-val{width:70px;text-align:right;font-family:monospace;font-size:11px;flex-shrink:0}
.hgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.hcard{background:#111827;border:1px solid #232e47;border-radius:10px;padding:12px 14px}
.hrow{display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-top:1px solid #131e30;font-size:12px}
.hrow-label{color:#8993ad}
.igrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.icard{background:#111827;border:1px solid #232e47;border-radius:10px;padding:14px 16px}
.irow{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-top:1px solid #131e30;font-size:12px}
.irow-label{color:#8993ad}
.irow-val{font-family:monospace;font-weight:600}
.tbox{background:#0f1826;border:1px solid #1e2d45;border-radius:10px;padding:12px 16px;margin-bottom:10px}
.loading{padding:30px;text-align:center;color:#8993ad}
.note{background:rgba(250,204,21,.08);border:1px solid rgba(250,204,21,.25);border-radius:6px;font-size:10px;color:#facc15;padding:1px 6px}
.inote{font-size:10px;color:#5d6680;margin-top:8px;padding-top:8px;border-top:1px solid #1a2338;line-height:1.6}
</style></head><body>
<div class="hdr">
  <div class="eyebrow">SHINHAN REIT 운용 · 상장리츠전략부</div>
  <div class="hdr-row">
    <div><h1 style="font-size:19px;font-weight:700">ETF 편입 모니터링</h1>
      <div style="font-size:12px;color:#8993ad;margin-top:2px">리츠 ETF 정기변경 선제 대응 시스템</div></div>
    <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#8993ad">
      <span class="num" id="clock" style="font-size:12px;color:#eceff6"></span>
      <span id="upd">연결 중<span class="pulse" id="pulse"></span></span>
      <button class="btn" onclick="refresh()">↺ 갱신</button>
      <button id="btn-etf-upd" class="btn" onclick="updateEtfHoldings()"
        style="background:rgba(167,139,250,.12);border-color:rgba(167,139,250,.4);color:#a78bfa">
        📡 ETF 비중 업데이트
      </button>
      <a href="/" class="btn" style="color:#8993ad">← 대시보드</a>
    </div>
  </div>
</div>
<div style="background:#0f1826;border:1px solid #1e2d45;border-radius:10px;padding:10px 16px;margin:8px 20px 0;font-size:11px;color:#5d6680;line-height:1.7">
  ⚠️ <b style="color:#8993ad">이용 안내</b> &nbsp;—&nbsp;
  ETF 편입 현황은 <b>수동 입력 데이터(2026-06-30 기준)</b>이며 실시간이 아닙니다.
  임계치 모니터·D-day·예상 매매 영향은 AI가 규칙 기반으로 자동 산출한 <b>참고용 추정치</b>로, 실제 ETF 정기변경 결과와 다를 수 있습니다.
  <b>투자 판단의 근거로 사용하지 마십시오.</b>
</div>
<div id="alert-banner"></div>
<div class="section"><div class="sec-title">🚨 편입·편출 임계치 현황 <span style="font-size:10px;font-weight:400;color:#5d6680">— 실시간 시가총액·거래대금</span></div>
  <div class="tgrid" id="thresh-grid"><div class="loading">API 데이터 로딩 중…</div></div></div>
<div class="section" style="margin-top:14px"><div class="sec-title">📅 정기변경 D-day 캘린더</div>
  <div class="dday-grid" id="dday-grid"></div></div>
<div class="section" style="margin-top:14px"><div class="sec-title">📊 ETF별 보유 현황 <span style="font-size:10px;font-weight:400;color:#5d6680">(2026-06-30 기준)</span> <span id="etf-cache-time" style="font-size:10px;font-weight:400;color:#5d6680"></span></div>
  <div class="tbox" id="total-box"></div>
  <div class="hgrid" id="hold-grid"></div></div>
<div class="section" style="margin-top:14px;margin-bottom:20px">
  <div class="sec-title">📂 ETF 자산구성 파일 업로드 <span style="font-size:10px;font-weight:400;color:#5d6680">— 운용사 엑셀 파일로 비중 직접 업데이트</span></div>
  <div style="background:#111827;border:1px solid #232e47;border-radius:10px;padding:14px 16px">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;margin-bottom:10px">
      <div>
        <div style="font-size:10px;color:#8993ad;margin-bottom:4px">ETF 선택</div>
        <select id="upload-etf-key" style="width:100%;background:#0a0e1a;border:1px solid #232e47;border-radius:5px;color:#eceff6;padding:5px 8px;font-size:12px">
          <option value="TIGER">TIGER 리츠부동산인프라</option>
          <option value="KODEX">KODEX 한국부동산리츠인프라</option>
          <option value="ACE">ACE 리츠부동산인프라액티브</option>
          <option value="WON">WON 한국부동산TOP3플러스</option>
          <option value="PLUS">PLUS K리츠</option>
        </select>
      </div>
      <div>
        <div style="font-size:10px;color:#8993ad;margin-bottom:4px">자산구성내역 엑셀 (.xlsx)</div>
        <input type="file" id="upload-file" accept=".xlsx,.xls"
          style="width:100%;background:#0a0e1a;border:1px solid #232e47;border-radius:5px;color:#eceff6;padding:4px 8px;font-size:11px"/>
      </div>
      <div style="display:flex;align-items:flex-end">
        <button onclick="uploadEtfFile()"
          style="background:#3457ff;border:none;border-radius:6px;color:#fff;font-size:12px;padding:6px 14px;cursor:pointer;font-weight:600;width:100%">
          📂 업로드 & 적용
        </button>
      </div>
    </div>
    <div id="upload-result" style="font-size:11px;color:#5d6680"></div>
    <div style="font-size:10px;color:#3b4564;margin-top:6px">
      운용사 홈페이지에서 다운로드한 "자산구성내역" 엑셀 파일을 올리면 293940·404990 비중을 자동 추출합니다.
    </div>
  </div>
</div>
  <div style="background:#111827;border:1px solid #232e47;border-radius:10px;padding:14px 16px;margin-bottom:12px">
    <div style="font-size:11px;font-weight:600;margin-bottom:10px;color:#9db4ff">📝 예상 비중 입력 (다음 정기변경 기준 %)</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px" id="w-inputs"></div>
    <button onclick="calcImpact()" style="margin-top:10px;background:#3457ff;border:none;border-radius:6px;color:#fff;font-size:12px;padding:6px 14px;cursor:pointer;font-weight:600">🔢 영향 계산</button>
  </div>
  <div class="igrid" id="impact-grid"><div class="loading">데이터 로드 후 계산 가능</div></div>
</div>
<script>
const E=""" + ETF_JS + """;
const AUTH='""" + AUTH_KEY + """';
const TODAY=new Date();
let LD={};

function fmt(v){return Math.round(v).toLocaleString('ko-KR');}
function fmtA(v){v=parseFloat(v)||0;if(v>=1e12)return(v/1e12).toFixed(2)+'조';if(v>=1e8)return(v/1e8).toFixed(0)+'억';return fmt(v);}
function n(s){return parseFloat((s||'0').toString().replace(/,/g,''))||0;}

// ── 정기변경 날짜 계산 ────────────────────────────────────────
// 한국 선물옵션 만기일 = 해당 월 두 번째 목요일
function secondThursday(y,m){
  let cnt=0;
  for(let d=1;d<=31;d++){
    const dt=new Date(y,m-1,d);
    if(dt.getMonth()!==m-1) break;
    if(dt.getDay()===4){cnt++;if(cnt===2)return dt;}
  }
  return null;
}
// n 영업일 후
function addBizDays(dt,n){
  let d=new Date(dt);
  while(n>0){d.setDate(d.getDate()+1);if(d.getDay()!==0&&d.getDay()!==6)n--;}
  return d;
}
// 익주 월요일 (만기일 다음 주 첫 영업일 근사)
function nextMonday(dt){
  let d=new Date(dt);d.setDate(d.getDate()+1);
  while(d.getDay()!==1)d.setDate(d.getDate()+1);
  return d;
}
function fmtD(dt){
  return `${dt.getFullYear()}.${String(dt.getMonth()+1).padStart(2,'0')}.${String(dt.getDate()).padStart(2,'0')}`;
}
function diffDays(dt){
  return Math.ceil((dt-new Date(TODAY.getFullYear(),TODAY.getMonth(),TODAY.getDate()))/(864e5));
}

function calcRebDates(etfKey,rebMonths){
  const cur=TODAY.getMonth()+1,y=TODAY.getFullYear();
  let ry=y,rm=null;
  for(const m of [...rebMonths].sort((a,b)=>a-b)){if(m>cur){rm=m;break;}}
  if(!rm){ry=y+1;rm=Math.min(...rebMonths);}
  const exp=secondThursday(ry,rm);
  if(!exp) return null;
  let start,end,label,rule;
  switch(etfKey){
    case 'TIGER':
      // D+2 ~ D+6 (5영업일)
      start=addBizDays(exp,2);end=addBizDays(exp,6);
      label=`${fmtD(start)} ~ ${fmtD(end).slice(5)}`;rule="D+2~D+6 (5영업일)";break;
    case 'KODEX':
      // D+1 (KOSPI200 선물 최종거래일 다음 매매거래일)
      start=addBizDays(exp,1);end=start;
      label=fmtD(start);rule="최종거래일 다음 매매거래일(D+1)";break;
    case 'ACE':
      // D+2 영업일
      start=addBizDays(exp,2);end=start;
      label=fmtD(start);rule="만기일 +2영업일";break;
    case 'WON':
      // 익주 첫 영업일부터 5영업일
      start=nextMonday(exp);end=addBizDays(start,4);
      label=`${fmtD(start)} ~ ${fmtD(end).slice(5)}`;rule="익주 월요일~5영업일";break;
    case 'PLUS':
      // 익주 첫 영업일(D+2)부터 5영업일
      start=nextMonday(exp);end=addBizDays(start,4);
      label=`${fmtD(start)} ~ ${fmtD(end).slice(5)}`;rule="익주 첫 영업일(D+2)~5영업일";break;
    default:
      start=addBizDays(exp,2);end=start;
      label=fmtD(start);rule="만기일 기준";
  }
  return{exp,start,end,label,rule,d:diffDays(start),dEnd:diffDays(end)};
}

// nextSel: calcImpact용 — 기존 유지 (선정 기준일 D-day 표시)
function nextSel(selM,rebM){
  const cur=TODAY.getMonth()+1,y=TODAY.getFullYear();
  for(const m of [...selM].sort((a,b)=>a-b)){
    if(m>=cur){
      const same=rebM&&rebM.some(r=>r===m);
      const d=same?10:new Date(y,m,0).getDate();
      return{y,m,d};
    }
  }
  const m=[...selM].sort((a,b)=>a-b)[0];
  const same=rebM&&rebM.some(r=>r===m);
  const d=same?10:new Date(y+1,m,0).getDate();
  return{y:y+1,m,d};
}

function renderDday(){
  document.getElementById('dday-grid').innerHTML=Object.entries(E).map(([k,etf])=>{
    const reb=calcRebDates(k,etf.rebalance_months);
    if(!reb) return '';
    const d=reb.d;
    const clr=d<=14?'#ff5267':d<=30?'#facc15':'#4ade80';
    return `<div class="dcard">
      <div class="dcard-etf">${etf.name.split(' ').slice(0,2).join(' ')}</div>
      <div class="dcard-idx">${etf.index}</div>
      <div class="dday-item" style="padding-top:8px">
        <span style="color:#8993ad">🔄 정기변경 실행</span>
        <span class="dday-num" style="color:${clr}">D-${d}</span>
      </div>
      <div style="text-align:right;font-size:11px;color:#5d6680;margin-bottom:4px">${reb.label}</div>
      <div style="font-size:9px;color:#5d6680;padding-top:6px;border-top:1px solid #131e30;line-height:1.5">
        만기일: ${fmtD(reb.exp)}<br>${reb.rule}
      </div>
    </div>`;
  }).join('');
}


function renderHoldings(){
  let tA=0,tS=0,tAUM=0;
  for(const etf of Object.values(E)){tA+=etf.alpha?.shares||0;tS+=etf.seobu?.shares||0;tAUM+=etf.aum;}
  const ad=LD.alpha,sd=LD.seobu;
  document.getElementById('total-box').innerHTML=`
    <div style="font-size:11px;font-weight:700;color:#9db4ff;margin-bottom:8px">5개 ETF 합산 보유</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <div><div style="font-size:11px;color:#8993ad;margin-bottom:3px">알파리츠 (293940)</div>
        <div class="num" style="font-size:16px;font-weight:700">${fmt(tA)}주</div>
        <div style="font-size:11px;color:#8993ad">${ad?fmtA(tA*ad.price)+'원':''} ${ad&&ad.avgVol20?'· '+Math.round(tA/ad.avgVol20*10)/10+'일치 거래량':''}</div></div>
      <div><div style="font-size:11px;color:#8993ad;margin-bottom:3px">서부리츠 (404990)</div>
        <div class="num" style="font-size:16px;font-weight:700">${fmt(tS)}주</div>
        <div style="font-size:11px;color:#8993ad">${sd?fmtA(tS*sd.price)+'원':''} ${sd&&sd.avgVol20?'· '+Math.round(tS/sd.avgVol20*10)/10+'일치 거래량':''}</div></div>
    </div>
    <div style="font-size:10px;color:#5d6680;margin-top:6px">총 AUM ${fmtA(tAUM)}</div>`;

  document.getElementById('hold-grid').innerHTML=Object.entries(E).map(([k,etf])=>{
    const ha=etf.alpha||{},hs=etf.seobu||{};
    const histA=(ha.hist||[]).slice(-5),histS=(hs.hist||[]).slice(-5);
    const mx=Math.max(...histA.map(h=>h[1]),...histS.map(h=>h[1]),0.01);
    const noteTag=hs.note?`<span class="note" title="${hs.note}">📌 ${hs.note.includes('완료')?'편입완료':'편입'}</span>`:'';
    return `<div class="hcard">
      <div style="font-size:12px;font-weight:700;margin-bottom:4px">${etf.name}</div>
      <div style="font-size:10px;color:#5d6680;margin-bottom:10px">AUM ${fmtA(etf.aum)} · ${etf.index}</div>
      <div class="hrow"><span class="hrow-label">알파리츠</span>
        <span class="num" style="font-weight:600">${fmt(ha.shares||0)}주
          <span style="color:#ff5267">(${((ha.weight||0)*100).toFixed(2)}%)</span></span></div>
      <div style="height:5px;background:#1a2338;border-radius:3px;margin:4px 0 8px">
        <div style="height:100%;width:${Math.min(100,(ha.weight||0)*5*100).toFixed(0)}%;background:#ff5267;border-radius:3px"></div>
      </div>
      <div class="hrow">${noteTag}<span class="hrow-label" style="flex:1">서부리츠</span>
        <span class="num" style="font-weight:600">${fmt(hs.shares||0)}주
          <span style="color:#4fa8ff">(${((hs.weight||0)*100).toFixed(2)}%)</span></span></div>
      <div style="height:5px;background:#1a2338;border-radius:3px;margin:4px 0 0">
        <div style="height:100%;width:${Math.min(100,(hs.weight||0)*5*100).toFixed(0)}%;background:#4fa8ff;border-radius:3px"></div>
      </div>
    </div>`;
  }).join('');
}

function renderWeightInputs(){
  document.getElementById('w-inputs').innerHTML=Object.entries(E).map(([k,etf])=>`
    <div style="background:#161f33;border-radius:8px;padding:8px 10px">
      <div style="font-size:11px;font-weight:600;margin-bottom:6px;color:#cfd4e4">${etf.name.split(' ').slice(0,2).join(' ')}</div>
      <div style="font-size:10px;color:#8993ad;margin-bottom:2px">알파리츠 (%)</div>
      <input id="ia-${k}" type="number" step="0.01" value="${((etf.alpha?.weight||0)*100).toFixed(2)}"
        style="width:100%;background:#0a0e1a;border:1px solid #232e47;border-radius:4px;color:#eceff6;padding:3px 7px;font-size:12px;font-family:monospace;margin-bottom:5px"/>
      <div style="font-size:10px;color:#8993ad;margin-bottom:2px">서부티엔디 (%)</div>
      <input id="is-${k}" type="number" step="0.01" value="${((etf.seobu?.weight||0)*100).toFixed(2)}"
        style="width:100%;background:#0a0e1a;border:1px solid #232e47;border-radius:4px;color:#eceff6;padding:3px 7px;font-size:12px;font-family:monospace"/>
    </div>`).join('');
}

function renderThresholds(){
  const el=document.getElementById('thresh-grid');
  if(!LD.alpha){el.innerHTML='<div class="loading" style="color:#5d6680">시가총액·거래대금 로딩 중…</div>';return;}
  el.innerHTML=Object.entries(E).map(([k,etf])=>{
    const stocks=[['알파리츠',LD.alpha],['서부리츠',LD.seobu]];
    return `<div class="tcard">
      <div style="font-size:12px;font-weight:700;margin-bottom:2px">${etf.name}</div>
      <div style="font-size:10px;color:#5d6680;margin-bottom:10px">${etf.index}</div>
      ${stocks.map(([nm,sd])=>{
        if(!sd) return '';
        const mc=sd.mktcap,ti=etf.mktcap_include,tb=etf.mktcap_buffer;
        const vd=etf.vol_days||20,av=vd<=20?sd.avg20:sd.avg60,vt=etf.vol_threshold;
        const mcP=Math.min(100,mc/ti*100);
        const vP=Math.min(100,av/vt*100);
        const mcC=mc>=ti*1.25?'#4ade80':mc>=ti?'#facc15':mc>=tb?'#fb923c':'#ef4444';
        const vC=av>=vt*1.25?'#4ade80':av>=vt?'#facc15':av>0?'#fb923c':'#5d6680';
        return `<div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid #131e30">
          <div style="font-size:11px;font-weight:700;margin-bottom:6px;color:#cfd4e4">${nm}</div>
          <div class="trow"><span class="trow-label">시가총액</span>
            <div class="trow-bar-wrap"><div class="trow-bar" style="width:${mcP.toFixed(0)}%;background:${mcC}"></div></div>
            <span class="trow-val num" style="color:${mcC}">${fmtA(mc)}</span></div>
          <div style="font-size:9px;color:#5d6680;margin:-4px 0 6px 148px">기준 ${fmtA(ti)}${tb<ti?' | Buffer '+fmtA(tb):''}</div>
          <div class="trow"><span class="trow-label">${vd}일 평균 거래대금</span>
            <div class="trow-bar-wrap"><div class="trow-bar" style="width:${vP.toFixed(0)}%;background:${vC}"></div></div>
            <span class="trow-val num" style="color:${vC}">${fmtA(av)}</span></div>
          <div style="font-size:9px;color:#5d6680;margin:-4px 0 0 148px">기준 ${fmtA(vt)}/일</div>
        </div>`;
      }).join('')}
    </div>`;
  }).join('');
}

function renderAlerts(){
  const alerts=[];
  for(const [k,etf] of Object.entries(E)){
    const reb=calcRebDates(k,etf.rebalance_months);
    if(reb&&reb.d>0&&reb.d<=30) alerts.push({c:'#facc15',msg:`📅 ${etf.name} 정기변경 실행 D-${reb.d} (${reb.label})`});
    for(const [nm,sd] of [['알파리츠',LD.alpha],['서부리츠',LD.seobu]]){
      if(!sd||!sd.mktcap) continue;
      if(sd.mktcap<etf.mktcap_buffer) alerts.push({c:'#ef4444',msg:`⚠️ ${nm} — ${etf.name} 편출 위험 (시총 ${fmtA(sd.mktcap)} < Buffer ${fmtA(etf.mktcap_buffer)})`});
      else if(sd.mktcap<etf.mktcap_include) alerts.push({c:'#fb923c',msg:`🔶 ${nm} — ${etf.name} 편입 기준 미달 (Buffer Rule 유지 중)`});
      const av=etf.vol_days<=20?sd.avg20:sd.avg60;
      if(av>0&&av<etf.vol_threshold) alerts.push({c:'#fb923c',msg:`🔶 ${nm} — ${etf.name} ${etf.vol_days}일 평균 거래대금 기준 미달 (${fmtA(av)})`});
    }
  }
  const el=document.getElementById('alert-banner');
  if(!alerts.length){el.innerHTML='';return;}
  el.innerHTML=`<div style="margin:10px 20px 0;background:#0f1826;border:1px solid #1e2d45;border-radius:10px;padding:10px 14px">
    <div style="font-size:10px;font-weight:700;color:#facc15;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px">⚡ 주의 알림</div>
    ${alerts.map(a=>`<div style="font-size:12px;padding:3px 0;border-top:1px solid #1a2338;color:${a.c}">${a.msg}</div>`).join('')}
  </div>`;
}

function calcImpact(){
  const ad=LD.alpha,sd=LD.seobu;
  if(!ad||!sd){document.getElementById('impact-grid').innerHTML='<div class="loading">시가총액 데이터 로드 후 계산 가능</div>';return;}
  document.getElementById('impact-grid').innerHTML=Object.entries(E).map(([k,etf])=>{
    const na=parseFloat(document.getElementById('ia-'+k)?.value||0)/100;
    const ns=parseFloat(document.getElementById('is-'+k)?.value||0)/100;
    const ca=etf.alpha?.weight||0,cs=etf.seobu?.weight||0,aum=etf.aum;
    const aa=(na-ca)*aum,as=Math.round(aa/ad.price);
    const sa=(ns-cs)*aum,ss=Math.round(sa/sd.price);
    const ai=ad.avgVol20>0?Math.abs(as)/ad.avgVol20:0;
    const si=sd.avgVol20>0?Math.abs(ss)/sd.avgVol20:0;
    const acl=aa>=0?'#ff5267':'#4fa8ff',scl=sa>=0?'#ff5267':'#4fa8ff';
    const reb=calcRebDates(k,etf.rebalance_months);
    return `<div class="icard">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span style="font-size:12px;font-weight:700">${etf.name.split(' ').slice(0,2).join(' ')}</span>
        <span style="font-size:10px;color:#5d6680">${reb?`D-${reb.d} (${reb.label})`:'–'}</span>
      </div>
      <div style="font-size:10px;font-weight:600;color:#9db4ff;margin-bottom:4px">알파리츠</div>
      <div class="irow"><span class="irow-label">비중 변화</span><span class="irow-val">${(ca*100).toFixed(2)}% → ${(na*100).toFixed(2)}% (${aa>=0?'+':''}${((na-ca)*100).toFixed(2)}%p)</span></div>
      <div class="irow"><span class="irow-label">예상 매매금액</span><span class="irow-val" style="color:${acl}">${aa>=0?'▲ 매수':'▼ 매도'} ${fmtA(Math.abs(aa))}</span></div>
      <div class="irow"><span class="irow-label">예상 주수</span><span class="irow-val" style="color:${acl}">${aa>=0?'▲':'▼'} ${fmt(Math.abs(as))}주</span></div>
      <div class="irow"><span class="irow-label">20일 거래량 대비</span><span class="irow-val">${ai.toFixed(1)}일치</span></div>
      <div style="height:1px;background:#1a2338;margin:8px 0"></div>
      <div style="font-size:10px;font-weight:600;color:#9db4ff;margin-bottom:4px">서부리츠</div>
      <div class="irow"><span class="irow-label">비중 변화</span><span class="irow-val">${(cs*100).toFixed(2)}% → ${(ns*100).toFixed(2)}% (${sa>=0?'+':''}${((ns-cs)*100).toFixed(2)}%p)</span></div>
      <div class="irow"><span class="irow-label">예상 매매금액</span><span class="irow-val" style="color:${scl}">${sa>=0?'▲ 매수':'▼ 매도'} ${fmtA(Math.abs(sa))}</span></div>
      <div class="irow"><span class="irow-label">예상 주수</span><span class="irow-val" style="color:${scl}">${sa>=0?'▲':'▼'} ${fmt(Math.abs(ss))}주</span></div>
      <div class="irow"><span class="irow-label">${etf.vol_days||20}일 거래량 대비</span><span class="irow-val">${si.toFixed(1)}일치</span></div>
      <div class="inote">AUM ${fmtA(aum)} 기준 추정. ${etf.seobu?.note?'<b style="color:#facc15">※ '+etf.seobu.note+'</b>':''}</div>
    </div>`;
  }).join('');
}

// ── ETF 비중 업데이트 버튼 ──────────────────────────────────
async function uploadEtfFile(){
  const key=document.getElementById('upload-etf-key').value;
  const file=document.getElementById('upload-file').files[0];
  const res=document.getElementById('upload-result');
  if(!file){res.textContent='파일을 선택해주세요.';res.style.color='#fb923c';return;}
  res.textContent='업로드 중…';res.style.color='#facc15';
  const fd=new FormData();
  fd.append('etf_key',key);fd.append('file',file);
  try{
    const r=await fetch('/upload-etf',{method:'POST',body:fd});
    const d=await r.json();
    if(d.error){res.textContent=`오류: ${d.error}`;res.style.color='#ef4444';return;}
    const aStr=d.alpha?`알파리츠 ${d.alpha.shares.toLocaleString()}주 (${(d.alpha.weight*100).toFixed(2)}%)`:'없음';
    const sStr=d.seobu?`서부리츠 ${d.seobu.shares.toLocaleString()}주 (${(d.seobu.weight*100).toFixed(2)}%)`:'없음 (미편입)';
    res.innerHTML=`✅ <b style="color:#4ade80">${key}</b> 적용 완료 — ${aStr} · ${sStr}`;
    res.style.color='#4ade80';
    // E 객체 실시간 반영
    if(E[key]){
      if(d.alpha){E[key].alpha.shares=d.alpha.shares;E[key].alpha.weight=d.alpha.weight;}
      if(d.seobu){E[key].seobu.shares=d.seobu.shares;E[key].seobu.weight=d.seobu.weight;}
    }
    renderHoldings();renderWeightInputs();calcImpact();
  }catch(e){res.textContent=`실패: ${e.message}`;res.style.color='#ef4444';}
}

async function updateEtfHoldings(){
  const btn=document.getElementById('btn-etf-upd');
  if(btn){btn.textContent='수집 중…';btn.disabled=true;}
  try{
    await fetch('/update-etf',{method:'POST'});
    let secs=20;
    const t=setInterval(async()=>{
      secs--;
      if(btn) btn.textContent='수집 중… ('+secs+'초)';
      if(secs<=0){
        clearInterval(t);
        await loadEtfCache();
        if(btn){btn.textContent='✓ 완료';setTimeout(()=>{btn.textContent='📡 ETF 비중 업데이트';btn.disabled=false;},3000);}
      }
    },1000);
  }catch(e){if(btn){btn.textContent='📡 ETF 비중 업데이트';btn.disabled=false;}}
}

// ── ETF 캐시에서 비중 반영 ────────────────────────────────────
async function loadEtfCache(){
  try{
    const r=await fetch('/etf-cache');
    if(!r.ok) return;
    const d=await r.json();
    if(!d.etfs) return;
    // 보유 현황 데이터 갱신 (각 ETF의 shares/weight 업데이트)
    for(const [key,data] of Object.entries(d.etfs)){
      if(E[key]){
        if(data.alpha&&data.alpha.shares>0){
          E[key].alpha.shares=data.alpha.shares;
          E[key].alpha.weight=data.alpha.weight;
          // hist에 최신 분기 추가
          const latestQ='26.2Q';
          if(E[key].alpha.hist.length&&E[key].alpha.hist[E[key].alpha.hist.length-1][0]!==latestQ)
            E[key].alpha.hist.push([latestQ,data.alpha.weight]);
        }
        if(data.seobu&&data.seobu.shares>0){
          E[key].seobu.shares=data.seobu.shares;
          E[key].seobu.weight=data.seobu.weight;
          if(E[key].seobu.note&&E[key].seobu.note.includes('예정')) E[key].seobu.note='';
          const latestQ='26.2Q';
          if(E[key].seobu.hist.length&&E[key].seobu.hist[E[key].seobu.hist.length-1][0]!==latestQ)
            E[key].seobu.hist.push([latestQ,data.seobu.weight]);
        }
      }
      // 입력창도 업데이트
      if(data.alpha){const el=document.getElementById('ia-'+key);if(el)el.value=(data.alpha.weight*100).toFixed(2);}
      if(data.seobu){const el=document.getElementById('is-'+key);if(el)el.value=(data.seobu.weight*100).toFixed(2);}
    }
    const tEl=document.getElementById('etf-cache-time');
    if(tEl) tEl.textContent='캐시: '+(d.updated_at_str||'');
    renderHoldings();calcImpact();
    console.log('ETF 캐시 반영 완료',d.updated_at_str);
  }catch(e){console.log('ETF cache load failed',e);}
}

async function refresh(){
  document.getElementById('pulse').style.background='#facc15';
  try{
    const [r1,r2]=await Promise.all([
      fetch('/getStockInfo?code=293940&auth_key='+AUTH+'&gubun=K').then(r=>r.json()),
      fetch('/getStockInfo?code=404990&auth_key='+AUTH+'&gubun=K').then(r=>r.json()),
    ]);
    function ext(raw){
      const st=raw.results?.STOCK||{},hist=raw.results?.HIST||[];
      const price=n(st.F15001);
      // 시총: STOCK F15028 우선 → HIST[0] F15028 → 현재가×상장주식수 순 폴백
      let mktcap=n(st.F15028);
      if(!mktcap&&hist.length) mktcap=n(hist[0].F15028||hist[0].F02007||0);
      if(!mktcap&&price) mktcap=price*n(st.F16143||st.F02002||0);
      const a20=hist.slice(0,20).map(h=>n(h.F15023)).filter(v=>v>0);
      const a60=hist.slice(0,60).map(h=>n(h.F15023)).filter(v=>v>0);
      const v20=hist.slice(0,20).map(h=>n(h.F15015)).filter(v=>v>0);
      return{price,mktcap,
        avg20:a20.length?a20.reduce((s,v)=>s+v,0)/a20.length:0,
        avg60:a60.length?a60.reduce((s,v)=>s+v,0)/a60.length:0,
        avgVol20:v20.length?v20.reduce((s,v)=>s+v,0)/v20.length:0};
    }
    LD={alpha:ext(r1),seobu:ext(r2)};
    renderThresholds();renderAlerts();renderHoldings();calcImpact();
    document.getElementById('upd').innerHTML=new Date().toLocaleTimeString('ko-KR')+' 업데이트<span class="pulse" id="pulse" style="background:#4ade80"></span>';
  }catch(e){document.getElementById('pulse').style.background='#4fa8ff';}
}

setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',second:'2-digit'});},1000);
renderDday();renderHoldings();renderWeightInputs();
loadEtfCache();
refresh();setInterval(refresh,60000);
</script></body></html>"""
    resp = Response(html, mimetype="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "no-store, no-cache"
    return resp

@app.route("/<path:endpoint>", methods=["GET","OPTIONS"])
def proxy(endpoint):
    if request.method=="OPTIONS": return Response(status=200)
    try:
        r=requests.get(f"{KOSCOM_BASE}/{endpoint}",params=request.args.to_dict(),timeout=10)
        r.raise_for_status()
        return Response(json.dumps(r.json(),ensure_ascii=False),
                        status=r.status_code,mimetype="application/json; charset=utf-8")
    except requests.Timeout:
        return Response('{"error":"응답 시간 초과"}',status=504,mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"error":str(e)}),status=500,mimetype="application/json")


# ══════════════════════════════════════════════════════════════════
#  이메일 자동 발송
# ══════════════════════════════════════════════════════════════════
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

def _send_report_email(html_content, subject, recipients, cc=[]):
    """Gmail SMTP로 HTML 보고서 이메일 발송"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = ", ".join(recipients)
        if cc: msg["Cc"] = ", ".join(cc)

        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.sendmail(GMAIL_USER, recipients + cc, msg.as_string())

        print(f"✅ 이메일 발송 완료 → {recipients}")
        return True
    except Exception as e:
        print(f"❌ 이메일 발송 오류: {e}")
        return False

def _auto_send_reports():
    """장마감 후 보고서 자동 생성 및 발송"""
    now = datetime.now(KST)
    print(f"\n📧 보고서 자동 발송 시작 ({now.strftime('%H:%M KST')})")

    # 데이터 수집
    sd = {}
    for code in ["293940","404990"]:
        try:
            r = requests.get(f"{KOSCOM_BASE}/getStockInfo",
                params={"code":code,"auth_key":AUTH,"gubun":"K"}, timeout=10)
            if r.ok: sd[code] = r.json()
        except: pass

    fd = {}
    for code in ["293940","404990"]:
        fd[code] = scrape_naver_frgn(code)

    # 일일 보고서 발송
    html = _build_report_html(sd, fd, now)
    subject = f"[신한리츠] 일일 거래 모니터링 보고서 {now.strftime('%Y.%m.%d')}"
    _send_report_email(html, subject, REPORT_TO, REPORT_CC)

def _start_scheduler():
    """매일 지정 시각에 보고서 자동 발송"""
    import time as _time
    print(f"📅 자동 발송 스케줄러 시작 (매일 {REPORT_HOUR:02d}:{REPORT_MIN:02d} KST)")
    sent_today = None
    while True:
        now = datetime.now(KST)
        today = now.date()
        # 평일만 (월=0 ~ 금=4)
        if now.weekday() < 5 and now.hour == REPORT_HOUR and now.minute == REPORT_MIN:
            if sent_today != today:
                sent_today = today
                threading.Thread(target=_auto_send_reports, daemon=True).start()
        _time.sleep(30)


if __name__=="__main__":
    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │  신한리츠 실시간 모니터링                            │")
    print(f"  │  대시보드:  http://localhost:{PORT}                      │")
    print("  │  종료: Ctrl + C                                     │")
    print("  └─────────────────────────────────────────────────────┘")
    print()
    try:
        # 자동 발송 스케줄러 백그라운드 실행
        threading.Thread(target=_start_scheduler, daemon=True).start()
        app.run(host="0.0.0.0",port=PORT,debug=False)
    except OSError as e:
        print(f"\n[오류] 포트 {PORT} 사용 중: {e}"); sys.exit(1)
