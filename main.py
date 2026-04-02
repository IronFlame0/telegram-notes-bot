"""
Combined runner — starts bot and processor together on the same machine.

Usage:
  python main.py

For separate machines:
  Machine 1 (processor):  python processor.py
  Machine 2 (bot):        PROCESSOR_URL=http://machine1:8080 python bot.py
"""

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
        # startup_process=False: bot triggers processing after its drain period
        processor.run(startup_process=False),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
