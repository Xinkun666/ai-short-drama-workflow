# Book Timeline Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a DeepSeek-backed timeline agent that turns each book chapter into sortable historical event modules.

**Architecture:** The new agent runs after chapter splitting and chapter refinement. It reads each chapter's original Markdown plus reader JSON, writes per-chapter timeline JSON, merges the events into a book-level `timeline.json`, and exposes the result through Flask routes and a timeline page.

**Tech Stack:** Python dataclasses, Flask, DeepSeek chat completions, JSON/Markdown output, pytest.

---

### Task 1: Timeline Builder Contract

**Files:**
- Create: `drama_agents/timeline_builder.py`
- Test: `tests/test_timeline_builder.py`

- [x] Write failing tests for provider output normalization, artifact writing, confidence/evidence fields, and missing-provider skip behavior.
- [x] Run the timeline tests and verify they fail because the module does not exist.
- [x] Implement the builder, provider wrapper, prompt, normalization, sorting, JSON/Markdown writers, and payload helper.
- [x] Run the timeline tests and verify they pass.

### Task 2: Web API And Detail Integration

**Files:**
- Modify: `drama_agents/webapp/app.py`
- Modify: `drama_agents/webapp/templates/detail.html`
- Test: `tests/test_webapp.py`

- [x] Write failing web tests for `POST /api/materials/<record_id>/timeline`, timeline record metadata, and detail page timeline entry.
- [x] Implement route wiring and record updates.
- [x] Run web tests and verify they pass.

### Task 3: Timeline Reader Page

**Files:**
- Create: `drama_agents/webapp/templates/timeline.html`
- Modify: `drama_agents/webapp/static/styles.css`
- Test: `tests/test_webapp.py`

- [x] Write failing test for `GET /materials/<record_id>/timeline` rendering event cards with time, place, content, confidence, evidence note, source pages, and chapter links.
- [x] Implement the template and styles.
- [x] Run all tests.

### Task 4: Real Material Verification

**Files:**
- Output: `outputs/material_splits/<book_id>/timeline/`

- [ ] Build timeline for the existing Cambridge record.
- [ ] Verify generated JSON event count, status, and page render.
- [ ] Keep the local app running on port 8765.
