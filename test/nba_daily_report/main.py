"""Fetch today's NBA games, summarize with Claude via AWS Bedrock, save as Markdown."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

logging.basicConfig(format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import anthropic
from nba_api.live.nba.endpoints import boxscore, scoreboard

from utils.aws_auth import setup_aws_session

PROJECT_DIR = Path(__file__).parent
REPO_ROOT = PROJECT_DIR.parent.parent
TIMEOUT_SECONDS = 600


def _timeout_handler(_signum, _frame):
    raise TimeoutError(f"整支程式執行超過 {TIMEOUT_SECONDS // 60} 分鐘，已中止")


def load_config() -> dict:
    config_path = PROJECT_DIR / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_system_prompt(config: dict) -> str:
    prompt_file = PROJECT_DIR / config["claude"]["system_prompt_file"]
    return prompt_file.read_text(encoding="utf-8").strip()


def fetch_todays_games() -> list[dict]:
    sb = scoreboard.ScoreBoard()
    return sb.get_dict().get("scoreboard", {}).get("games", [])


def fetch_boxscore(game_id: str) -> dict:
    bs = boxscore.BoxScore(game_id=game_id)
    return bs.get_dict().get("game", {})


def _extract_player_stats(team_box: dict) -> list[dict]:
    players = []
    for p in team_box.get("players", []) or []:
        stats = p.get("statistics") or {}
        if not p.get("played", "0") or p.get("played") == "0":
            continue
        players.append({
            "name": p.get("name"),
            "position": p.get("position") or "",
            "starter": p.get("starter", "0") == "1",
            "minutes": stats.get("minutesCalculated") or stats.get("minutes", ""),
            "points": stats.get("points"),
            "rebounds": stats.get("reboundsTotal"),
            "assists": stats.get("assists"),
            "steals": stats.get("steals"),
            "blocks": stats.get("blocks"),
            "turnovers": stats.get("turnovers"),
            "fg": f"{stats.get('fieldGoalsMade')}/{stats.get('fieldGoalsAttempted')}",
            "3pt": f"{stats.get('threePointersMade')}/{stats.get('threePointersAttempted')}",
            "ft": f"{stats.get('freeThrowsMade')}/{stats.get('freeThrowsAttempted')}",
            "plusMinus": stats.get("plusMinusPoints"),
        })
    return players


def build_games_payload(games: list[dict]) -> list[dict]:
    payload = []
    for g in games:
        game_id = g.get("gameId")
        home = g.get("homeTeam", {})
        away = g.get("awayTeam", {})
        status = g.get("gameStatusText", "")
        entry = {
            "gameId": game_id,
            "status": status,
            "statusCode": g.get("gameStatus"),
            "arena": (g.get("arena") or {}).get("arenaName"),
            "home": {
                "team": f"{home.get('teamCity','')} {home.get('teamName','')}".strip(),
                "tricode": home.get("teamTricode"),
                "score": home.get("score"),
                "wins": home.get("wins"),
                "losses": home.get("losses"),
            },
            "away": {
                "team": f"{away.get('teamCity','')} {away.get('teamName','')}".strip(),
                "tricode": away.get("teamTricode"),
                "score": away.get("score"),
                "wins": away.get("wins"),
                "losses": away.get("losses"),
            },
        }
        # Only pull box scores for finished games (statusCode 3 = Final).
        if g.get("gameStatus") == 3:
            try:
                box = fetch_boxscore(game_id)
                entry["home"]["players"] = _extract_player_stats(box.get("homeTeam", {}))
                entry["away"]["players"] = _extract_player_stats(box.get("awayTeam", {}))
                entry["home"]["teamStats"] = (box.get("homeTeam", {}) or {}).get("statistics")
                entry["away"]["teamStats"] = (box.get("awayTeam", {}) or {}).get("statistics")
            except Exception as e:
                logger.warning("取得 boxscore 失敗 (gameId=%s): %s", game_id, e)
                entry["boxscoreError"] = str(e)
        payload.append(entry)
    return payload


def summarize_with_claude(
    games_payload: list[dict], report_date: str, aws_region: str, config: dict, system_prompt: str,
) -> str:
    client = anthropic.AnthropicBedrock(aws_region=aws_region)
    bedrock_model = config["aws"]["bedrock_model"]
    max_tokens = config["claude"]["max_tokens"]
    user_prompt = (
        f"以下是 {report_date} (美東時間) 的 NBA 比賽資料，請依系統指示撰寫繁體中文每日戰報。\n\n"
        f"```json\n{json.dumps(games_payload, ensure_ascii=False, indent=2)}\n```"
    )
    with client.messages.stream(
        model=bedrock_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        final = stream.get_final_message()
    usage = final.usage
    print(f"      Token 用量：input={usage.input_tokens}, output={usage.output_tokens}, total={usage.input_tokens + usage.output_tokens}")
    return "".join(b.text for b in final.content if b.type == "text")


def _render_page(
    title: str,
    body_html: str,
    reports: list[dict],
    active_date: str,
    base_path: str,
) -> str:
    """Render a full HTML page with dark theme, glassmorphism header, animated sidebar."""
    sidebar_items = []
    for i, r in enumerate(reports):
        href = (
            f"{base_path}index.html"
            if r["date"] == reports[0]["date"]
            else f"{base_path}reports/{r['filename']}"
        )
        active = ' aria-current="page"' if r["date"] == active_date else ""
        delay = f"{i * 0.06 + 0.05:.2f}s"
        sidebar_items.append(f'        <li style="animation-delay:{delay}"><a href="{href}"{active}>{r["date"]}</a></li>')
    sidebar = "\n".join(sidebar_items)
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0d1117;
      --surface: #161b22;
      --sidebar-bg: #090d13;
      --border: #30363d;
      --text: #e6edf3;
      --text-muted: #7d8590;
      --accent: #6366f1;
      --accent-light: #818cf8;
      --accent-glow: rgba(99, 102, 241, 0.25);
      --header-height: 3.5rem;
      --sidebar-width: 220px;
      font-family: 'Inter', 'Noto Sans TC', sans-serif;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      -webkit-font-smoothing: antialiased;
    }}

    /* Progress bar */
    #progress-bar {{
      position: fixed;
      top: 0; left: 0;
      height: 3px; width: 0%;
      background: linear-gradient(90deg, var(--accent), var(--accent-light));
      z-index: 1000;
      transition: width 0.1s linear;
    }}

    /* Header — glassmorphism + shrink on scroll */
    header {{
      position: sticky;
      top: 0; z-index: 100;
      height: var(--header-height);
      display: flex;
      align-items: center;
      padding: 0 1.5rem;
      background: rgba(9, 13, 19, 0.85);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-bottom: 1px solid var(--border);
      transition: height 0.3s ease;
    }}
    header.shrunk {{ height: 2.6rem; }}
    .header-inner {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
    }}
    .header-title {{ font-size: 1rem; font-weight: 700; letter-spacing: -0.01em; transition: font-size 0.3s ease; }}
    header.shrunk .header-title {{ font-size: 0.875rem; }}
    .hamburger {{
      display: none;
      background: none;
      border: 1px solid var(--border);
      border-radius: 6px;
      cursor: pointer;
      padding: 0.3rem 0.6rem;
      color: var(--text-muted);
      font-size: 1rem;
      line-height: 1;
      transition: color 0.2s, border-color 0.2s;
    }}
    .hamburger:hover {{ color: var(--text); border-color: var(--accent); }}

    /* Layout */
    .layout {{
      display: grid;
      grid-template-columns: var(--sidebar-width) 1fr;
      min-height: calc(100vh - var(--header-height));
    }}

    /* Sidebar stagger fade-in */
    .sidebar li {{
      opacity: 0;
      animation: slideInLeft 0.3s ease forwards;
    }}
    @keyframes slideInLeft {{
      from {{ opacity: 0; transform: translateX(-12px); }}
      to   {{ opacity: 1; transform: translateX(0); }}
    }}

    /* Sidebar */
    .sidebar {{
      background: var(--sidebar-bg);
      border-right: 1px solid var(--border);
      padding: 1.5rem 0;
      position: sticky;
      top: var(--header-height);
      height: calc(100vh - var(--header-height));
      overflow-y: auto;
    }}
    .sidebar-label {{
      font-size: 0.65rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-muted);
      padding: 0 1.25rem;
      margin-bottom: 0.6rem;
    }}
    .sidebar ul {{ list-style: none; }}
    .sidebar a {{
      display: block;
      padding: 0.5rem 1.25rem;
      font-size: 0.875rem;
      color: var(--text-muted);
      text-decoration: none;
      position: relative;
      transition: color 0.2s, background 0.2s;
    }}
    .sidebar a::before {{
      content: '';
      position: absolute;
      left: 0; top: 0; bottom: 0;
      width: 3px;
      background: var(--accent);
      border-radius: 0 2px 2px 0;
      transform: scaleY(0);
      transition: transform 0.2s ease;
    }}
    .sidebar a:hover {{ color: var(--text); background: rgba(255,255,255,0.04); }}
    .sidebar a:hover::before {{ transform: scaleY(1); }}
    .sidebar a[aria-current] {{
      color: var(--accent-light);
      font-weight: 600;
      background: var(--accent-glow);
    }}
    .sidebar a[aria-current]::before {{ transform: scaleY(1); }}
    .sidebar a[aria-current]::after {{
      content: '●';
      position: absolute;
      right: 1rem; top: 50%;
      transform: translateY(-50%);
      font-size: 0.35rem;
      color: var(--accent-light);
      animation: dotPulse 2s ease-in-out infinite;
    }}
    @keyframes dotPulse {{
      0%, 100% {{ opacity: 1; transform: translateY(-50%) scale(1); }}
      50%       {{ opacity: 0.3; transform: translateY(-50%) scale(2.5); }}
    }}

    /* Content + fade-in */
    .content {{
      padding: 2.5rem 3rem;
      max-width: 860px;
      animation: fadeIn 0.4s ease both;
    }}
    @keyframes fadeIn {{
      from {{ opacity: 0; transform: translateY(14px); }}
      to   {{ opacity: 1; transform: translateY(0); }}
    }}

    /* Scroll-triggered section fade */
    .fade-section {{
      opacity: 0;
      transform: translateY(16px);
      transition: opacity 0.5s ease, transform 0.5s ease;
    }}
    .fade-section.visible {{ opacity: 1; transform: translateY(0); }}

    /* Content exit transition (page navigation) */
    .content.exiting {{
      opacity: 0;
      transform: translateY(10px);
      transition: opacity 0.2s ease, transform 0.2s ease;
    }}

    /* Markdown typography */
    .content h1 {{
      font-size: 1.75rem; font-weight: 700;
      line-height: 1.2; margin-bottom: 1.25rem;
      letter-spacing: -0.02em;
      background: linear-gradient(90deg, #f0f6fc 0%, #818cf8 45%, #c7d2fe 55%, #f0f6fc 100%);
      background-size: 300% auto;
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      animation: shimmer 4s linear infinite;
    }}
    @keyframes shimmer {{
      from {{ background-position: 300% center; }}
      to   {{ background-position: -300% center; }}
    }}
    .content h2 {{
      font-size: 1.2rem; font-weight: 600;
      margin: 2rem 0 0.6rem; color: #f0f6fc;
      padding-bottom: 0.4rem;
      border-bottom: 1px solid var(--border);
    }}
    .content h3 {{
      font-size: 1rem; font-weight: 600;
      margin: 1.5rem 0 0.4rem; color: #cdd9e5;
    }}
    .content p {{ margin-bottom: 0.9rem; color: #adbac7; line-height: 1.8; }}
    .content strong {{ font-weight: 600; color: #cdd9e5; }}
    .content a {{ color: var(--accent-light); text-decoration: none; }}
    .content a:hover {{ text-decoration: underline; }}
    .content ul, .content ol {{ padding-left: 1.5rem; margin-bottom: 0.9rem; color: #adbac7; }}
    .content li {{ margin-bottom: 0.25rem; line-height: 1.75; }}
    .content hr {{ border: none; border-top: 1px solid var(--border); margin: 2rem 0; }}
    .content blockquote {{
      border-left: 3px solid var(--accent);
      padding: 0.5rem 1rem; margin: 1rem 0;
      background: rgba(99,102,241,0.08);
      color: var(--text-muted);
      border-radius: 0 4px 4px 0;
    }}
    .content code {{
      font-family: 'Fira Code', 'Courier New', monospace;
      font-size: 0.85em;
      background: #1c2128; color: #adbac7;
      padding: 0.15em 0.4em;
      border-radius: 4px;
      border: 1px solid var(--border);
    }}
    .content pre {{
      background: #1c2128; border: 1px solid var(--border);
      border-radius: 8px; padding: 1rem 1.25rem;
      overflow-x: auto; margin: 1rem 0;
    }}
    .content pre code {{ background: none; border: none; padding: 0; font-size: 0.875rem; }}
    .content table {{ width: 100%; border-collapse: collapse; margin: 1.25rem 0; font-size: 0.9rem; }}
    .content th {{
      background: #1c2128; padding: 0.6rem 1rem;
      text-align: left; font-weight: 600; color: #cdd9e5;
      border-bottom: 2px solid var(--border);
    }}
    .content td {{ padding: 0.55rem 1rem; border-bottom: 1px solid #21262d; color: #adbac7; }}
    .content td:first-child {{ border-left: 2px solid transparent; transition: border-color 0.25s ease; }}
    .content tr:hover td {{ background: rgba(255,255,255,0.02); }}
    .content tr:hover td:first-child {{ border-left-color: var(--accent); }}

    /* Back to top */
    #back-to-top {{
      position: fixed;
      bottom: 2rem; right: 2rem;
      width: 2.5rem; height: 2.5rem;
      border-radius: 50%;
      background: var(--accent); color: #fff;
      border: none; cursor: pointer;
      font-size: 1rem;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 16px var(--accent-glow);
      opacity: 0; transform: translateY(10px);
      transition: opacity 0.3s, transform 0.3s, background 0.2s;
      pointer-events: none;
    }}
    #back-to-top.visible {{ opacity: 1; transform: translateY(0); pointer-events: auto; }}
    #back-to-top:hover {{ background: var(--accent-light); }}

    /* Overlay */
    .overlay {{
      display: none; position: fixed;
      inset: 0; top: var(--header-height);
      background: rgba(0,0,0,0.6); z-index: 98;
      backdrop-filter: blur(2px);
    }}
    .overlay.open {{ display: block; }}

    /* RWD */
    @media (max-width: 768px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .hamburger {{ display: block; }}
      .sidebar {{
        position: fixed;
        top: var(--header-height); left: -100%;
        width: var(--sidebar-width);
        height: calc(100vh - var(--header-height));
        z-index: 99;
        transition: left 0.3s ease;
      }}
      .sidebar.open {{ left: 0; }}
      .content {{ padding: 1.5rem 1.25rem; }}
      #back-to-top {{ bottom: 1.25rem; right: 1.25rem; }}
    }}
  </style>
</head>
<body>
  <div id="progress-bar"></div>

  <header>
    <div class="header-inner">
      <span class="header-title">🏀 NBA Daily Report</span>
      <button class="hamburger" id="hamburger" aria-label="選單">☰</button>
    </div>
  </header>

  <div class="overlay" id="overlay"></div>

  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <p class="sidebar-label">歷史報告</p>
      <ul>
{sidebar}
      </ul>
    </aside>
    <article class="content">
      {body_html}
    </article>
  </div>

  <button id="back-to-top" aria-label="回到頂部">↑</button>

  <script>
    const bar = document.getElementById('progress-bar');
    window.addEventListener('scroll', () => {{
      const d = document.documentElement;
      bar.style.width = (d.scrollTop / (d.scrollHeight - d.clientHeight) * 100) + '%';
    }});

    const btn = document.getElementById('back-to-top');
    window.addEventListener('scroll', () => btn.classList.toggle('visible', window.scrollY > 300));
    btn.addEventListener('click', () => window.scrollTo({{ top: 0, behavior: 'smooth' }}));

    const hamburger = document.getElementById('hamburger');
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('overlay');
    hamburger.addEventListener('click', () => {{
      sidebar.classList.toggle('open');
      overlay.classList.toggle('open');
    }});
    overlay.addEventListener('click', () => {{
      sidebar.classList.remove('open');
      overlay.classList.remove('open');
    }});

    // Header shrink on scroll
    window.addEventListener('scroll', () => {{
      document.querySelector('header').classList.toggle('shrunk', window.scrollY > 60);
    }});

    // Scroll-triggered section fade (IntersectionObserver)
    const fadeEls = document.querySelectorAll('.content h2, .content h3');
    fadeEls.forEach(el => el.classList.add('fade-section'));
    const sectionObserver = new IntersectionObserver((entries) => {{
      entries.forEach(e => {{
        if (e.isIntersecting) {{
          e.target.classList.add('visible');
          sectionObserver.unobserve(e.target);
        }}
      }});
    }}, {{ threshold: 0.1, rootMargin: '0px 0px -30px 0px' }});
    fadeEls.forEach(el => sectionObserver.observe(el));

    // Link click fade-out transition
    document.querySelectorAll('.sidebar a').forEach(link => {{
      link.addEventListener('click', function(e) {{
        const href = this.getAttribute('href');
        if (href && !href.startsWith('#') && !this.hasAttribute('aria-current')) {{
          e.preventDefault();
          document.querySelector('.content').classList.add('exiting');
          setTimeout(() => {{ window.location.href = href; }}, 200);
        }}
      }});
    }});
  </script>
</body>
</html>"""


def generate_website(report_dir: Path, docs_dir: Path, keep_n: int = 10) -> int:
    """Generate static HTML site from the last keep_n markdown reports.

    Returns the number of reports rendered.
    """
    import markdown as md_lib

    md_files = sorted(report_dir.glob("nba_daily_report_*.md"), reverse=True)[:keep_n]
    if not md_files:
        return 0

    docs_reports_dir = docs_dir / "reports"
    docs_reports_dir.mkdir(parents=True, exist_ok=True)

    reports = [
        {
            "date": p.stem.replace("nba_daily_report_", ""),
            "body": md_lib.markdown(p.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"]),
            "filename": f"{p.stem}.html",
        }
        for p in md_files
    ]

    # index.html = latest report
    (docs_dir / "index.html").write_text(
        _render_page(f"NBA Daily Report — {reports[0]['date']}", reports[0]["body"], reports, reports[0]["date"], base_path=""),
        encoding="utf-8",
    )

    # individual pages
    for report in reports:
        (docs_reports_dir / report["filename"]).write_text(
            _render_page(f"NBA Daily Report — {report['date']}", report["body"], reports, report["date"], base_path="../"),
            encoding="utf-8",
        )

    # remove html files beyond keep_n
    for old in sorted(docs_reports_dir.glob("nba_daily_report_*.html"), reverse=True)[keep_n:]:
        old.unlink()

    return len(reports)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NBA 每日戰報產生器 (Bedrock)")
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS profile name (對應 ~/.aws/credentials 與 ~/.aws/config)",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="覆蓋 profile 中的 AWS region (例如 us-east-1)",
    )
    return parser.parse_args()


def main() -> int:
    start = time.perf_counter()
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)

    try:
        args = parse_args()
        config = load_config()
        system_prompt = load_system_prompt(config)

        default_region = config["aws"]["default_region"]
        bedrock_model = config["aws"]["bedrock_model"]
        output_dir = PROJECT_DIR / config["output"]["dir"]
        docs_dir = REPO_ROOT / config["output"]["docs_dir"]

        profile = args.profile or config["aws"].get("default_profile")
        if profile:
            print(f"[*] 使用 AWS profile: {profile}")

        aws_region = setup_aws_session(profile, args.region, default_region)

        et_now = datetime.now(ZoneInfo("US/Eastern"))
        report_date = et_now.strftime("%Y-%m-%d")

        print(f"[1/4] 擷取 {report_date} (ET) 的 NBA 比賽資料 ...")
        games = fetch_todays_games()
        print(f"      找到 {len(games)} 場比賽")

        payload = build_games_payload(games)

        print(f"[2/4] 呼叫 Claude ({bedrock_model}) 彙整報告 ...")
        if not payload:
            report = f"# 🏀 NBA 每日戰報 — {report_date}\n\n本日美東時間暫無 NBA 賽事。\n"
        else:
            summary = summarize_with_claude(payload, report_date, aws_region, config, system_prompt)
            report = f"{summary}\n"

        output_dir.mkdir(exist_ok=True)
        out_path = output_dir / f"nba_daily_report_{report_date}.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"[3/4] 已寫入：{out_path}")

        print(f"[4/4] 更新靜態網頁 ...")
        n = generate_website(output_dir, docs_dir)
        print(f"      已產生 {n} 份報告至 {docs_dir}")
        return 0
    except (ValueError, TimeoutError) as e:
        logger.error("%s", e)
        return 1
    finally:
        signal.alarm(0)
        elapsed = time.perf_counter() - start
        print(f"[*] 總執行時間：{elapsed:.2f} 秒")


if __name__ == "__main__":
    sys.exit(main())
