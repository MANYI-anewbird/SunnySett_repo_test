import os
import json
import re
from google.cloud import storage
from flask import jsonify, Request
from openai import OpenAI

# ====== ENV CONFIG ======
BUCKET_NAME = os.getenv("BUCKET_NAME", "user_private_models")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)


# ====== Step 1: Generate summary ======
def generate_llm_summary(readme_text: str, repo_name: str):
    """Use GPT to generate a clean and professional summary of the repo README."""
    prompt = f"""
You are an AI analyst summarizing open-source projects.

Summarize the following GitHub repository README concisely and clearly.

Repository: {repo_name}

README content:
\"\"\"
{readme_text[:6000]}
\"\"\"

Focus on:
1. What the project does.
2. Key technologies or frameworks used.
3. Main use cases or target users.
4. Any unique features or strengths.

Output as a short, professional paragraph without markdown, symbols, or extra formatting.
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=400
    )
    return response.choices[0].message.content.strip()


# ====== Step 2: Extract structured attributes ======
def generate_llm_attributes(summary_text: str):
    """Extract structured metadata from the LLM summary and ensure valid JSON."""
    prompt = f"""
Extract the following structured metadata from this project summary.
You MUST return valid JSON with the following keys:
core_concept, architecture_type, capabilities, tools_frameworks, target_audience.

If a field is not mentioned, set it to an empty string or an empty list.

Summary:
\"\"\"
{summary_text}
\"\"\"

Return ONLY a valid JSON object, nothing else.
Example:
{{
  "core_concept": "Lightweight GPT training framework",
  "architecture_type": "Transformer-based autoregressive model",
  "capabilities": ["training", "fine-tuning", "text generation"],
  "tools_frameworks": ["PyTorch", "NumPy", "Transformers"],
  "target_audience": ["ML engineers", "AI researchers"]
}}
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=300
    )

    raw = response.choices[0].message.content.strip()
    print(f"🧩 Raw LLM attributes output:\n{raw}")

    # Try parsing JSON directly
    try:
        attributes = json.loads(raw)
        print("✅ Parsed attributes as valid JSON.")
        return attributes
    except Exception as e:
        print(f"⚠️ Failed to parse attributes as JSON: {e}")

        # Fallback: regex extraction
        attributes = {}
        for key in ["core_concept", "architecture_type", "capabilities", "tools_frameworks", "target_audience"]:
            match = re.search(rf'"?{key}"?\s*:\s*(.*)', raw)
            if match:
                val = match.group(1).strip().strip(',').strip('"')
                attributes[key] = val
        return attributes


# ====== Step 3: Cloud Function Entrypoint ======
def summarize_repo_llm(request: Request):
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No JSON body received"}), 400

        username = data.get("username")
        repo_name = data.get("repo_name")
        if not username or not repo_name:
            return jsonify({"error": "Missing username or repo_name"}), 400

        print(f"🚀 Starting LLM summarization for {username}/{repo_name}")

        # ====== GCS setup ======
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        meta_prefix = f"{username}/{repo_name}/_meta"

        # ====== Load README ======
        readme_path = f"{meta_prefix}/README.md"
        readme_blob = bucket.blob(readme_path)
        if not readme_blob.exists():
            return jsonify({"error": f"README not found at {readme_path}"}), 404
        readme_text = readme_blob.download_as_text()
        print(f"✅ Loaded README ({len(readme_text)} chars)")

        # ====== Load existing repo_summary ======
        repo_summary_path = f"{meta_prefix}/repo_summary.json"
        repo_summary = {}
        if bucket.blob(repo_summary_path).exists():
            repo_summary = json.loads(bucket.blob(repo_summary_path).download_as_text())
            print("📦 Loaded existing repo_summary.json")

        # ====== Generate new summary ======
        llm_summary = generate_llm_summary(readme_text, repo_name)
        attributes = generate_llm_attributes(llm_summary)
        print("🧠 LLM summary + structured attributes generated")

        # ====== Merge & Clean ======
        repo_details = repo_summary or {}
        for noisy_field in ["summary", "readme", "raw_summary", "ingested_at", "generated_at", "model"]:
            repo_details.pop(noisy_field, None)

        repo_details.update({
            "repo": f"{username}/{repo_name}",
            "llm_summary": llm_summary,
            **attributes
        })

        # ====== Save final outputs ======
        final_json_path = f"{meta_prefix}/repo_details.json"
        llm_txt_path = f"{meta_prefix}/readme_llm_summary.txt"

        bucket.blob(final_json_path).upload_from_string(
            json.dumps(repo_details, indent=2, ensure_ascii=False),
            content_type="application/json"
        )
        bucket.blob(llm_txt_path).upload_from_string(
            llm_summary,
            content_type="text/plain"
        )

        print(f"✅ Uploaded clean repo_details.json and readme_llm_summary.txt")

        return jsonify({
            "status": "ok",
            "repo": repo_name,
            "bucket": BUCKET_NAME,
            "details_json": final_json_path
        }), 200

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500
