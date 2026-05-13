"""
agent/scraper/indeed.py — Indeed scraper via JobSpy.
"""

from agent.scraper.jobspy_scraper import JobSpyScraper


class IndeedScraper(JobSpyScraper):
    """Scrapes Indeed job listings via the JobSpy library."""

    _SITE = "indeed"
