"""Learning loop — feedback from our uploaded videos to improve the pipeline.

Modules:
- analytics: Pull YouTube Data + Analytics API stats per video
- features: Extract quantifiable features from videos we produced
- database: Performance tracking SQLite schema
- analyzer: Correlate features with outcomes (views, retention, engagement)
- insights: LLM-based natural language learnings
- recommender: Suggest parameters for next video based on what worked
- sentiment: Comment sentiment analysis
- loop: Orchestration of the full feedback cycle
"""
