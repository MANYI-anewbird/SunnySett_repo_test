import os
import json
import requests
from datetime import datetime
from google.cloud import storage
from flask import jsonify, Request
from taxonomy_schema import TASKS, DATA_TYPES, CATEGORIES


# ====== ENV CONFIG ======
BUCKET_NAME = os.getenv("BUCKET_NAME", "user_private_models")


# ====== Get GitHub Stars ======
def get_github_stars(username, repo_name):
    """Fetch stargazers_count from GitHub API."""
    try:
        url = f"https://api.github.com/repos/{username}/{repo_name}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json().get("stargazers_count", 0)
        else:
            print(f"⚠️ GitHub API returned {r.status_code}")
    except Exception as e:
        print(f"⚠️ Failed to fetch stars: {e}")
    return 0


# ====== Infer taxonomy from README ======
def infer_taxonomy_from_text(text: str):
    text_lower = text.lower()

    # Step 1. Try to match known task keywords
    task_match = next((t for t in TASKS if t in text_lower), "unknown")

    # Step 2. Add heuristic fallback for LLM-related repos
    if task_match == "unknown":
        if any(k in text_lower for k in ["gpt", "transformer", "llm", "language model", "chatbot", "agent"]):
            task_match = "text-generation"

    # Step 3. Find data type
    data_type = next(
        (k for k, v in DATA_TYPES.items() if task_match in v),
        "unknown"
    )

    # Step 4. Find possible industries
    industries = [
        cat for cat, tasks in CATEGORIES.items()
        if task_match in tasks
    ]

    return {
        "task": task_match,
        "data_types": [data_type] if data_type != "unknown" else [],
        "industry": industries[:3]  # Limit to top 3
    }


# ====== Main Cloud Function ======
def summarize_repo_meta(request: Request):
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON body received"}), 400

        username = data.get("username")
        repo_name = data.get("repo_name")

        if not username or not repo_name:
            return jsonify({"error": "Missing username or repo_name"}), 400

        print(f"🚀 Summarizing metadata for {username}/{repo_name}")

        # ====== Initialize GCS ======
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)

        # ====== Locate README file ======
        readme_path = f"{username}/{repo_name}/_meta/README.md"
        blob = bucket.blob(readme_path)

        if not blob.exists():
            return jsonify({"error": f"README not found at {readme_path}"}), 404

        readme_content = blob.download_as_text()
        print(f"✅ Loaded README ({len(readme_content)} chars)")

        # ====== Fetch taxonomy info ======
        taxonomy = infer_taxonomy_from_text(readme_content)

        # ====== Fetch GitHub likes (stars) ======
        stars = get_github_stars(username, repo_name)

        # ====== Build summary ======
        summary = {
            "modelId": f"{username}/{repo_name}",
            "author": username,
            "repo_url": f"https://github.com/{username}/{repo_name}",
            "pipeline_tag": taxonomy["task"],
            "tags": ["source:github"],
            "library": "Python",
            "license": "MIT",
            "likes": stars,
            "task": taxonomy["task"],
            "industry": taxonomy["industry"],
            "data_types": taxonomy["data_types"],
            "summary": (
                readme_content[:500] + "..."
                if len(readme_content) > 500
                else readme_content
            ),
            "ingested_at": datetime.utcnow().isoformat() + "Z",
        }

        # ====== Upload JSON and text ======
        meta_prefix = f"{username}/{repo_name}/_meta"
        summary_json_path = f"{meta_prefix}/repo_summary.json"
        summary_txt_path = f"{meta_prefix}/readme_summary.txt"

        bucket.blob(summary_json_path).upload_from_string(
            json.dumps(summary, indent=2, ensure_ascii=False),
            content_type="application/json"
        )
        bucket.blob(summary_txt_path).upload_from_string(
            summary["summary"],
            content_type="text/plain"
        )

        print(f"✅ Uploaded summary for {username}/{repo_name}")

        return jsonify({
            "status": "ok",
            "username": username,
            "repo": repo_name,
            "bucket": BUCKET_NAME,
            "summary_json": summary_json_path,
            "summary_txt": summary_txt_path
        }), 200

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500
