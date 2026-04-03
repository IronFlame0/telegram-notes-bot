"""
Generate PROCESS_TASK.md from PROCESS_TASK.example.md.

Reads the example prompt, substitutes {BASE_DIR} and {PROMPT_LANGUAGE},
then translates it into the target language using Claude or Gemini.

Usage:
  python generate_prompt.py              # translate via Claude (default)
  python generate_prompt.py --gemini     # translate via Gemini
  python generate_prompt.py --no-translate  # just substitute vars, no translation
"""

import argparse
import asyncio
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLE_FILE = os.path.join(BASE_DIR, "PROCESS_TASK.example.md")
OUTPUT_FILE = os.path.join(BASE_DIR, "PROCESS_TASK.md")

PROMPT_LANGUAGE = os.getenv("PROMPT_LANGUAGE", "English")


def substitute(text: str) -> str:
    return text.replace("{BASE_DIR}", BASE_DIR).replace("{PROMPT_LANGUAGE}", PROMPT_LANGUAGE)


def translate_claude(text: str) -> str:
    prompt = (
        f"Translate the following markdown prompt into {PROMPT_LANGUAGE}. "
        "Keep all markdown formatting, code blocks, and placeholders exactly as-is. "
        "Translate only the human-readable text.\n\n"
        f"{text}"
    )
    result = subprocess.run(
        ["claude", "--print", "--dangerously-skip-permissions", prompt],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Claude error: {result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


async def translate_gemini(text: str) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        print("google-generativeai not installed. Run: pip install google-generativeai")
        sys.exit(1)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set in .env")
        sys.exit(1)

    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    model = genai.GenerativeModel(model_name)

    prompt = (
        f"Translate the following markdown prompt into {PROMPT_LANGUAGE}. "
        "Keep all markdown formatting, code blocks, and placeholders exactly as-is. "
        "Translate only the human-readable text.\n\n"
        f"{text}"
    )
    response = model.generate_content(prompt)
    return response.text.strip()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gemini", action="store_true", help="Use Gemini for translation")
    parser.add_argument("--no-translate", action="store_true", help="Skip translation, just substitute variables")
    args = parser.parse_args()

    if not os.path.exists(EXAMPLE_FILE):
        print(f"❌ {EXAMPLE_FILE} not found")
        sys.exit(1)

    with open(EXAMPLE_FILE, "r", encoding="utf-8") as f:
        template = f.read()

    substituted = substitute(template)

    if args.no_translate or PROMPT_LANGUAGE.lower() == "english":
        result = substituted
        print(f"✅ No translation needed (language: {PROMPT_LANGUAGE})")
    elif args.gemini:
        print(f"🔄 Translating via Gemini ({PROMPT_LANGUAGE})...")
        result = await translate_gemini(substituted)
    else:
        print(f"🔄 Translating via Claude ({PROMPT_LANGUAGE})...")
        result = translate_claude(substituted)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"✅ PROCESS_TASK.md generated → {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
