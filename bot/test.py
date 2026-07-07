#!/usr/bin/env python3
"""
test.py — Quick smoke-test for the ARGUS context builder.

Run from argus/bot/:
    python test.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Ai.context_builder import ContextBuilder

TEST_QUESTIONS = [
    "Summarize today's findings",
    "Which assets have KEV vulnerabilities?",
    "Show me overdue findings",
    "Which team owns the highest-risk vulnerabilities?",
    "What is the current risk overview?",
    "Show open vulnerabilities",
    "Give me an asset summary",
    "Something completely unrecognised xyz123",
]


def main():
    cb = ContextBuilder()
    print("ARGUS Context Builder Smoke Test\n" + "=" * 40)
    for q in TEST_QUESTIONS:
        intent = cb.determine_intent(q)
        print(f"\nQ: {q!r}")
        print(f"Intent: {intent}")
        ctx = cb.build_context(q)
        # Print first 120 chars to keep output readable
        preview = ctx[:120].replace("\n", " ↵ ")
        print(f"Context preview: {preview}…")
    print("\n" + "=" * 40 + "\nSmoke test complete.")


if __name__ == "__main__":
    main()
