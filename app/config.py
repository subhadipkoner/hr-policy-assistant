"""Branding and document settings.

Everything company-specific lives here so the app can be pointed at another
organisation's handbook by editing one file (or setting the env vars).
"""
from __future__ import annotations

import os

# Shown in the sidebar, the page title and the answer footer.
COMPANY_NAME = os.getenv("COMPANY_NAME", "SKonnect Industry")
COMPANY_TAGLINE = os.getenv("COMPANY_TAGLINE", "Human Resources")

# The policy PDF is a template that still carries its originating name. These
# are rewritten to COMPANY_NAME as the text is extracted, so answers read as
# the company's own. Set REWRITE_COMPANY_NAME=0 to quote the PDF verbatim.
SOURCE_COMPANY_ALIASES = [
    "XYZ Technology Company",
    "XYZ Technology",
    "XYZ Tech",
    "XYZ",
]
REWRITE_COMPANY_NAME = os.getenv("REWRITE_COMPANY_NAME", "1") != "0"

# Fallback questions, used when the handbook has no recognised section to
# draw suggestions from.
DEFAULT_SUGGESTIONS = [
    "How much paid time off do I get?",
    "What is the remote work policy?",
    "How do I claim travel expenses?",
    "What is the notice period when I resign?",
    "How does the performance appraisal work?",
    "What are the working hours and break rules?",
]
