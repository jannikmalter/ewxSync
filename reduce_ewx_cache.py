"""
Reads local_eventworx_projects.json and writes a slim version
(local_eventworx_slim.json) containing only the fields needed for
AI-assisted Discord channel matching.
"""

import json

INPUT = "local_eventworx_projects.json"
OUTPUT = "local_eventworx_slim.json"

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
