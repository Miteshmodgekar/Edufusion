"""
AI Career Assistant
====================
Uses Google Gemini (gemini-3.5-flash-lite) via the official google-genai SDK.
- FREE tier: 1 million tokens per minute — no rate limits for normal usage
- Get a free key: https://aistudio.google.com/app/apikey

Falls back to Groq if GEMINI_API_KEY is not set.
"""

import requests

# Groq fallback (OpenAI-compatible)
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

GEMINI_MODEL  = "gemini-3.5-flash-lite"
MAX_HISTORY   = 10
MAX_TOKENS    = 1024


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


def _call_gemini(api_key, system_prompt, history, user_message):
    """Call Google Gemini using the google-genai SDK."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        # Build conversation history for Gemini
        contents = []
        for msg in history[-MAX_HISTORY:]:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
        contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=MAX_TOKENS,
            ),
        )
        return True, response.text

    except Exception as e:
        err = str(e)
        if "quota" in err.lower() or "429" in err:
            return False, "AI service is busy. Please try again in a moment."
        return False, f"AI service error: {err[:150]}"


def _call_groq(api_key, system_prompt, history, user_message):
    """Fallback: call Groq's OpenAI-compatible API."""
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history[-6:])
    messages.append({"role": "user", "content": user_message})

    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "openai/gpt-oss-20b", "messages": messages, "max_tokens": 600},
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
    return True, (choices[0].get("message", {}).get("content") or "").strip()


def send_career_chat(api_key, model, system_prompt, history, user_message):
    """
    Main entry point. Uses Gemini if api_key looks like a Gemini key,
    otherwise falls back to Groq.
    Returns (success: bool, reply_or_error: str).
    """
    if not api_key:
        return False, (
            "Career Assistant not configured. "
            "Admin needs to add GEMINI_API_KEY in Railway variables. "
            "Get a free key at https://aistudio.google.com/app/apikey"
        )

    # Use Gemini SDK for all keys (it's the primary provider now)
    return _call_gemini(api_key, system_prompt, history, user_message)
