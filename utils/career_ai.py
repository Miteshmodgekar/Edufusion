"""
AI Career Assistant
====================
Uses Google Gemini (primary — 1M TPM FREE, no card needed) or
Groq (fallback — 8K TPM free) via OpenAI-compatible endpoints.

Get free Gemini key: https://aistudio.google.com/app/apikey
Get free Groq key:   https://console.groq.com/keys
"""

import requests

# Gemini — OpenAI-compatible endpoint (1 million TPM free)
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODEL   = "gemini-2.0-flash"

# Groq — fallback (8K TPM free)
GROQ_API_URL   = "https://api.groq.com/openai/v1/chat/completions"

MAX_HISTORY_MESSAGES = 10   # last 10 messages
MAX_RESPONSE_TOKENS  = 1024 # good length answers


def build_system_prompt(student, profile, avg_attendance, open_drives):
    skills    = (profile.skills or "None listed")[:200] if profile else "None"
    interests = (profile.interests or "Not set")[:100] if profile else "Not set"
    cgpa      = (profile.cgpa if profile and profile.cgpa is not None else "Not set")
    backlogs  = (profile.backlogs if profile and profile.backlogs is not None else 0)
    certs     = (profile.certifications or "None")[:150] if profile else "None"

    drives_lines = []
    for d in open_drives[:5]:  # max 5 drives to save tokens
        drives_lines.append(
            f"- {d.company_name} | {d.role} | {d.package_lpa or '?'} LPA | "
            f"Min CGPA: {d.min_cgpa} | Max Backlogs: {d.max_backlogs} | "
            f"Skills: {(d.required_skills or 'Any')[:60]}"
        )
    drives_text = "\n".join(drives_lines) if drives_lines else "No open drives."

    first_name = student.name.split()[0]
    return f"""You are EduFusion AI, a smart career mentor for college students.

STUDENT: {student.name} | {student.department or 'CSE'} | Sem {student.semester or '?'} | CGPA: {cgpa} | Backlogs: {backlogs}
Skills: {skills}
Interests: {interests}
Certifications: {certs}
Attendance: {avg_attendance}%

OPEN CAMPUS DRIVES:
{drives_text}

RULES:
- Address {first_name} by name
- Give specific, actionable career advice
- For drive eligibility, compare their CGPA/backlogs to requirements
- Cover: skills, resume, interviews, certifications, global jobs, salary info
- Use bullet points. Be concise but complete."""


def send_career_chat(api_key, model, system_prompt, history, user_message):
    """
    Calls Gemini (primary) or Groq (fallback) OpenAI-compatible API.
    api_key: can be GEMINI_API_KEY or GROQ_API_KEY
    Returns (success: bool, reply_or_error: str).
    """
    if not api_key:
        return False, (
            "Career Assistant not configured. "
            "Admin needs to add GEMINI_API_KEY in Railway variables. "
            "Get a free key at https://aistudio.google.com/app/apikey"
        )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history[-MAX_HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": user_message})

    # Determine which API to use based on model name
    if "gemini" in model.lower():
        api_url = GEMINI_API_URL
    else:
        api_url = GROQ_API_URL

    try:
        resp = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": MAX_RESPONSE_TOKENS,
            },
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        return False, f"Could not reach the AI service: {e}"

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            detail = resp.text
        return False, f"AI service error ({resp.status_code}): {detail}"

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        return False, "The AI didn't return a response. Please try again."

    reply = (choices[0].get("message", {}).get("content") or "").strip()
    if not reply:
        return False, "The AI didn't return a response. Please try again."

    return True, reply
