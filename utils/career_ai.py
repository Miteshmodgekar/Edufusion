"""
AI Career Assistant
====================
A real conversational AI (Groq — free tier, no credit card) that gives
students personalized career guidance — skills to learn, internships/drives
to target — grounded in their actual profile and the actual open drives in
this system (not invented companies or generic advice).

Requires GROQ_API_KEY in .env. Get a free key at https://console.groq.com/keys.
If it's not set, the chat endpoint returns a clear "not configured" message
instead of crashing.
"""

import requests

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_HISTORY_MESSAGES = 6    # keep last 6 messages to avoid rate limits
MAX_RESPONSE_TOKENS  = 600  # concise answers to stay within free-tier TPM


def build_system_prompt(student, profile, avg_attendance, open_drives):
    """
    Builds a system prompt grounded in the student's REAL data so the AI
    can't hallucinate companies, skills, or eligibility that don't exist
    in this system.
    """
    skills    = profile.skills if profile and profile.skills else "None listed yet"
    interests = profile.interests if profile and profile.interests else "Not set yet"
    cgpa      = profile.cgpa if profile and profile.cgpa is not None else "Not set"
    backlogs  = profile.backlogs if profile and profile.backlogs is not None else 0
    certs     = profile.certifications if profile and profile.certifications else "None listed"

    drives_lines = []
    for d in open_drives:
        drives_lines.append(
            f"- {d.company_name} — {d.role} | Package: {d.package_lpa or '?'} LPA | "
            f"Min CGPA: {d.min_cgpa} | Max Backlogs: {d.max_backlogs} | "
            f"Required skills: {d.required_skills or 'not specified'} | "
            f"Branches: {d.eligible_branches or 'All'} | Deadline: {d.deadline or 'not set'}"
        )
    drives_text = "\n".join(drives_lines) if drives_lines else "No drives are currently open in the system."

    return f"""You are EduFusion AI, a friendly career mentor for college students.

STUDENT: {student.name} | Dept: {student.department or 'CSE'} | Sem {student.semester or '?'} | CGPA: {cgpa} | Backlogs: {backlogs}
Skills: {skills[:200] if skills else 'None'}
Interests: {interests[:100] if interests else 'None'}
Certifications: {certs[:100] if certs else 'None'}
Attendance: {avg_attendance}%

OPEN PLACEMENT DRIVES:
{drives_text[:600] if drives_text else 'None'}

INSTRUCTIONS:
- Address student by first name ({student.name.split()[0]})
- Give specific, actionable advice based on their profile
- For eligibility questions, compare their CGPA/backlogs to drive requirements
- Keep answers concise but helpful. Use bullet points.
- Help with skills, resume, interviews, global jobs, certifications — everything career-related."""


def send_career_chat(api_key, model, system_prompt, history, user_message):
    """
    Calls Groq's OpenAI-compatible chat completions API.
    `history` is a list of {"role": "user"|"assistant", "content": str}
    dicts (already trimmed to MAX_HISTORY_MESSAGES by the caller).

    Returns (success: bool, reply_or_error: str).
    """
    if not api_key:
        return False, ("The Career Assistant isn't configured yet — an administrator "
                        "needs to add a free GROQ_API_KEY in the server's .env file "
                        "(get one at https://console.groq.com/keys).")

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": m["role"], "content": m["content"]} for m in history)
    messages.append({"role": "user", "content": user_message})

    try:
        resp = requests.post(
            GROQ_API_URL,
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
        return False, f"Could not reach the AI service right now: {e}"

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
