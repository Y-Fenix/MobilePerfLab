# P0 Professional Workbench UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize MobilePerfLab into a professional first-screen testing workbench without changing the core sampling, reporting, or weak-network engines.

**Architecture:** Keep the existing Tkinter single-file application for this phase, but add pure UI contract helpers so layout, copy, and state priorities can be tested without opening the desktop app. The UI implementation should recompose current widgets into a top session bar, a step-based control sidebar, a central metrics/graph workspace, and a right diagnostics rail.

**Tech Stack:** Python 3, Tkinter/ttk, `unittest`, existing `mobileperflab.py` helpers and tests.

---

## File Structure

- Modify: `mobileperflab.py`
  - Add pure layout contract helpers near existing graph/layout helpers.
  - Update `App._configure_styles` for a more professional workbench palette and compact component hierarchy.
  - Update `App._build_ui`, `App._build_header`, `App._build_sidebar`, and `App._build_dashboard`.
  - Add a new right diagnostics rail builder that reuses existing collection link, quality event, weak-network, and log widgets.
- Modify: `tests/test_environment.py`
  - Add layout contract tests for shell regions, sidebar steps, top status chips, graph priority, and no-app-open policy.
- Reference: `docs/requirements/perfdog-gap-ui-weak-network-prd.md`
  - Sections 5.1, 5.2, 5.3, 5.4, and 5.5 are P0 scope.

## Task 1: Define Workbench Layout Contract

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing test**

Add this import to `tests/test_environment.py`:

```python
from mobileperflab import (
    WORKBENCH_SHELL_REGIONS,
    workbench_sidebar_steps,
    workbench_top_status_items,
)
```

Add these tests to `EnvironmentTest`:

```python
def test_workbench_shell_has_professional_four_region_layout(self) -> None:
    self.assertEqual(
        WORKBENCH_SHELL_REGIONS,
        ("top_session_bar", "left_control_rail", "central_observability", "right_diagnostics_rail"),
    )


def test_sidebar_steps_match_zero_learning_workflow(self) -> None:
    steps = workbench_sidebar_steps()

    self.assertEqual(
        [step["key"] for step in steps],
        ["connect_device", "select_app", "preflight", "sample"],
    )
    self.assertEqual(steps[0]["title"], "1 连接设备")
    self.assertEqual(steps[1]["title"], "2 选择应用")
    self.assertIn("开始采集", steps[3]["primary_action"])


def test_top_status_items_keep_session_context_visible(self) -> None:
    items = workbench_top_status_items()

    self.assertEqual(
        [item["key"] for item in items],
        ["device", "target_app", "capture", "quality", "weak_network"],
    )
    self.assertEqual(items[0]["label"], "设备")
    self.assertEqual(items[-1]["label"], "弱网")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_workbench_shell_has_professional_four_region_layout tests.test_environment.EnvironmentTest.test_sidebar_steps_match_zero_learning_workflow tests.test_environment.EnvironmentTest.test_top_status_items_keep_session_context_visible
```

Expected: FAIL with import errors because the helpers do not exist.

- [ ] **Step 3: Write minimal implementation**

Add near existing UI helper constants in `mobileperflab.py`:

```python
WORKBENCH_SHELL_REGIONS = (
    "top_session_bar",
    "left_control_rail",
    "central_observability",
    "right_diagnostics_rail",
)


def workbench_sidebar_steps() -> list[dict[str, str]]:
    return [
        {
            "key": "connect_device",
            "title": "1 连接设备",
            "detail": "刷新或进入演示模式，选择 Android/iOS 真机。",
            "primary_action": "刷新设备",
        },
        {
            "key": "select_app",
            "title": "2 选择应用",
            "detail": "自动识别当前前台应用，也可从应用列表选择。",
            "primary_action": "前台应用",
        },
        {
            "key": "preflight",
            "title": "3 采集自检",
            "detail": "检查前台、PID、UID、FPS 和网络来源。",
            "primary_action": "采集自检",
        },
        {
            "key": "sample",
            "title": "4 开始采集",
            "detail": "开始记录性能曲线，采集中可打点和导出报告。",
            "primary_action": "开始采集",
        },
    ]


def workbench_top_status_items() -> list[dict[str, str]]:
    return [
        {"key": "device", "label": "设备"},
        {"key": "target_app", "label": "目标应用"},
        {"key": "capture", "label": "采集"},
        {"key": "quality", "label": "质量"},
        {"key": "weak_network", "label": "弱网"},
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_workbench_shell_has_professional_four_region_layout tests.test_environment.EnvironmentTest.test_sidebar_steps_match_zero_learning_workflow tests.test_environment.EnvironmentTest.test_top_status_items_keep_session_context_visible
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Define professional workbench layout contract"
```

## Task 2: Prioritize First-Screen Metrics and Graphs

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing test**

Add this import to `tests/test_environment.py`:

```python
from mobileperflab import workbench_primary_metric_order
```

Add these tests to `EnvironmentTest`:

```python
def test_workbench_primary_metric_order_prioritizes_core_readability(self) -> None:
    self.assertEqual(
        workbench_primary_metric_order(),
        ["fps", "cpu_percent", "memory_mb", "rx_kbps", "tx_kbps", "jank_percent", "temperature_c", "power_w"],
    )


def test_metric_graph_layout_uses_workbench_priority_for_first_four_graphs(self) -> None:
    layout = metric_graph_layout()

    self.assertEqual([item["key"] for item in layout[:4]], ["fps", "cpu_percent", "memory_mb", "rx_kbps"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_workbench_primary_metric_order_prioritizes_core_readability tests.test_environment.EnvironmentTest.test_metric_graph_layout_uses_workbench_priority_for_first_four_graphs
```

Expected: FAIL because `workbench_primary_metric_order` does not exist, or because current graph order is FPS/Jank/CPU/Memory.

- [ ] **Step 3: Write minimal implementation**

Add helper:

```python
def workbench_primary_metric_order() -> list[str]:
    return ["fps", "cpu_percent", "memory_mb", "rx_kbps", "tx_kbps", "jank_percent", "temperature_c", "power_w"]
```

Update `metric_graph_layout()` so its definitions are ordered:

```python
metrics = [
    ("fps", "FPS", "", "#1F8FFF"),
    ("cpu_percent", "CPU", "%", "#FF8A34"),
    ("memory_mb", "Memory", "MB", "#4F46E5"),
    ("rx_kbps", "Down", "KB/s", "#16A34A"),
    ("tx_kbps", "Up", "KB/s", "#0D9488"),
    ("jank_percent", "Jank", "%", "#E8590C"),
    ("temperature_c", "Temp", "C", "#EF4444"),
    ("power_w", "Power", "W", "#0E9F6E"),
]
```

Keep row/column calculation unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_workbench_primary_metric_order_prioritizes_core_readability tests.test_environment.EnvironmentTest.test_metric_graph_layout_uses_workbench_priority_for_first_four_graphs
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Prioritize workbench core metric graphs"
```

## Task 3: Add Compact Status Chip Text Helpers

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing test**

Add this import to `tests/test_environment.py`:

```python
from mobileperflab import format_workbench_status_chip
```

Add these tests to `EnvironmentTest`:

```python
def test_workbench_status_chip_keeps_short_labels_for_empty_state(self) -> None:
    self.assertEqual(format_workbench_status_chip("设备", ""), "设备：未选择")
    self.assertEqual(format_workbench_status_chip("弱网", "弱网 OFF · 未启动"), "弱网：OFF")


def test_workbench_status_chip_truncates_long_operational_text(self) -> None:
    text = format_workbench_status_chip(
        "质量",
        "高可信 95.0% · 网络来源：目标 App per-UID · 窗口：稳定 · 趋势：平稳",
    )

    self.assertLessEqual(len(text), 28)
    self.assertEqual(text, "质量：高可信 95.0%")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_workbench_status_chip_keeps_short_labels_for_empty_state tests.test_environment.EnvironmentTest.test_workbench_status_chip_truncates_long_operational_text
```

Expected: FAIL because helper does not exist.

- [ ] **Step 3: Write minimal implementation**

Add helper:

```python
def format_workbench_status_chip(label: str, value: str, max_length: int = 28) -> str:
    clean_label = str(label or "").strip() or "状态"
    clean_value = str(value or "").strip()
    if not clean_value:
        clean_value = "未选择"
    if clean_value.startswith("弱网 OFF"):
        clean_value = "OFF"
    if " · " in clean_value:
        clean_value = clean_value.split(" · ", 1)[0]
    text = f"{clean_label}：{clean_value}"
    if len(text) <= max_length:
        return text
    return text[: max(max_length - 1, 1)] + "…"
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_workbench_status_chip_keeps_short_labels_for_empty_state tests.test_environment.EnvironmentTest.test_workbench_status_chip_truncates_long_operational_text
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Add compact workbench status chips"
```

## Task 4: Recompose the Main Shell Into Four Regions

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing structural test**

Add this test to `EnvironmentTest`:

```python
def test_app_build_ui_uses_four_workbench_regions(self) -> None:
    source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
    text = source.read_text(encoding="utf-8")

    self.assertIn("self._build_session_bar(root_frame)", text)
    self.assertIn("self._build_control_rail(shell)", text)
    self.assertIn("self._build_observability_workspace(shell)", text)
    self.assertIn("self._build_diagnostics_rail(shell)", text)
    self.assertNotIn("self._build_header(root_frame)", text)
    self.assertNotIn("self._build_sidebar(body)", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_app_build_ui_uses_four_workbench_regions
```

Expected: FAIL because current code still calls `_build_header` and `_build_sidebar`.

- [ ] **Step 3: Rename shell builders and keep compatibility wrappers**

Update `App._build_ui`:

```python
def _build_ui(self) -> None:
    root_frame = ttk.Frame(self.root, style="Root.TFrame")
    root_frame.pack(fill="both", expand=True)
    self._build_session_bar(root_frame)
    shell = ttk.Frame(root_frame, style="Root.TFrame", padding=(12, 12, 12, 12))
    shell.pack(fill="both", expand=True)
    shell.columnconfigure(0, minsize=320, weight=0)
    shell.columnconfigure(1, weight=1)
    shell.columnconfigure(2, minsize=360, weight=0)
    shell.rowconfigure(0, weight=1)
    self._build_control_rail(shell)
    self._build_observability_workspace(shell)
    self._build_diagnostics_rail(shell)
```

Rename method definitions without changing the existing method bodies in this task. The old `_build_header` body becomes `_build_session_bar`; the old `_build_sidebar` body becomes `_build_control_rail`.

```python
def _build_session_bar(self, master: tk.Widget) -> None:
    # Use the current _build_header body here.
    # The only required body change in Task 4 is the method name.
    header = ttk.Frame(master, style="Top.TFrame", padding=(18, 14, 18, 14))
    header.pack(fill="x")


def _build_control_rail(self, master: tk.Widget) -> None:
    # Use the current _build_sidebar body here.
    # The only required body change in Task 4 is the method name and sidebar grid column.
    sidebar = ttk.Frame(master, style="Sidebar.TFrame", padding=(14, 14))
    sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
```

Inside `_build_control_rail`, grid the sidebar at column 0:

```python
sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
```

Add compatibility wrappers after the renamed methods so older tests or call sites still work:

```python
def _build_header(self, master: tk.Widget) -> None:
    self._build_session_bar(master)


def _build_sidebar(self, master: tk.Widget) -> None:
    self._build_control_rail(master)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_app_build_ui_uses_four_workbench_regions
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Recompose app shell into workbench regions"
```

## Task 5: Build Session Bar With Compact Status Chips

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing structural test**

Add this test to `EnvironmentTest`:

```python
def test_session_bar_contains_status_chip_variables(self) -> None:
    source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
    text = source.read_text(encoding="utf-8")

    self.assertIn("self.session_chip_vars", text)
    self.assertIn("format_workbench_status_chip(\"设备\"", text)
    self.assertIn("format_workbench_status_chip(\"目标应用\"", text)
    self.assertIn("format_workbench_status_chip(\"采集\"", text)
    self.assertIn("format_workbench_status_chip(\"质量\"", text)
    self.assertIn("format_workbench_status_chip(\"弱网\"", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_session_bar_contains_status_chip_variables
```

Expected: FAIL because status chips are not implemented.

- [ ] **Step 3: Implement session chips**

In `App.__init__`, after existing `StringVar` declarations:

```python
self.session_chip_vars: dict[str, tk.StringVar] = {
    "device": tk.StringVar(value=format_workbench_status_chip("设备", "")),
    "target_app": tk.StringVar(value=format_workbench_status_chip("目标应用", "")),
    "capture": tk.StringVar(value=format_workbench_status_chip("采集", "待开始")),
    "quality": tk.StringVar(value=format_workbench_status_chip("质量", "等待数据")),
    "weak_network": tk.StringVar(value=format_workbench_status_chip("弱网", "弱网 OFF · 未启动")),
}
```

In `_build_session_bar`, between title group and action buttons:

```python
chip_row = ttk.Frame(header, style="Top.TFrame")
chip_row.pack(side="left", padx=(28, 0))
for item in workbench_top_status_items():
    key = item["key"]
    ttk.Label(chip_row, textvariable=self.session_chip_vars[key], style="StatusChip.TLabel").pack(side="left", padx=(0, 8))
```

Add style:

```python
style.configure("StatusChip.TLabel", background="#22324A", foreground="#EAF2FF", font=("Helvetica", 10, "bold"), padding=(9, 5))
```

Add helper method:

```python
def _refresh_session_chips(self) -> None:
    if not hasattr(self, "session_chip_vars"):
        return
    self.session_chip_vars["device"].set(format_workbench_status_chip("设备", self.device_var.get()))
    self.session_chip_vars["target_app"].set(format_workbench_status_chip("目标应用", self.app_var.get()))
    self.session_chip_vars["capture"].set(format_workbench_status_chip("采集", self.status_var.get()))
    self.session_chip_vars["quality"].set(format_workbench_status_chip("质量", self.quality_summary_var.get()))
    self.session_chip_vars["weak_network"].set(format_workbench_status_chip("弱网", self.weak_live_summary_var.get()))
```

Call `_refresh_session_chips()` at the end of `_handle_sample`, `start_sampling`, `stop_sampling`, `_on_device_selected`, `detect_foreground_app`, `start_weak_proxy`, `stop_weak_proxy`, `apply_android_proxy`, `clear_android_proxy`, and `refresh_android_proxy_status`.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_session_bar_contains_status_chip_variables tests.test_environment.EnvironmentTest.test_workbench_status_chip_keeps_short_labels_for_empty_state tests.test_environment.EnvironmentTest.test_workbench_status_chip_truncates_long_operational_text
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Add workbench session status chips"
```

## Task 6: Convert Sidebar Into Step-Based Control Rail

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing structural test**

Add this test to `EnvironmentTest`:

```python
def test_control_rail_renders_step_titles_without_long_help_text(self) -> None:
    source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
    text = source.read_text(encoding="utf-8")

    self.assertIn("for step in workbench_sidebar_steps()", text)
    self.assertIn("1 连接设备", text)
    self.assertIn("2 选择应用", text)
    self.assertIn("3 采集自检", text)
    self.assertIn("4 开始采集", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_control_rail_renders_step_titles_without_long_help_text
```

Expected: FAIL because the control rail does not render steps.

- [ ] **Step 3: Add step header rendering**

At the top of `_build_control_rail`, after creating `sidebar`, add:

```python
steps_panel = ttk.Frame(sidebar, style="Sidebar.TFrame")
steps_panel.grid(row=0, column=0, sticky="ew")
for index, step in enumerate(workbench_sidebar_steps()):
    row = ttk.Frame(steps_panel, style="Step.TFrame", padding=(10, 8))
    row.grid(row=index, column=0, sticky="ew", pady=(0 if index == 0 else 6, 0))
    ttk.Label(row, text=step["title"], style="StepTitle.TLabel").pack(anchor="w")
    ttk.Label(row, text=step["detail"], style="StepDetail.TLabel", wraplength=270).pack(anchor="w", pady=(2, 0))
```

Add styles:

```python
style.configure("Step.TFrame", background="#F8FAFC")
style.configure("StepTitle.TLabel", background="#F8FAFC", foreground="#18212F", font=("Helvetica", 11, "bold"))
style.configure("StepDetail.TLabel", background="#F8FAFC", foreground="#64748B", font=("Helvetica", 9))
```

Move existing sidebar content down by incrementing grid rows by 1:

```python
ttk.Label(sidebar, text="设备", style="SidebarTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 0))
filter_row.grid(row=2, column=0, sticky="ew", pady=(10, 8))
button_row.grid(row=3, column=0, sticky="ew", pady=(0, 8))
self.device_tree.grid(row=4, column=0, sticky="ew")
app_panel.grid(row=5, column=0, sticky="nsew", pady=(16, 0))
settings.grid(row=6, column=0, sticky="ew", pady=(16, 0))
sidebar.rowconfigure(5, weight=1)
```

- [ ] **Step 4: Run targeted test**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_control_rail_renders_step_titles_without_long_help_text tests.test_environment.EnvironmentTest.test_sidebar_steps_match_zero_learning_workflow
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Make control rail step based"
```

## Task 7: Split Central Observability From Right Diagnostics Rail

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing structural test**

Add this test to `EnvironmentTest`:

```python
def test_diagnostics_rail_owns_quality_events_weak_status_and_logs(self) -> None:
    source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
    text = source.read_text(encoding="utf-8")

    self.assertIn("def _build_diagnostics_rail", text)
    diagnostics_start = text.index("def _build_diagnostics_rail")
    diagnostics_end = text.index("def _build_metric_health_strip", diagnostics_start)
    diagnostics_body = text[diagnostics_start:diagnostics_end]

    self.assertIn("采集链路", diagnostics_body)
    self.assertIn("弱网状态", diagnostics_body)
    self.assertIn("质量事件", diagnostics_body)
    self.assertIn("日志", diagnostics_body)
    self.assertNotIn("bottom = ttk.Frame(main", diagnostics_body)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_diagnostics_rail_owns_quality_events_weak_status_and_logs
```

Expected: FAIL because `_build_diagnostics_rail` does not exist.

- [ ] **Step 3: Implement observability workspace wrapper**

Add method:

```python
def _build_observability_workspace(self, master: tk.Widget) -> None:
    workspace = ttk.Frame(master, style="Root.TFrame")
    workspace.grid(row=0, column=1, sticky="nsew")
    workspace.columnconfigure(0, weight=1)
    workspace.rowconfigure(0, weight=1)
    self.workspace_tabs = ttk.Notebook(workspace)
    self.workspace_tabs.grid(row=0, column=0, sticky="nsew")
    self.performance_tab = ttk.Frame(self.workspace_tabs, style="Root.TFrame")
    self.network_tab = ttk.Frame(self.workspace_tabs, style="Root.TFrame")
    self.workspace_tabs.add(self.performance_tab, text="性能采集")
    self.workspace_tabs.add(self.network_tab, text="弱网工具")
    self._build_dashboard(self.performance_tab, include_bottom=False)
    self._build_network_workspace(self.network_tab)
```

Change dashboard signature:

```python
def _build_dashboard(self, master: tk.Widget, include_bottom: bool = True) -> None:
```

Wrap current bottom event/log area:

```python
if include_bottom:
    self._build_bottom_event_log_area(main, row=6)
```

Move the existing bottom panel code into a new helper. The helper body is the current code block that starts at `bottom = ttk.Frame(main, style="Root.TFrame")` and ends after `self.log_text.configure(state="disabled")`; keep that body unchanged except that it uses `master` and `row`.

```python
def _build_bottom_event_log_area(self, master: tk.Widget, row: int) -> None:
    bottom = ttk.Frame(master, style="Root.TFrame")
    bottom.grid(row=row, column=0, sticky="nsew", pady=(12, 0))
    # Move the existing marker, quality event, and log panel construction here.
```

- [ ] **Step 4: Implement right diagnostics rail**

Add:

```python
def _build_diagnostics_rail(self, master: tk.Widget) -> None:
    rail = ttk.Frame(master, style="Panel.TFrame", padding=(12, 12))
    rail.grid(row=0, column=2, sticky="nsew", padx=(12, 0))
    rail.columnconfigure(0, weight=1)
    rail.rowconfigure(3, weight=1)

    ttk.Label(rail, text="采集链路", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
    self._build_collection_link_strip(rail, row=1)

    weak_panel = ttk.Frame(rail, style="Panel.TFrame", padding=(10, 10))
    weak_panel.grid(row=2, column=0, sticky="ew", pady=(12, 0))
    ttk.Label(weak_panel, text="弱网状态", style="PanelTitle.TLabel").pack(anchor="w")
    ttk.Label(weak_panel, textvariable=self.weak_readiness_var, style="GraphValue.TLabel").pack(anchor="w", pady=(6, 0))
    ttk.Label(weak_panel, textvariable=self.weak_live_summary_var, style="Muted.TLabel", wraplength=320).pack(anchor="w", pady=(4, 0))

    event_panel = ttk.Frame(rail, style="Panel.TFrame", padding=(10, 10))
    event_panel.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
    event_panel.rowconfigure(1, weight=1)
    event_panel.columnconfigure(0, weight=1)
    ttk.Label(event_panel, text="质量事件", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
    self.quality_event_tree = ttk.Treeview(event_panel, columns=("time", "kind", "detail"), show="headings", height=7)
    self.quality_event_tree.heading("time", text="时间")
    self.quality_event_tree.heading("kind", text="类型")
    self.quality_event_tree.heading("detail", text="说明")
    self.quality_event_tree.column("time", width=62, anchor="center", stretch=False)
    self.quality_event_tree.column("kind", width=88, anchor="center", stretch=False)
    self.quality_event_tree.column("detail", width=180, stretch=True)
    self.quality_event_tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

    log_panel = ttk.Frame(rail, style="Panel.TFrame", padding=(10, 10))
    log_panel.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
    log_panel.rowconfigure(1, weight=1)
    log_panel.columnconfigure(0, weight=1)
    ttk.Label(log_panel, text="日志", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
    self.log_text = tk.Text(log_panel, height=7, wrap="word", borderwidth=0, highlightthickness=0, bg="#FFFFFF", fg="#243044", font=("Menlo", 10))
    self.log_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
    self.log_text.configure(state="disabled")
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_diagnostics_rail_owns_quality_events_weak_status_and_logs tests.test_environment.EnvironmentTest.test_app_build_ui_uses_four_workbench_regions
```

Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Move diagnostics into right rail"
```

## Task 8: Polish Workbench Visual Density

**Files:**
- Modify: `mobileperflab.py`
- Modify: `tests/test_environment.py`

- [ ] **Step 1: Write the failing static style test**

Add this test to `EnvironmentTest`:

```python
def test_workbench_styles_use_professional_neutral_palette(self) -> None:
    source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
    text = source.read_text(encoding="utf-8")

    self.assertIn("#0F172A", text)
    self.assertIn("#F8FAFC", text)
    self.assertIn("StatusChip.TLabel", text)
    self.assertIn("StepTitle.TLabel", text)
    self.assertNotIn("background=\"#172235\"", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_workbench_styles_use_professional_neutral_palette
```

Expected: FAIL because current top background still uses `#172235`.

- [ ] **Step 3: Update styles**

In `_configure_styles`, replace the base palette:

```python
style.configure(".", font=("Helvetica", 11), background="#F8FAFC", foreground="#0F172A")
style.configure("Root.TFrame", background="#F8FAFC")
style.configure("Top.TFrame", background="#0F172A")
style.configure("TopTitle.TLabel", background="#0F172A", foreground="#FFFFFF", font=("Helvetica", 17, "bold"))
style.configure("TopSub.TLabel", background="#0F172A", foreground="#CBD5E1", font=("Helvetica", 10))
style.configure("Sidebar.TFrame", background="#FFFFFF")
style.configure("Panel.TFrame", background="#FFFFFF", relief="solid", borderwidth=1)
style.configure("PanelBody.TFrame", background="#FFFFFF")
style.configure("Card.TFrame", background="#FFFFFF", relief="solid", borderwidth=1)
style.configure("Status.TLabel", background="#0F172A", foreground="#E2E8F0", font=("Helvetica", 10))
```

Keep existing metric colors, but avoid adding large purple/blue gradients or decorative backgrounds.

- [ ] **Step 4: Run style test**

Run:

```bash
python3 -m unittest tests.test_environment.EnvironmentTest.test_workbench_styles_use_professional_neutral_palette
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mobileperflab.py tests/test_environment.py
git commit -m "Polish professional workbench styling"
```

## Task 9: Final Verification Without Opening the App

**Files:**
- Verify only.

- [ ] **Step 1: Confirm app is not running**

Run:

```bash
pgrep -fl MobilePerfLab || true
```

Expected: no output.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python3 -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 3: Compile Python file**

Run:

```bash
python3 -m py_compile mobileperflab.py
```

Expected: exit 0.

- [ ] **Step 4: Check whitespace**

Run:

```bash
git diff --check
```

Expected: exit 0.

- [ ] **Step 5: Confirm git state**

Run:

```bash
git status --short --branch
git rev-list --left-right --count origin/main...HEAD
```

Expected: working tree clean. If GitHub push still fails, report local ahead count exactly.

- [ ] **Step 6: Push if network allows**

Run:

```bash
git push origin main
```

Expected: push succeeds. If it fails with network timeout or HTTP2 error, keep local commits and report failure.
