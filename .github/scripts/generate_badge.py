#!/usr/bin/env python3
"""Generate an SVG code quality badge from fuck-u-code JSON report."""

import json
import os

REPORT_PATH = "report.json"
BADGE_PATH = "assets/code-quality.svg"


def get_score_color(score: int) -> str:
    if score >= 80:
        return "#2E7D32"      # green
    elif score >= 60:
        return "#558B2F"      # light green
    elif score >= 40:
        return "#E65100"      # orange
    elif score >= 20:
        return "#BF360C"      # deep orange
    return "#B71C1C"           # red


def generate_svg(score: int) -> str:
    color = get_score_color(score)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="250" height="54" viewBox="0 0 250 54">
  <defs>
    <style type="text/css">
      .text-bold {{ font-family: 'Helvetica Bold', Helvetica, Arial, sans-serif; font-weight: bold; }}
      .text-regular {{ font-family: Helvetica, Arial, sans-serif; }}
      .emoji {{ font-family: "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji"; }}
    </style>
  </defs>

  <!-- 背景卡片 -->
  <rect x="0.5" y="0.5" width="249" height="53" rx="10" fill="#FFFFFF" stroke="#E0C9A6" stroke-width="1"/>

  <!-- 装饰图标 -->
  <text x="28" y="38" font-size="30" class="emoji" text-anchor="middle">\U0001f4a9</text>

  <!-- 品牌文字 -->
  <g fill="#5D4037" class="text-bold">
    <text x="53" y="21" font-size="9">CODE SMELL BY</text>
    <text x="52" y="41" font-size="21">Fuck-U-Code</text>
  </g>

  <!-- 分数指示器 -->
  <g transform="translate(185, 13)" fill="{color}">
    <text x="35" y="10" font-size="9" class="text-regular" text-anchor="middle">SCORE</text>
    <text x="35" y="28" font-size="18" class="text-bold" text-anchor="middle">{score}</text>
  </g>
</svg>
'''


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
