import os
import base64
import functions_framework
from google.cloud import storage
from github import Github
import requests

# ====== ENV CONFIG ======
BUCKET_NAME = os.getenv("BUCKET_NAME", "user_private_models")

# ====== Helper Function: Recursively Fetch All Files ======
def fetch_files_recursive(repo, path="", files=None):
    if files is None:
        files = []
    try:
        contents = repo.get_contents(path)
        for item in contents:
            if item.type == "dir":
                fetch_files_recursive(repo, item.path, files)
            elif item.name.endswith(".py") or item.name == "requirements.txt":
                files.append(item)
    except Exception as e:
        print(f"⚠️ Error while traversing {path}: {e}")
    return files


# ====== Cloud Function Entrypoint ======
@functions_framework.http
def raw_extract_user_repo(request):
    try:
        data = request.get_json(silent=True)
        if not data:
            return {"error": "No JSON body received"}, 400

        username = data.get("username")
        repo_name = data.get("repo_name")
        token = data.get("github_token")

        if not username or not repo_name:
            return {"error": "Missing username or repo_name"}, 400

        # ✅ normalize repo_name (avoid duplicated username)
        if "/" in repo_name:
            repo_name = repo_name.split("/")[-1]

        print(f"🚀 Starting extraction for user={username}, repo={repo_name}")

        # ====== GitHub Connection ======
        if token:
            print("🔐 Using authenticated GitHub access...")
            g = Github(token)
        else:
            print("🌍 Using public GitHub access...")
            g = Github()  # unauthenticated for public repos

        repo_fullname = f"{username}/{repo_name}"
        repo = g.get_repo(repo_fullname)
        print(f"✅ Connected to repo: {repo_fullname}")
        
        # ====== Try to get README.md at the beginning ======
        try:
           readme_content = repo.get_readme().decoded_content.decode("utf-8")
           blob = bucket.blob(f"{username}/{repo_name}/_meta/README.md")
           blob.upload_from_string(readme_content, content_type="text/markdown")
           print("✅ Saved README.md")
        except Exception as e:
           print(f"⚠️ No README.md found or failed to fetch: {e}")

        # ====== Fetch Files Recursively ======
        py_files = fetch_files_recursive(repo)
        print(f"📦 Found {len(py_files)} eligible files")

        # ====== Initialize GCS Client ======
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)

        # ====== Upload raw files ======
        uploaded = 0
        for file in py_files:
            try:
                blob_path = f"{username}/{repo_name}/raw/{file.path}"
                blob = bucket.blob(blob_path)
                blob.upload_from_string(
                    file.decoded_content,
                    content_type="text/plain"
                )
                uploaded += 1
                print(f"✅ Uploaded: {blob_path}")
            except Exception as e:
                print(f"⚠️ Failed to upload {file.path}: {e}")

        # ====== Create _meta folder & fetch README ======
        try:
            readme_content = None
            try:
                readme_content = repo.get_readme().decoded_content.decode("utf-8")
                print("📘 Fetched README via GitHub API")
            except Exception:
                print("⚠️ GitHub API README fetch failed, trying raw URL...")
                # fallback for public repo
                raw_url = f"https://raw.githubusercontent.com/{username}/{repo_name}/main/README.md"
                resp = requests.get(raw_url)
                if resp.status_code == 200:
                    readme_content = resp.text
                    print("📄 Fetched README via raw.githubusercontent.com")
                else:
                    print("⚠️ No README found for this repo")

            if readme_content:
                meta_blob = bucket.blob(f"{username}/{repo_name}/_meta/README.md")
                meta_blob.upload_from_string(readme_content, content_type="text/plain")
                print(f"✅ Uploaded README.md to _meta/")
            else:
                print("⚠️ README not found or empty")

        except Exception as e:
            print(f"⚠️ README extraction failed: {e}")

        # ====== Create repo_structure.json ======
        try:
            structure_data = [
                {"path": f.path, "size": f.size, "type": f.type}
                for f in py_files
            ]
            import json
            structure_blob = bucket.blob(f"{username}/{repo_name}/_meta/repo_structure.json")
            structure_blob.upload_from_string(
                json.dumps(structure_data, indent=2),
                content_type="application/json"
            )
            print("✅ Uploaded repo_structure.json to _meta/")
        except Exception as e:
            print(f"⚠️ Failed to create repo_structure.json: {e}")

        print(f"🎉 Done. Total uploaded: {uploaded}")

        return {
            "status": "ok",
            "username": username,
            "repo": repo_name,
            "bucket": BUCKET_NAME,
            "files_saved": uploaded,
        }

    except Exception as e:
        print(f"❌ Error: {e}")
        return {"error": str(e)}, 500
