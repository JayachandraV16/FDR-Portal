"""Extraction of structured information from uploaded documents, for human review.

Everything here is assistance, not verification. The package must stay light to import: the sandbox
worker (worker.py) imports it inside a child process that deliberately has no application settings
or secrets, so nothing in this package may import app.config, app.db or any model.
"""
