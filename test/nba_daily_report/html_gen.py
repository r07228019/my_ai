"""Static website generation: HTML rendering and NBA.com link injection."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
_PROJECT_DIR = Path(__file__).parent


def _team_logo_url(team_id: int | None) -> str:
    """Return NBA.com primary logo URL for a given team id (SVG)."""
    return f"https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg" if team_id else ""


def _scores_block_html(scores: list[dict]) -> str:
    """Render the 各場比分 block — final score + team logos + top scorer per team."""
    if not scores:
        return ""

    cards = []
    for g in scores:
        home, away = g.get("home") or {}, g.get("away") or {}
        try:
            hs, as_ = int(home.get("score") or 0), int(away.get("score") or 0)
        except (TypeError, ValueError):
            hs = as_ = 0
        home_win_cls = " winner" if hs > as_ else ""
        away_win_cls = " winner" if as_ > hs else ""

        def leader_row(side: dict) -> str:
            ld = side.get("leader")
            if not ld:
                return ""
            tri = side.get("tricode") or ""
            name = ld.get("name") or ""
            pts = ld.get("points") if ld.get("points") is not None else "-"
            reb = ld.get("rebounds") if ld.get("rebounds") is not None else "-"
            ast = ld.get("assists") if ld.get("assists") is not None else "-"
            return (
                '<div class="score-leader">'
                f'<span class="score-leader-name"><span class="tri">{tri}</span>{name}</span>'
                '<span class="score-leader-stats">'
                f'{pts}<span class="stat-label">PTS</span> '
                f'{reb}<span class="stat-label">REB</span> '
                f'{ast}<span class="stat-label">AST</span>'
                '</span></div>'
            )

        def team_block(side: dict, pos_cls: str) -> str:
            logo = _team_logo_url(side.get("teamId"))
            tri = side.get("tricode") or ""
            name = side.get("team") or ""
            logo_img = f'<img src="{logo}" alt="{tri} logo" loading="lazy">' if logo else ""
            return (
                f'<div class="score-team {pos_cls}">{logo_img}'
                '<div class="score-team-info">'
                f'<span class="score-tri">{tri}</span>'
                f'<span class="score-name">{name}</span>'
                '</div></div>'
            )

        cards.append(
            '<div class="score-card">'
            '<div class="score-matchup">'
            f'{team_block(away, "away")}'
            f'<span class="score-value{away_win_cls}">{as_}</span>'
            '<span class="score-status">Final</span>'
            f'<span class="score-value{home_win_cls}">{hs}</span>'
            f'{team_block(home, "home")}'
            '</div>'
            '<div class="score-leaders">'
            f'{leader_row(away)}{leader_row(home)}'
            '</div>'
            '</div>'
        )

    return (
        '<section class="scores-block">'
        '<h2><i class="ph ph-scoreboard"></i> 各場比分</h2>'
        f'<div class="scores-grid">{"".join(cards)}</div>'
        '</section>'
    )


def _load_scores_summary(report_dir: Path, date_str: str) -> list[dict]:
    path = report_dir / f"scores_{date_str}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("讀取比分摘要失敗 %s: %s", path, e)
        return []


def _standings_html(standings: dict) -> str:
    """Generate HTML for the standings page."""
    if not standings or (not standings.get("East") and not standings.get("West")):
        return '<p style="color:var(--text-muted);padding:2rem">球隊排名資料暫時無法取得。</p>'

    season = standings.get("season", "")

    def render_conf(teams: list, conf_name: str) -> str:
        rows_html = ""
        for i, t in enumerate(teams):
            row_class = ""
            if i == 8:
                row_class = ' class="zone-divider playin-zone"'
            elif i == 10:
                row_class = ' class="zone-divider out-zone"'

            clinch = t["clinch"]
            badge = ""
            team_class = ""
            if clinch in ("x", "y", "z"):
                team_class = "clinched"
                badge = ' <span class="clinch-badge">✓</span>'
            elif clinch == "p":
                team_class = "playin"
                badge = ' <span class="clinch-badge playin">PI</span>'
            elif clinch == "e" or clinch == "o":
                team_class = "eliminated"

            streak = t["streak"] or ""
            streak_class = "streak-win" if streak.startswith("W") else "streak-loss"

            rows_html += f"""
      <tr{row_class}>
        <td class="seed-col">{t['seed']}</td>
        <td class="team-col {team_class}">
          <a href="https://www.nba.com/team/{t['team_id']}" target="_blank" rel="noopener" class="team-link">{t['city']} {t['name']}</a>{badge}
        </td>
        <td>{t['wins']}</td><td>{t['losses']}</td><td>{t['pct']}</td>
        <td>{t['gb']}</td>
        <td class="minor-col">{t['home']}</td>
        <td class="minor-col">{t['road']}</td>
        <td class="{streak_class}">{streak}</td>
      </tr>"""

        return f"""
    <div class="conf-standings">
      <h2 class="conf-title"><i class="ph ph-trophy"></i> {conf_name}</h2>
      <table class="standings-table">
        <thead><tr>
          <th>#</th>
          <th data-col="1">球隊</th>
          <th data-col="2" data-numeric="1">勝</th>
          <th data-col="3" data-numeric="1">敗</th>
          <th data-col="4" data-numeric="1">勝率</th>
          <th data-col="5">差距</th>
          <th class="minor-col" data-col="6">主場</th>
          <th class="minor-col" data-col="7">客場</th>
          <th data-col="8">近況</th>
        </tr></thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>"""

    east_html = render_conf(standings["East"], "東區 Eastern Conference")
    west_html = render_conf(standings["West"], "西區 Western Conference")
    return f"""<div class="standings-page">
  <p class="data-note">{season} 賽季常規賽成績　✓ 晉季後賽　PI 附加賽</p>
  {east_html}
  {west_html}
</div>"""



def _render_page(
    title: str,
    body_html: str,
    reports: list[dict],
    active_date: str,
    base_path: str,
    hero_label: str = "NBA Daily Report",
    hero_date_override: str | None = None,
) -> str:
    """Render a full HTML page by filling template.html with report data."""
    sidebar_items = []
    for i, r in enumerate(reports):
        href = (
            f"{base_path}index.html"
            if r["date"] == reports[0]["date"]
            else f"{base_path}reports/{r['filename']}"
        )
        active = ' aria-current="page"' if r["date"] == active_date else ""
        delay = f"{i * 0.06 + 0.05:.2f}s"
        sidebar_items.append(
            f'        <li style="animation-delay:{delay}"><a href="{href}"{active}>'
            f'<i class="ph ph-basketball"></i> {r["date"]}</a></li>'
        )
    sidebar = "\n".join(sidebar_items)

    standings_active = ' aria-current="page"' if active_date == "standings" else ""
    nav_links = (
        f'    <li><a href="{base_path}standings.html"{standings_active}><i class="ph ph-trophy"></i> 球隊排名</a></li>'
    )

    if hero_date_override:
        hero_date = hero_date_override
    else:
        try:
            _d = datetime.strptime(active_date, "%Y-%m-%d")
            hero_date = f"{_d.year} 年 {_d.month} 月 {_d.day} 日"
        except Exception:
            hero_date = active_date

    template = (_PROJECT_DIR / "template.html").read_text(encoding="utf-8")
    return (
        template
        .replace("%%TITLE%%", title)
        .replace("%%NAV_LINKS%%", nav_links)
        .replace("%%SIDEBAR%%", sidebar)
        .replace("%%HERO_LABEL%%", hero_label)
        .replace("%%HERO_DATE%%", hero_date)
        .replace("%%BODY_HTML%%", body_html)
    )


def _build_player_url_map() -> dict[str, tuple[int, str]]:
    """Return {full_name: (player_id, slug)} for all active NBA players (no network call)."""
    import unicodedata
    from nba_api.stats.static import players as nba_players

    def to_slug(name: str) -> str:
        name = unicodedata.normalize("NFKD", name)
        name = "".join(c for c in name if not unicodedata.combining(c))
        name = name.lower().replace("'", "").replace("`", "")
        name = re.sub(r"\s+", "-", name.strip())
        name = re.sub(r"[^a-z0-9-]", "", name)
        return name

    return {
        p["full_name"]: (p["id"], to_slug(p["full_name"]))
        for p in nba_players.get_players()
        if p.get("is_active")
    }


def _linkify_players(html: str, player_map: dict) -> str:
    """Replace full English player names in HTML text nodes with NBA.com hyperlinks."""
    sorted_names = sorted(player_map.keys(), key=len, reverse=True)
    relevant = [n for n in sorted_names if n in html]
    if not relevant:
        return html

    pattern = re.compile("(" + "|".join(re.escape(n) for n in relevant) + ")")

    def replacer(m: re.Match) -> str:
        name = m.group(0)
        pid, slug = player_map[name]
        return f'<a href="https://www.nba.com/player/{pid}/{slug}" target="_blank" rel="noopener" class="player-link">{name}</a>'

    parts = re.split(r"(<[^>]+>)", html)
    return "".join(
        part if part.startswith("<") else pattern.sub(replacer, part)
        for part in parts
    )


def _build_team_url_map() -> dict[str, tuple[int, str]]:
    """Return {abbreviation: (team_id, slug)} and {full_name: (team_id, slug)} for all NBA teams."""
    from nba_api.stats.static import teams as nba_teams

    result = {}
    for t in nba_teams.get_teams():
        slug = t["nickname"].lower()
        tid = t["id"]
        result[t["abbreviation"]] = (tid, slug)
        result[t["full_name"]] = (tid, slug)
    return result


def _linkify_teams(html: str, team_map: dict) -> str:
    """Replace team abbreviations and full English team names in HTML text nodes with NBA.com links."""
    tricodes = {k: v for k, v in team_map.items() if k.isupper() and len(k) <= 3}
    full_names = {k: v for k, v in team_map.items() if not (k.isupper() and len(k) <= 3)}

    relevant_tricodes = [k for k in tricodes if k in html]
    relevant_names = sorted([k for k in full_names if k in html], key=len, reverse=True)

    if not relevant_tricodes and not relevant_names:
        return html

    patterns = [re.escape(n) for n in relevant_names] + [r"\b" + re.escape(t) + r"\b" for t in relevant_tricodes]
    combined = re.compile("(" + "|".join(patterns) + ")")
    lookup = {**{n: full_names[n] for n in relevant_names}, **{t: tricodes[t] for t in relevant_tricodes}}

    def replacer(m: re.Match) -> str:
        name = m.group(0)
        tid, slug = lookup[name]
        return f'<a href="https://www.nba.com/team/{tid}/{slug}" target="_blank" rel="noopener" class="team-link">{name}</a>'

    parts = re.split(r"(<[^>]+>)", html)
    return "".join(
        part if part.startswith("<") else combined.sub(replacer, part)
        for part in parts
    )


def _linkify_report_date(html: str, date_str: str) -> str:
    """Wrap the Chinese date in the H1 title with a link to nba.com/games?date=YYYY-MM-DD."""
    url = f"https://www.nba.com/games?date={date_str}"
    date_pattern = re.compile(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)")
    replacement = f'<a href="{url}" target="_blank" rel="noopener" class="date-link">\\1</a>'
    return re.sub(
        r"(<h1[^>]*>)(.*?)(</h1>)",
        lambda m: m.group(1) + date_pattern.sub(replacement, m.group(2), count=1) + m.group(3),
        html,
        count=1,
        flags=re.DOTALL,
    )


def generate_website(report_dir: Path, docs_dir: Path, keep_n: int = 10) -> int:
    """Generate static HTML site from the last keep_n markdown reports.

    Returns the number of reports rendered.
    """
    import markdown as md_lib
    from .nba_data import fetch_standings

    md_files = sorted(report_dir.glob("nba_daily_report_*.md"), reverse=True)[:keep_n]
    if not md_files:
        return 0

    docs_reports_dir = docs_dir / "reports"
    docs_reports_dir.mkdir(parents=True, exist_ok=True)

    player_map = _build_player_url_map()
    team_map = _build_team_url_map()

    reports = []
    for p in md_files:
        date_str = p.stem.replace("nba_daily_report_", "")
        body = md_lib.markdown(p.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
        body = _linkify_players(body, player_map)
        body = _linkify_teams(body, team_map)
        body = _linkify_report_date(body, date_str)

        scores_html = _scores_block_html(_load_scores_summary(report_dir, date_str))
        if scores_html:
            body = re.sub(r"(</h1>)", r"\1" + scores_html, body, count=1)

        reports.append({"date": date_str, "body": body, "filename": f"{p.stem}.html"})

    (docs_dir / "index.html").write_text(
        _render_page(f"NBA Daily Report — {reports[0]['date']}", reports[0]["body"], reports, reports[0]["date"], base_path=""),
        encoding="utf-8",
    )

    for report in reports:
        (docs_reports_dir / report["filename"]).write_text(
            _render_page(f"NBA Daily Report — {report['date']}", report["body"], reports, report["date"], base_path="../"),
            encoding="utf-8",
        )

    for old in sorted(docs_reports_dir.glob("nba_daily_report_*.html"), reverse=True)[keep_n:]:
        old.unlink()

    # Standings page
    try:
        print("      取得球隊排名資料 ...")
        standings = fetch_standings()
        season = standings.get("season", "")
    except Exception as e:
        logger.warning("無法取得球隊排名: %s", e)
        standings, season = {}, ""
    (docs_dir / "standings.html").write_text(
        _render_page(
            f"NBA 球隊排名 {season}",
            _standings_html(standings),
            reports,
            active_date="standings",
            base_path="",
            hero_label="球隊排名 Standings",
            hero_date_override=f"{season} 賽季" if season else "本賽季",
        ),
        encoding="utf-8",
    )

    return len(reports)
