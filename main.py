"""
Combined runner — starts bot and processor together on the same machine.

Usage:
  python main.py              # use Claude (default)
  python main.py --gemini     # use Gemini

For separate machines:
  Machine 1 (processor):  python processor.py [--gemini]
  Machine 2 (bot):        PROCESSOR_URL=http://machine1:8080 python bot.py
"""

import argparse
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

# Ensure bot always knows where processor is when running together
os.environ.setdefault("PROCESSOR_URL", "http://localhost:8080")

import bot
import processor


async def main() -> None:
    await asyncio.gather(
        bot.run(),
        processor.run(startup_process=False),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gemini", action="store_true", help="Use Gemini instead of Claude")
    args = parser.parse_args()

    if args.gemini:
        processor.USE_GEMINI = True

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
