# Runtime UI Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MobilePerfLab reliably runnable for user inspection, with fullscreen startup, row-based graph scrolling, complete metric graph presence, and a repeatable self-test trail.

**Architecture:** Keep the existing single-file Tk app structure for this phase and add pure helper functions where UI behavior can be unit-tested without opening real windows. UI code in `mobileperflab.py` remains the integration point; tests live in `tests/test_environment.py` and existing report/metric tests.

**Tech Stack:** Python 3, Tkinter/ttk, `unittest`, existing `mobileperflab.py` helpers.

---

## File Structure

- Modify: `mobileperflab.py`
  - `App._open_fullscreen_window`
  - graph layout helpers near `graph_scroll_row_step`
  - graph area construction inside `App._build_dashboard`
  - launch/runtime helpers if needed
- Modify: `tests/test_environment.py`
  - fullscreen fallback tests
  - graph row visibility and scroll increment tests
  - metric graph presence tests
- Reference: `docs/requirements/performance-weak-network-prd.md`
  - Sections 9, 11, and 12 define UI/runtime acceptance.

---

### Task 1: Verify Fullscreen Startup Contract

**Files:**
- Modify: `tests/test_environment.py`
- Modify: `mobileperflab.py`

- [ ] **Step 1: Write the failing test**

Add a fake root and test that fullscreen startup tries native zoom first, then falls back to screen-sized geometry.

```python
class FakeFullscreenRoot:
    def __init__(self, fail_state: bool = False, fail_attributes: bool = False) -> None:
        self.fail_state = fail_state
        self.fail_attributes = fail_attributes
        self.calls: list[tuple[str, object]] = []

    def state(self, value: str) -> None:
        self.calls.append(("state", value))
        if self.fail_state:
            raise RuntimeError("state unsupported")

    def attributes(self, key: str, value: bool) -> None:
        self.calls.append(("attributes", (key, value)))
        if self.fail_attributes:
            raise RuntimeError("attributes unsupported")

    def winfo_screenwidth(self) -> int:
        return 1440

    def winfo_screenheight(self) -> int:
        return 900

    def geometry(self, value: str) -> None:
        self.calls.append(("geometry", value))


def test_fullscreen_prefers_zoomed_state(self) -> None:
    root = FakeFullscreenRoot()

    App._open_fullscreen_window_for_root(root)

    self.assertEqual(root.calls, [("state", "zoomed")])


def test_fullscreen_falls_back_to_screen_geometry(self) -> None:
    root = FakeFullscreenRoot(fail_state=True, fail_attributes=True)

    App._open_fullscreen_window_for_root(root)

    self.assertEqual(
        root.calls,
        [
            ("state", "zoomed"),
            ("attributes", ("-zoomed", True)),
            ("geometry", "1440x900+0+0"),
        ],
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_fullscreen_prefers_zoomed_state tests.test_environment.EnvironmentTest.test_fullscreen_falls_back_to_screen_geometry
```

Expected: FAIL because `App._open_fullscreen_window_for_root` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `mobileperflab.py`, refactor fullscreen logic to a static helper.

```python
    @staticmethod
    def _open_fullscreen_window_for_root(root: tk.Tk) -> None:
        try:
            root.state("zoomed")
            return
        except Exception:
            pass
        try:
            root.attributes("-zoomed", True)
            return
        except Exception:
            pass
        width = root.winfo_screenwidth()
        height = root.winfo_screenheight()
        root.geometry(f"{width}x{height}+0+0")

    def _open_fullscreen_window(self) -> None:
        self._open_fullscreen_window_for_root(self.root)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_fullscreen_prefers_zoomed_state tests.test_environment.EnvironmentTest.test_fullscreen_falls_back_to_screen_geometry
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Verify fullscreen startup fallback"
```

---

### Task 2: Lock Graph Area to Four Visible Charts

**Files:**
- Modify: `tests/test_environment.py`
- Modify: `mobileperflab.py`

- [ ] **Step 1: Write the failing test**

Add or update tests so the graph area always shows two rows by default, which means four graphs with a 2-column layout.

```python
def test_graph_view_rows_defaults_to_two_visible_rows(self) -> None:
    self.assertEqual(graph_visible_rows_for_height(720), 2)
    self.assertEqual(graph_visible_rows_for_height(900), 2)
    self.assertEqual(graph_visible_rows_for_height(1400), 2)


def test_graph_view_height_shows_exactly_two_rows_plus_scrollbar(self) -> None:
    self.assertEqual(format_graph_view_height(2, 176, 10, 22), 384)
```

- [ ] **Step 2: Run test to verify it fails if current behavior allows more than two rows**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_graph_view_rows_defaults_to_two_visible_rows tests.test_environment.EnvironmentTest.test_graph_view_height_shows_exactly_two_rows_plus_scrollbar
```

Expected: FAIL if `graph_visible_rows_for_height` returns more than 2 for tall screens. If it already passes, keep the tests as contract coverage and continue.

- [ ] **Step 3: Write minimal implementation**

Make `graph_visible_rows_for_height` return 2 for the main graph area.

```python
def graph_visible_rows_for_height(_screen_height: int) -> int:
    return 2
```

Keep `format_graph_view_height` unchanged if it already computes:

```python
def format_graph_view_height(visible_rows: int, row_height: int, row_gap: int, scrollbar_height: int) -> int:
    rows = max(1, int(visible_rows))
    return row_height * rows + row_gap * max(rows - 1, 0) + scrollbar_height
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_graph_view_rows_defaults_to_two_visible_rows tests.test_environment.EnvironmentTest.test_graph_view_height_shows_exactly_two_rows_plus_scrollbar
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Show four performance charts by default"
```

---

### Task 3: Preserve One Mouse Wheel Notch Equals One Graph Row

**Files:**
- Modify: `tests/test_environment.py`
- Modify: `mobileperflab.py`

- [ ] **Step 1: Write the failing test**

Strengthen the existing row-step test to cover macOS multi-notch deltas and Linux buttons.

```python
def test_mousewheel_scrolls_one_row_per_notch(self) -> None:
    self.assertEqual(graph_scroll_row_step(1), 1)
    self.assertEqual(graph_scroll_row_step(3), 1)
    self.assertEqual(graph_scroll_row_step(-1), -1)
    self.assertEqual(graph_scroll_row_step(-4), -1)
    self.assertEqual(graph_scroll_row_step(0), 0)
```

- [ ] **Step 2: Run test to verify it fails if magnitude is used directly**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_mousewheel_scrolls_one_row_per_notch
```

Expected: FAIL if `graph_scroll_row_step(3)` returns 3. If it already passes, keep it as contract coverage and continue.

- [ ] **Step 3: Write minimal implementation**

Ensure helper normalizes to one row.

```python
def graph_scroll_row_step(wheel_units: int) -> int:
    if wheel_units > 0:
        return 1
    if wheel_units < 0:
        return -1
    return 0
```

In `_on_graph_mousewheel`, continue calling:

```python
row_step = graph_scroll_row_step(units)
self.graph_canvas.yview_scroll(row_step, "units")
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_mousewheel_scrolls_one_row_per_notch
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Normalize graph wheel scrolling by row"
```

---

### Task 4: Assert All Required Metric Graphs Exist

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing test**

Add a pure helper that returns the graph layout spec and test it contains all PRD-required graphs in the intended order.

```python
def test_metric_graph_layout_contains_all_required_graphs(self) -> None:
    layout = metric_graph_layout()
    keys = [item["key"] for item in layout]

    self.assertEqual(
        keys,
        [
            "fps",
            "jank_percent",
            "cpu_percent",
            "memory_mb",
            "temperature_c",
            "power_w",
            "rx_kbps",
            "tx_kbps",
        ],
    )
    self.assertEqual([(item["row"], item["col"]) for item in layout], [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_metric_graph_layout_contains_all_required_graphs
```

Expected: FAIL because `metric_graph_layout` does not exist.

- [ ] **Step 3: Write minimal implementation**

In `mobileperflab.py`, add a helper near graph helpers.

```python
def metric_graph_layout() -> list[dict[str, object]]:
    return [
        {"key": "fps", "title": "帧率", "unit": "FPS", "color": "#1F8FFF", "row": 0, "col": 0},
        {"key": "jank_percent", "title": "Jank", "unit": "%", "color": "#E8590C", "row": 0, "col": 1},
        {"key": "cpu_percent", "title": "CPU 占用", "unit": "%", "color": "#FF8A34", "row": 1, "col": 0},
        {"key": "memory_mb", "title": "内存", "unit": "MB", "color": "#4F46E5", "row": 1, "col": 1},
        {"key": "temperature_c", "title": "温度", "unit": "C", "color": "#EF4444", "row": 2, "col": 0},
        {"key": "power_w", "title": "功耗", "unit": "W", "color": "#0E9F6E", "row": 2, "col": 1},
        {"key": "rx_kbps", "title": "网络下行", "unit": "KB/s", "color": "#16A34A", "row": 3, "col": 0},
        {"key": "tx_kbps", "title": "网络上行", "unit": "KB/s", "color": "#0D9488", "row": 3, "col": 1},
    ]
```

Update `App._build_dashboard` to create graphs from this helper.

```python
self.graphs = {
    str(item["key"]): GraphPanel(
        graphs,
        str(item["title"]),
        str(item["key"]),
        str(item["unit"]),
        str(item["color"]),
    )
    for item in metric_graph_layout()
}
for item in metric_graph_layout():
    key = str(item["key"])
    row = int(item["row"])
    col = int(item["col"])
    self.graphs[key].grid(row=row, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0), pady=(0 if row == 0 else 10, 0))
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_metric_graph_layout_contains_all_required_graphs
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Define required performance graph layout"
```

---

### Task 5: Add Runtime Smoke Script for User Inspection

**Files:**
- Modify: `tests/test_packaging.py`
- Modify: `README.md`
- Modify: `一键启动.command` if launch behavior needs adjustment

- [ ] **Step 1: Write the failing test**

Add a packaging test that checks the startup command uses the project script and keeps the path absolute.

```python
def test_startup_command_launches_mobileperflab_script(self) -> None:
    text = Path("一键启动.command").read_text(encoding="utf-8")

    self.assertIn('SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"', text)
    self.assertIn('"$PYTHON_BIN" "$SCRIPT_DIR/mobileperflab.py"', text)
```

- [ ] **Step 2: Run test to verify it fails if startup script drifts**

Run:

```bash
python3 -m unittest tests.test_packaging.PackagingTest.test_startup_command_launches_mobileperflab_script
```

Expected: OK if current script already matches. Keep as regression coverage.

- [ ] **Step 3: Update README with manual runtime check**

Add a short section under “运行”:

```markdown
### 本地运行自检

```bash
python3 -m py_compile mobileperflab.py
python3 -m unittest discover -s tests
python3 mobileperflab.py
```

窗口应全屏打开；无真机时点击“演示模式”可查看指标卡、4 个默认图表、向下滚动查看更多图表、弱网工具和报告导出流程。
```
```

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m unittest tests.test_packaging
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_packaging.py 一键启动.command
git commit -m "Document runtime smoke check"
```

---

### Task 6: Final Verification and User Launch

**Files:**
- No required file changes unless verification exposes a defect.

- [ ] **Step 1: Run full verification**

Run:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile mobileperflab.py
git diff --check
```

Expected:

- `Ran ... tests ... OK`
- `py_compile` exits 0
- `git diff --check` exits 0

- [ ] **Step 2: Launch app for inspection**

Run:

```bash
python3 mobileperflab.py
```

Expected:

- Tk window opens.
- Window attempts fullscreen.
- User can enter Demo mode.
- Performance page shows 8 metric cards and 4 visible charts.
- Mouse wheel over chart area moves exactly one graph row.
- Temperature, Power, Down, and Up charts are reachable by scrolling.
- Weak network tab opens and shows presets, controls, diagnostics, and real traffic chart.

- [ ] **Step 3: Commit any verification fix**

Only if Step 1 or Step 2 required a code/doc fix:

```bash
git add mobileperflab.py tests README.md
git commit -m "Fix runtime UI baseline verification"
```

- [ ] **Step 4: Push if network allows**

Run:

```bash
git push origin main
```

Expected: push succeeds. If GitHub HTTPS times out, report that local commits are ready and branch is ahead.

