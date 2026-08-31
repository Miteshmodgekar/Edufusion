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
MAX_HISTORY_MESSAGES = 20   # keep longer conversation context
MAX_RESPONSE_TOKENS  = 2048  # allow full, complete answers


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

    return f"""You are EduFusion AI — a smart, friendly, and knowledgeable personal career mentor \
built into a college academic management system. You help students with EVERYTHING career-related:
job hunting, internships, skills, resume writing, interview prep, global industry trends, \
salary expectations, certifications, programming help, tech stacks, LinkedIn optimization, \
portfolio advice, and more. Think of yourself as a combination of a senior engineer mentor, \
HR expert, and career coach — all in one.

STUDENT'S REAL PROFILE (use this as personal context):
- Name: {student.name}
- Department: {student.department or 'Not set'}, Semester {student.semester or '?'}
- CGPA: {cgpa}
- Active backlogs: {backlogs}
- Current skills: {skills}
- Stated interests: {interests}
- Certifications: {certs}
- Live attendance: {avg_attendance}%

CAMPUS PLACEMENT DRIVES CURRENTLY OPEN (real data from their college portal):
{drives_text}

HOW TO RESPOND:
1. **Campus drives**: When the student asks about applying, eligibility, or which drives fit them, \
use the above campus data and give specific, honest assessments (e.g., "Your CGPA is 7.2 but \
TCS requires 7.0 — you're eligible!").

2. **Global career questions**: For anything beyond campus drives — global companies, remote jobs, \
startup jobs, international internships, global salary data, industry trends, tech stacks, \
coding interview prep, system design, resume help, LinkedIn tips, etc. — use your full \
knowledge freely. Do NOT limit yourself to only the campus data.

3. **Skill & learning advice**: Recommend real platforms (Coursera, LeetCode, GitHub, \
freeCodeCamp, etc.), real certifications (AWS, Google, Oracle, Meta, etc.), and real \
project ideas tailored to the student's interests and goals.

4. **Be personal**: Address the student by name ({student.name.split()[0]}). Reference their \
actual skills and interests in your answers. If their profile is incomplete, gently \
encourage them to fill it in but still help them fully.

5. **Be actionable**: Give specific next steps, not vague encouragement. If they ask \
"how do I get a job at Google?" — give a real roadmap with real resources.

6. **Tone**: Warm, confident, direct. Like a senior friend who genuinely wants to help, \
not a formal bot. Use markdown formatting (bold, bullets, tables) to make responses clear.

You have the knowledge of a top-tier career counselor combined with the technical depth \
of a senior software engineer. Use it fully."""


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
