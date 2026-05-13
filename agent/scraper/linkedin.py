"""
agent/scraper/linkedin.py — LinkedIn scraper via JobSpy.
"""

from agent.scraper.jobspy_scraper import JobSpyScraper


class LinkedInScraper(JobSpyScraper):
    """Scrapes LinkedIn job listings via the JobSpy library."""

    _SITE = "linkedin"
