#!/usr/bin/env python3
"""Generate an SVG code quality badge from fuck-u-code JSON report."""

import json
import os

REPORT_PATH = "report.json"
BADGE_PATH = "assets/code-quality.svg"


def get_color(score: int) -> str:
    if score >= 80:
        return "#4c1"      # green
    elif score >= 60:
        return "#97CA00"   # yellow-green
    elif score >= 40:
        return "#dfb317"   # yellow
    elif score >= 20:
        return "#fe7d37"   # orange
    return "#e05d44"       # red


def get_label(score: int) -> str:
    if score >= 80:
        return "Excellent"
    elif score >= 60:
        return "Good"
    elif score >= 40:
        return "Fair"
    elif score >= 20:
        return "Poor"
    return "Shit Code"


def generate_svg(score: int) -> str:
    color = get_color(score)
    label = get_label(score)
    score_text = f"{score}/100"
    label_w = 100
    score_w = 72
    total_w = label_w + score_w

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="20">
  <linearGradient id="b" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <mask id="a">
    <rect width="{total_w}" height="20" rx="3" fill="#fff"/>
  </mask>
  <g mask="url(#a)">
    <rect width="{label_w}" height="20" fill="#555"/>
    <rect x="{label_w}" width="{score_w}" height="20" fill="{color}"/>
    <rect width="{total_w}" height="20" fill="url(#b)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <text x="{label_w / 2}" y="15" fill="#010101" fill-opacity=".3">Code Quality</text>
    <text x="{label_w / 2}" y="14">Code Quality</text>
    <text x="{label_w + score_w / 2}" y="15" fill="#010101" fill-opacity=".3">{score_text}</text>
    <text x="{label_w + score_w / 2}" y="14">{score_text}</text>
  </g>
</svg>'''


def main():
    if not os.path.exists(REPORT_PATH):
        print(f"No report found at {REPORT_PATH}, generating placeholder badge")
        score = 0
    else:
        with open(REPORT_PATH, "r", encoding="utf-8") as f:
            report = json.load(f)
        score = report.get("overallScore", 0)
        if isinstance(score, float):
            score = int(round(score))
        print(f"Code quality score: {score}/100")

    os.makedirs(os.path.dirname(BADGE_PATH), exist_ok=True)
    svg = generate_svg(score)
    with open(BADGE_PATH, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Badge written to {BADGE_PATH}")


if __name__ == "__main__":
    main()
