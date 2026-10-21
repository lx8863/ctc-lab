"""Render a local Star History SVG using authenticated GitHub metadata only.

Run with GitHub CLI authentication locally, or GH_TOKEN in GitHub Actions.
No access tokens or stargazer identities are written to the repository.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
import random
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
    x0, x1, y0, y1 = 82, 850, 84, 530

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
    # Roughness is cosmetic and deterministic; data coordinates stay exact.
    rng = random.Random(20261021)

    def rough_line(ax, ay, bx, by):
        length = max(abs(bx - ax), abs(by - ay))
        steps = max(2, int(length / 12))
        coords = []
        for i in range(steps + 1):
            t = i / steps
            jitter = rng.uniform(-1.1, 1.1) if 0 < i < steps else 0
            coords.append(f"{ax + (bx - ax) * t + (jitter if ax == bx else 0):.2f},{ay + (by - ay) * t + (jitter if ay == by else 0):.2f}")
        return f'<polyline points="{" ".join(coords)}" fill="none" stroke="#111" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>'

    axes = rough_line(x0, y0, x0, y1) + rough_line(x0, y1, x1, y1)
    legend_width = max(172, 34 + len(args.repo) * 8.5)
    lx, ly, lh = x0 + 8, y0 + 5, 36
    legend = ''.join((rough_line(lx, ly, lx + legend_width, ly),
                      rough_line(lx + legend_width, ly, lx + legend_width, ly + lh),
                      rough_line(lx + legend_width, ly + lh, lx, ly + lh),
                      rough_line(lx, ly + lh, lx, ly)))
    labels = ""
    if cumulative:
        for value in sorted({0, maximum // 2, maximum}):
            y = y1 - (y1 - y0) * value / maximum
            labels += f'<text x="68" y="{y + 5}" text-anchor="end">{value}</text>'
    data_line = f'<polyline points="{" ".join(points)}" stroke="#df5138" stroke-width="2.5" fill="none"/>' if cumulative else ""
    dates = f'<text x="{x0}" y="553">{first:%Y-%m-%d}</text><text x="{x1}" y="553" text-anchor="end">{now:%Y-%m-%d}</text>' if first.date() != now.date() else f'<text x="466" y="553" text-anchor="middle">{now:%Y-%m-%d}</text>'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="920" height="600" viewBox="0 0 920 600" role="img" aria-labelledby="title description">
<title id="title">Star History — {title}</title>
<desc id="description">{cumulative} current stars. Snapshot {now:%Y-%m-%d %H:%M UTC}. Counts current stargazers by their star date; excludes removed stars.</desc>
<rect width="920" height="600" fill="white"/>
<g font-family="Comic Sans MS, Comic Neue, cursive" font-size="17" fill="#111">
<path d="M 368 38 L 376 33 L 376 51 L 368 47 Z M 380 33 L 388 38 L 388 47 L 380 51 Z" fill="#a661c7"/>
<text x="400" y="51" font-size="23" font-weight="bold">Star History</text>
{axes}{legend}<rect x="{lx+8}" y="{ly+13}" width="9" height="9" rx="2" fill="#df5138"/>
<text x="{lx+25}" y="{ly+24}">{title}</text>
<text x="36" y="310" text-anchor="middle" transform="rotate(-90 36 310)">GitHub Stars</text>
{labels}{data_line}{dates}
<text x="466" y="581" text-anchor="middle" font-size="19">Date</text>
<text x="860" y="581" text-anchor="end" font-size="12" fill="#64748b">GitHub snapshot · {cumulative} stars</text>
</g></svg>'''
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")
    print(f"Updated {output}: {cumulative} stars")


if __name__ == "__main__":
    main()
