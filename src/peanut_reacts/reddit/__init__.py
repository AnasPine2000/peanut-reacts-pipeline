"""Reddit-story content pipeline.

Pulls top posts from story subreddits (AITA, MaliciousCompliance, etc.),
cleans them, feeds through the existing TTS + SadTalker + compositor stack
to produce TikTok/YouTube Shorts.

Modules:
    scraper.py         — fetch + filter + dedupe posts (Reddit JSON API, no auth)
    db.py              — SQLite state store for the story queue
    story_processor.py — strip markdown, chunk, generate hook
    pipeline.py        — orchestrates scrape → process → render → queue-for-upload
"""
