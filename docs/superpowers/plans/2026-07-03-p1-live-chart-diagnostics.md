# P1 Live Chart Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add concise real-time chart diagnostic summaries so each performance graph explains whether the visible data is healthy, limited, fallback, or unavailable.

**Architecture:** Keep the existing single-file Tkinter app. Add pure helper functions for chart diagnostic copy and bind them into `GraphPanel`, so the behavior can be unit-tested without opening the desktop app.

**Tech Stack:** Python 3, Tkinter/ttk, `unittest`, existing `mobileperflab.py` helpers and tests.

---

### Task 1: Chart Diagnostic Summary Helper

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write failing tests**

Add tests for `graph_diagnostic_summary_text` covering limited FPS, fallback samples, and weak-network bypass wording.

- [ ] **Step 2: Run test to verify RED**

```bash
python3 -m unittest tests.test_environment.GraphScrollBehaviorTest.test_graph_diagnostic_summary_text_explains_limited_and_fallback_points
```

Expected: fails because `graph_diagnostic_summary_text` is not defined.

- [ ] **Step 3: Implement helper**

Add a pure helper near existing graph helpers. It counts quality tags, folds known health details into short status labels, and returns one concise line.

- [ ] **Step 4: Run focused tests**

```bash
python3 -m unittest tests.test_environment.GraphScrollBehaviorTest
```

Expected: OK.

### Task 2: Bind Diagnostics Into GraphPanel

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write failing tests**

Add source-contract tests confirming `GraphPanel` owns `diagnostic_var`, calls `graph_diagnostic_summary_text`, and accepts network weak-summary details.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_environment.QualityModeLabelTest.test_network_graph_diagnostic_detail_includes_weak_network_bypass_summary
```

Expected: fails until network graph diagnostics include weak-network summary text.

- [ ] **Step 3: Implement binding**

Add `diagnostic_var` to `GraphPanel`, refresh it on append/reset, and add `set_diagnostic_detail()` so `App._set_metric_card()` can pass metric health details into the chart.

- [ ] **Step 4: Run focused tests**

```bash
python3 -m unittest tests.test_environment.GraphScrollBehaviorTest tests.test_environment.QualityModeLabelTest
```

Expected: OK.

### Task 3: Verification and Sync

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`
- Add: `docs/superpowers/plans/2026-07-03-p1-live-chart-diagnostics.md`

- [ ] **Step 1: Run full verification**

```bash
python3 -m unittest discover -s tests
python3 -m py_compile mobileperflab.py
git diff --check
```

Expected: all pass.

- [ ] **Step 2: Commit and push**

```bash
git add mobileperflab.py tests/test_environment.py docs/superpowers/plans/2026-07-03-p1-live-chart-diagnostics.md
git commit -m "Add live chart diagnostics"
git push origin main
```

- [ ] **Step 3: Verify remote SHA**

```bash
git ls-remote --heads origin main
gh api repos/Y-Fenix/MobilePerfLab/git/ref/heads/main --jq '.object.sha'
```
