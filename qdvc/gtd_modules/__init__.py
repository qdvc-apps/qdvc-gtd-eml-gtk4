"""
gtd_modules — shared building blocks for the GTD-over-EML tools.

Modules:
    config    — config.yml + workspace.yml loading, defaults, colours, folder/metadata constants
    emailutil — reading EML messages, headers, body text (base64/QP), refs
    naming    — slug + filename-convention generation
    ingest    — moving/renaming new files from 01-input into 02-triage
    metadata  — metadata.csv load/sync
    report    — colourised status report for gtd.py
    preview   — single-message human-readable preview for gtd_email_preview.py
"""
