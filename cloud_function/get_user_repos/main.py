import requests
from flask import Request, jsonify

def get_user_repos(request: Request):
    """
    Cloud Function entry point.
    Supports both:
      - Public mode: list repos by username (no token required)
      - Private mode: list repos via GitHub token (includes private repos)

    Example inputs:
    ----------------
    Public mode:
    { "username": "karpathy" }

    Private mode:
    { "github_token": "github_pat_xxx" }

    Example outputs:
    ----------------
    [
        {
            "name": "minGPT",
            "full_name": "karpathy/minGPT",
            "description": "A minimal PyTorch GPT implementation",
            "html_url": "https://github.com/karpathy/minGPT",
            "private": false,
            "owner": "karpathy"
        },
        ...
    ]
    """
    try:
        data = request.get_json(silent=True) or {}
        token = data.get("github_token")
        username = data.get("username")

        # 🧭 Determine access mode
        if token:
            # Private access (authenticated user)
            url = "https://api.github.com/user/repos?per_page=100"
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "SunnySett"
            }
        elif username:
            # Public access (no authentication)
            url = f"https://api.github.com/users/{username}/repos?per_page=100"
            headers = {
                "Accept": "application/vnd.github+json",
                "User-Agent": "SunnySett"
            }
        else:
            return jsonify({"error": "Provide either github_token or username"}), 400

        # Fetch repositories
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 401:
            return jsonify({"error": "Invalid or expired GitHub token"}), 401
        r.raise_for_status()

        repos = r.json()
        simplified = [
            {
                "name": repo["name"],
                "full_name": repo["full_name"],
                "description": repo.get("description") or "",
                "html_url": repo.get("html_url"),
                "private": repo.get("private", False),
                "owner": repo.get("owner", {}).get("login", "")
            }
            for repo in repos
        ]

        return jsonify({
            "mode": "private" if token else "public",
            "count": len(simplified),
            "repos": simplified
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
