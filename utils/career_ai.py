"""
AI Career Assistant
====================
Uses Groq API (openai/gpt-oss-20b) — ultra-fast, free, no credit card.
Get a free key: https://console.groq.com/keys
"""

import re
import requests

GROQ_API_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL     = "openai/gpt-oss-20b"   # fastest clean model ~0.38s
MAX_HISTORY    = 10
MAX_TOKENS     = 1024
MAX_HISTORY_MESSAGES = MAX_HISTORY  # backward compat alias


def build_system_prompt(student, profile, avg_attendance, open_drives):
    skills    = (profile.skills    or "None listed")[:200] if profile else "None"
    interests = (profile.interests or "Not set")[:100]     if profile else "Not set"
    cgpa      = (profile.cgpa      if profile and profile.cgpa      is not None else "Not set")
    backlogs  = (profile.backlogs  if profile and profile.backlogs  is not None else 0)
    certs     = (profile.certifications or "None")[:150]   if profile else "None"

    drives_lines = []
    for d in open_drives[:5]:
        drives_lines.append(
            f"- {d.company_name} | {d.role} | {d.package_lpa or '?'} LPA | "
            f"Min CGPA: {d.min_cgpa} | Max Backlogs: {d.max_backlogs} | "
            f"Skills: {(d.required_skills or 'Any')[:60]}"
        )
    drives_text = "\n".join(drives_lines) if drives_lines else "No open drives."
    first_name  = student.name.split()[0]

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
- Use bullet points. Be helpful and clear."""


def send_career_chat(api_key, model, system_prompt, history, user_message):
    """
    Call Groq API. Returns (success: bool, reply_or_error: str).
    """
    import os
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()

    if not groq_key:
        return False, (
            "Career Assistant not configured. "
            "Add GROQ_API_KEY to .env. "
            "Get a free key at https://console.groq.com/keys"
        )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(
        {"role": m["role"], "content": m["content"]}
        for m in history[-MAX_HISTORY:]
    )
    messages.append({"role": "user", "content": user_message})

    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            },
            json={"model": GROQ_MODEL, "messages": messages, "max_tokens": MAX_TOKENS},
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        return False, f"Could not reach AI service: {e}"

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            detail = resp.text
        return False, f"AI service error ({resp.status_code}): {detail}"

    choices = resp.json().get("choices", [])
    if not choices:
        return False, "The AI didn't return a response. Please try again."

    text = (choices[0].get("message", {}).get("content") or "").strip()

    # Strip <think>…</think> blocks just in case model outputs reasoning
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return True, text
