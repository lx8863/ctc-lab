"""Render a local Star History SVG using authenticated GitHub metadata only.

Run with GitHub CLI authentication locally, or GH_TOKEN in GitHub Actions.
No access tokens or stargazer identities are written to the repository.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import subprocess


def api(gh, endpoint, *, stars=False):
    command = [gh, "api", endpoint]
    if stars:
        command += ["-H", "Accept: application/vnd.github.star+json"]
    return json.loads(subprocess.check_output(command, text=True, encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="lx8863/ctc-lab")
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--output", default="docs/star-history.svg")
    args = parser.parse_args()
    metadata = api(args.gh, f"repos/{args.repo}")
    daily = Counter()
    # Paginate without storing or logging user profiles.
    if metadata["stargazers_count"]:
        page = 1
        while True:
            rows = api(args.gh, f"repos/{args.repo}/stargazers?per_page=100&page={page}", stars=True)
            for row in rows:
                daily[row["starred_at"][:10]] += 1
            if len(rows) < 100:
                break
            page += 1
        if sum(daily.values()) != metadata["stargazers_count"]:
            raise RuntimeError("Star count changed during capture; rerun to obtain a consistent snapshot")
    now = datetime.now(timezone.utc)
    first = datetime.fromisoformat(metadata["created_at"].replace("Z", "+00:00"))
    duration = max((now - first).total_seconds(), 86400)
    maximum = max(1, metadata["stargazers_count"])
    x0, x1, y0, y1 = 80, 850, 92, 330

    def point(timestamp, count):
        x = x0 + (x1 - x0) * max(0, (timestamp - first).total_seconds()) / duration
        y = y1 - (y1 - y0) * count / maximum
        return f"{x:.2f},{y:.2f}"

    points = [point(first, 0)]
    cumulative = 0
    for day, count in sorted(daily.items()):
        stamp = max(first, datetime.fromisoformat(day).replace(tzinfo=timezone.utc))
        points.append(point(stamp, cumulative))
        cumulative += count
        points.append(point(stamp, cumulative))
    points.append(point(now, cumulative))
    title = escape(args.repo)
    ticks = sorted({0, maximum // 2, maximum})
    grid = ""
    for value in ticks:
        y = y1 - (y1 - y0) * value / maximum
        grid += f'<path d="M {x0} {y} H {x1}" stroke="#dbe4ed"/><text x="65" y="{y + 5}" text-anchor="end">{value}</text>'
    empty = '<text x="465" y="210" text-anchor="middle" fill="#64748b">No stars yet</text>' if not cumulative else ""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="920" height="420" viewBox="0 0 920 420" role="img" aria-labelledby="title description">
<title id="title">Star History — {title}</title>
<desc id="description">{cumulative} current stars. Snapshot {now:%Y-%m-%d %H:%M UTC}. Counts current stargazers by their star date; excludes removed stars.</desc>
<rect width="920" height="420" rx="16" fill="#f8fafc"/>
<g font-family="Arial, sans-serif" font-size="14" fill="#334155">
<text x="40" y="39" font-size="24" font-weight="bold">Star History</text>
<text x="40" y="65">{title}</text><text x="850" y="44" text-anchor="end" font-size="20">{cumulative} stars</text>
<text x="{x0}" y="83">Stars</text>{grid}
<path d="M {x0} {y0} V {y1} H {x1}" stroke="#94a3b8" fill="none"/>
<polyline points="{' '.join(points)}" stroke="#0d9488" stroke-width="3" fill="none"/>{empty}
<circle cx="{point(now,cumulative).split(',')[0]}" cy="{point(now,cumulative).split(',')[1]}" r="4" fill="#0d9488"/>
<text x="{x0}" y="358">{first:%Y-%m-%d}</text><text x="{x1}" y="358" text-anchor="end">{now:%Y-%m-%d}</text>
<text x="40" y="394" font-size="12" fill="#64748b">GitHub snapshot: {now:%Y-%m-%d %H:%M UTC} · Current stargazers; removed stars excluded</text>
</g></svg>'''
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")
    print(f"Updated {output}: {cumulative} stars")


if __name__ == "__main__":
    main()
