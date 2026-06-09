"""
Reads local_eventworx_projects.json and writes a slim version
(local_eventworx_slim.json) containing only the fields needed for
AI-assisted Discord channel matching.
"""

import json
import os

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
INPUT = os.path.join(CACHE_DIR, "local_eventworx_projects.json")
OUTPUT = os.path.join(CACHE_DIR, "local_eventworx_slim.json")

with open(INPUT, encoding="utf-8") as f:
    projects = json.load(f)

slim = [
    {
        "projectNumber": p["projectNumber"],
        "title": p["title"],
        "date": (p.get("rentStartDate") or "")[:10],  # YYYY-MM-DD
    }
    for p in projects
]

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(slim, f, ensure_ascii=False, indent=2)

print(f"Wrote {len(slim)} projects to {OUTPUT}")
