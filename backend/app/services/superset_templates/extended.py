"""Annexe catalogue viz §7 — 25 new hand-written template functions (plus 5 more that reuse
`echarts_timeseries` unchanged, registered directly in EXTENDED_TEMPLATES below), extending
the original 5-type library to the ~35-type catalogue. No browser access this session, so
every one of these was verified the same way: a direct `POST /api/v1/chart/` create with this
exact params shape against the real live Superset instance, fetched back via `GET`, then
deleted — never guessed. Style values (colors, formats, row limits) are copied straight from
that captured, accepted payload; only the data-carrying fields are parameterized.

Each viz_type's Superset params field NAMES and shapes (scalar vs list, "metric" vs
"metrics") are independent of the semantic slot names in chart_catalog.json — the mapping
between the two lives only in TEMPLATE_REGISTRY's lambdas in __init__.py, never here.
"""
from app.services.superset_templates import MetricSpec, _metric_dict


def big_number(datasource: str, metric: MetricSpec, x_axis: str) -> dict:
    """A single KPI with a trend sparkline over time."""
    return {
        "datasource": datasource,
        "viz_type": "big_number",
        "metric": _metric_dict(metric, "kpi"),
        "x_axis": x_axis,
        "adhoc_filters": [],
        "header_font_size": 0.4,
        "subtitle_font_size": 0.15,
        "y_axis_format": "SMART_NUMBER",
        "time_format": "smart_date",
        "extra_form_data": {},
    }


def pop_kpi(datasource: str, metric: MetricSpec, x_axis: str) -> dict:
    """A KPI compared against the same measure one period earlier (period-over-period)."""
    return {
        "datasource": datasource,
        "viz_type": "pop_kpi",
        "metrics": [_metric_dict(metric, "kpi")],
        "x_axis": x_axis,
        "time_comparison": "y",
        "adhoc_filters": [],
        "adhoc_custom": [],
        "row_limit": 10000,
        "y_axis_format": "SMART_NUMBER",
        "extra_form_data": {},
    }


def funnel(datasource: str, groupby: list[str], metric: MetricSpec) -> dict:
    """A measure across ordered stages of a dimension."""
    return {
        "datasource": datasource,
        "viz_type": "funnel",
        "metric": _metric_dict(metric, "fn"),
        "groupby": groupby,
        "adhoc_filters": [],
        "row_limit": 10,
        "color_scheme": "supersetColors",
        "number_format": "SMART_NUMBER",
        "extra_form_data": {},
    }


def gauge_chart(datasource: str, metric: MetricSpec, groupby: list[str]) -> dict:
    """A single aggregated measure rendered as a gauge, optionally split by a dimension."""
    return {
        "datasource": datasource,
        "viz_type": "gauge_chart",
        "metric": _metric_dict(metric, "gg"),
        "groupby": groupby,
        "adhoc_filters": [],
        "row_limit": 10,
        "min_val": 0,
        "max_val": 100,
        "value_formatter": "{value}",
        "font_size": 20,
        "number_format": "SMART_NUMBER",
        "extra_form_data": {},
    }


def treemap_v2(datasource: str, metric: MetricSpec, groupby: list[str]) -> dict:
    """A measure broken down over a (possibly nested) dimension hierarchy."""
    return {
        "datasource": datasource,
        "viz_type": "treemap_v2",
        "metric": _metric_dict(metric, "tm"),
        "groupby": groupby,
        "adhoc_filters": [],
        "row_limit": 100,
        "color_scheme": "supersetColors",
        "show_labels": True,
        "show_upper_labels": True,
        "number_format": "SMART_NUMBER",
        "date_format": "smart_date",
        "extra_form_data": {},
    }


def ag_grid(datasource: str, groupby: list[str], metrics: list[MetricSpec]) -> dict:
    """Interactive client-side sortable/filterable grid — the ag_grid sibling of `table`."""
    return {
        "datasource": datasource,
        "viz_type": "ag_grid",
        "query_mode": "aggregate",
        "groupby": groupby,
        "metrics": [_metric_dict(m, f"c{i}") for i, m in enumerate(metrics)],
        "all_columns": [],
        "adhoc_filters": [],
        "order_by_cols": [],
        "row_limit": 1000,
        "extra_form_data": {},
    }


def pivot_table_v2(datasource: str, metrics: list[MetricSpec], groupby_rows: list[str], groupby_columns: list[str]) -> dict:
    """Measures broken down across row and column dimensions in a cross-tab."""
    return {
        "datasource": datasource,
        "viz_type": "pivot_table_v2",
        "groupbyRows": groupby_rows,
        "groupbyColumns": groupby_columns,
        "metrics": [_metric_dict(m, f"pv{i}") for i, m in enumerate(metrics)],
        "adhoc_filters": [],
        "row_limit": 10000,
        "aggregateFunction": "Sum",
        "valueFormat": "SMART_NUMBER",
        "colOrder": "key_a_to_z",
        "rowOrder": "key_a_to_z",
        "transposePivot": False,
        "combineMetric": False,
        "rowSubtotalPosition": False,
        "colSubtotalPosition": False,
        "colTotals": False,
        "rowTotals": False,
        "extra_form_data": {},
    }


def sunburst_v2(datasource: str, columns: list[str], metric: MetricSpec) -> dict:
    """Nested-ring hierarchical breakdown of one measure — column order is the hierarchy."""
    return {
        "datasource": datasource,
        "viz_type": "sunburst_v2",
        "columns": columns,
        "metric": _metric_dict(metric, "sb"),
        "adhoc_filters": [],
        "row_limit": 100,
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def partition(datasource: str, groupby: list[str], metrics: list[MetricSpec]) -> dict:
    """Hierarchical partition diagram — measures broken down across dimension levels."""
    return {
        "datasource": datasource,
        "viz_type": "partition",
        "groupby": groupby,
        "metrics": [_metric_dict(m, f"pt{i}") for i, m in enumerate(metrics)],
        "adhoc_filters": [],
        "row_limit": 10000,
        "color_scheme": "supersetColors",
        "date_time_format": "smart_date",
        "partition_limit": "5",
        "partition_threshold": "0.05",
        "extra_form_data": {},
    }


def word_cloud(datasource: str, series: str, metric: MetricSpec) -> dict:
    """A measure's weight per value of a (typically text) dimension, rendered as a word cloud."""
    return {
        "datasource": datasource,
        "viz_type": "word_cloud",
        "series": series,
        "metric": _metric_dict(metric, "wc"),
        "adhoc_filters": [],
        "row_limit": 100,
        "size_from": 10,
        "size_to": 70,
        "rotation": "square",
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def cal_heatmap(datasource: str, metric: MetricSpec, x_axis: str) -> dict:
    """Calendar heatmap — intensity of a measure per day."""
    return {
        "datasource": datasource,
        "viz_type": "cal_heatmap",
        "metrics": [_metric_dict(metric, "ch")],
        "granularity_sqla": x_axis,
        "domain_granularity": "month",
        "subdomain_granularity": "day",
        "adhoc_filters": [],
        "row_limit": 10000,
        "linear_color_scheme": "supersetColors",
        "steps": 10,
        "cell_size": 10,
        "cell_padding": 2,
        "cell_radius": 0,
        "extra_form_data": {},
    }


def time_pivot(datasource: str, metric: MetricSpec, x_axis: str) -> dict:
    """A measure pivoted across superposed time periods."""
    return {
        "datasource": datasource,
        "viz_type": "time_pivot",
        "metric": _metric_dict(metric, "tp"),
        "granularity_sqla": x_axis,
        "adhoc_filters": [],
        "row_limit": 10000,
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def time_table(datasource: str, metrics: list[MetricSpec], x_axis: str, groupby: list[str]) -> dict:
    """Tabular measures over time, one sparkline row per dimension value."""
    return {
        "datasource": datasource,
        "viz_type": "time_table",
        "metrics": [_metric_dict(m, f"tt{i}") for i, m in enumerate(metrics)],
        "granularity_sqla": x_axis,
        "groupby": groupby,
        "adhoc_filters": [],
        "row_limit": 10000,
        "column_collection": [],
        "extra_form_data": {},
    }


def bubble_v2(datasource: str, entity: str, x: MetricSpec, y: MetricSpec, size: MetricSpec, series: str | None) -> dict:
    """Correlation of 3 measures (x, y, bubble size) across a dimension, optionally split by a second series dimension."""
    params = {
        "datasource": datasource,
        "viz_type": "bubble_v2",
        "entity": entity,
        "x": _metric_dict(x, "x"),
        "y": _metric_dict(y, "y"),
        "size": _metric_dict(size, "z"),
        "adhoc_filters": [],
        "row_limit": 10000,
        "color_scheme": "supersetColors",
        "max_bubble_size": "25",
        "x_axis_format": "SMART_NUMBER",
        "y_axis_format": "SMART_NUMBER",
        "extra_form_data": {},
    }
    if series:
        params["series"] = series
    return params


def radar(datasource: str, groupby: list[str], metrics: list[MetricSpec]) -> dict:
    """Multi-measure (>=3) profile of a dimension, one axis per measure."""
    return {
        "datasource": datasource,
        "viz_type": "radar",
        "groupby": groupby,
        "metrics": [_metric_dict(m, f"r{i}") for i, m in enumerate(metrics)],
        "adhoc_filters": [],
        "row_limit": 10,
        "color_scheme": "supersetColors",
        "number_format": "SMART_NUMBER",
        "extra_form_data": {},
    }


def box_plot(datasource: str, groupby: list[str], metrics: list[MetricSpec]) -> dict:
    """Distribution (quartiles/outliers) of one or more measures per category — no
    aggregation on the metrics themselves, the box plot computes its own statistics."""
    return {
        "datasource": datasource,
        "viz_type": "box_plot",
        "groupby": groupby,
        "metrics": [_metric_dict(m, f"bp{i}") for i, m in enumerate(metrics)],
        "whiskerOptions": "Tukey",
        "adhoc_filters": [],
        "row_limit": 100,
        "color_scheme": "supersetColors",
        "number_format": "SMART_NUMBER",
        "extra_form_data": {},
    }


def histogram_v2(datasource: str, column: str, groupby: list[str]) -> dict:
    """Distribution of a raw numeric column's values, binned — takes a bare column, never
    an aggregated metric (the histogram computes its own bins)."""
    return {
        "datasource": datasource,
        "viz_type": "histogram_v2",
        "column": column,
        "groupby": groupby,
        "adhoc_filters": [],
        "row_limit": 10000,
        "bins": 10,
        "color_scheme": "supersetColors",
        "normalize": False,
        "cumulative": False,
        "extra_form_data": {},
    }


def waterfall(datasource: str, x_axis: str, metric: MetricSpec, groupby: list[str]) -> dict:
    """Positive/negative contributions to a total across an ordered category or period axis."""
    return {
        "datasource": datasource,
        "viz_type": "waterfall",
        "x_axis": x_axis,
        "metric": _metric_dict(metric, "wf"),
        "groupby": groupby,
        "adhoc_filters": [],
        "row_limit": 100,
        "increase_color": {"r": 90, "g": 193, "b": 137, "a": 1},
        "decrease_color": {"r": 224, "g": 67, "b": 85, "a": 1},
        "total_color": {"r": 102, "g": 102, "b": 102, "a": 1},
        "extra_form_data": {},
    }


def mixed_timeseries(datasource: str, x_axis: str, metrics_a: list[MetricSpec], metrics_b: list[MetricSpec]) -> dict:
    """Two independent measure sets superposed on the same time axis (e.g. bars + line)."""
    return {
        "datasource": datasource,
        "viz_type": "mixed_timeseries",
        "x_axis": x_axis,
        "metrics": [_metric_dict(m, f"a{i}") for i, m in enumerate(metrics_a)],
        "groupby": [],
        "metrics_b": [_metric_dict(m, f"b{i}") for i, m in enumerate(metrics_b)],
        "groupby_b": [],
        "adhoc_filters": [],
        "adhoc_filters_b": [],
        "row_limit": 10000,
        "truncate_metric": True,
        "truncate_metric_b": True,
        "y_axis_format": "SMART_NUMBER",
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def rose(datasource: str, groupby: list[str], metrics: list[MetricSpec], x_axis: str) -> dict:
    """Nightingale/polar-area diagram of a measure over time, optionally split by a dimension."""
    return {
        "datasource": datasource,
        "viz_type": "rose",
        "groupby": groupby,
        "metrics": [_metric_dict(m, f"rs{i}") for i, m in enumerate(metrics)],
        "granularity_sqla": x_axis,
        "adhoc_filters": [],
        "row_limit": 10000,
        "color_scheme": "supersetColors",
        "date_time_format": "smart_date",
        "number_format": "SMART_NUMBER",
        "extra_form_data": {},
    }


def para(datasource: str, series: str, metrics: list[MetricSpec]) -> dict:
    """Parallel coordinates — comparison of multiple measures (>=2) across a dimension."""
    return {
        "datasource": datasource,
        "viz_type": "para",
        "series": series,
        "metrics": [_metric_dict(m, f"p{i}") for i, m in enumerate(metrics)],
        "adhoc_filters": [],
        "row_limit": 100,
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def paired_ttest(datasource: str, groupby: list[str], metrics: list[MetricSpec]) -> dict:
    """Paired t-test — statistical comparison of measures between groups."""
    return {
        "datasource": datasource,
        "viz_type": "paired_ttest",
        "groupby": groupby,
        "metrics": [_metric_dict(m, f"pt{i}") for i, m in enumerate(metrics)],
        "adhoc_filters": [],
        "row_limit": 10000,
        "extra_form_data": {},
    }


def horizon(datasource: str, series: str, metric: MetricSpec, x_axis: str) -> dict:
    """Horizon chart — many compacted time series, one band per dimension value."""
    return {
        "datasource": datasource,
        "viz_type": "horizon",
        "series": series,
        "metrics": [_metric_dict(metric, "h0")],
        "granularity_sqla": x_axis,
        "adhoc_filters": [],
        "row_limit": 10000,
        "horizon_color_scale": "series",
        "series_height": "25",
        "extra_form_data": {},
    }


def gantt_chart(datasource: str, x_axis: str, end_time: str, series: str) -> dict:
    """Gantt chart — tasks (one row per dimension value) on a start/end time axis."""
    return {
        "datasource": datasource,
        "viz_type": "gantt_chart",
        "start_time": x_axis,
        "end_time": end_time,
        "series": series,
        "adhoc_filters": [],
        "row_limit": 10000,
        "color_scheme": "supersetColors",
        "extra_form_data": {},
    }


def bullet(datasource: str, metric: MetricSpec, groupby: list[str]) -> dict:
    """Bullet chart — a measure against a fixed target range. The target itself is a style
    constant (frozen here), never deducible from a table profile."""
    return {
        "datasource": datasource,
        "viz_type": "bullet",
        "metric": _metric_dict(metric, "bl"),
        "groupby": groupby,
        "adhoc_filters": [],
        "ranges": "0,50,100",
        "range_labels": "Faible,Moyen,Fort",
        "markers": "60",
        "marker_labels": "Objectif",
        "marker_lines": "",
        "marker_lines_labels": "",
        "extra_form_data": {},
    }


# echarts_area / _smooth / _step / _timeseries (generic fallback) / _scatter are structurally
# identical to echarts_timeseries_line/bar (same modern ECharts control family, confirmed via
# the same create/fetch-back capture) — reuse the existing function, just a different
# viz_type string; no new function needed, only registry entries below.
from app.services.superset_templates import echarts_timeseries  # noqa: E402

EXTENDED_TEMPLATES = {
    "big_number": lambda datasource, slots: big_number(datasource, slots["metric"][0], slots["x_axis"][0]),
    "pop_kpi": lambda datasource, slots: pop_kpi(datasource, slots["metric"][0], slots["x_axis"][0]),
    "funnel": lambda datasource, slots: funnel(datasource, slots["groupby"], slots["metric"][0]),
    "gauge_chart": lambda datasource, slots: gauge_chart(datasource, slots["metric"][0], slots.get("groupby", [])),
    "treemap_v2": lambda datasource, slots: treemap_v2(datasource, slots["metric"][0], slots["groupby"]),
    "ag_grid": lambda datasource, slots: ag_grid(datasource, slots.get("groupby", []), slots.get("metrics", [])),
    "pivot_table_v2": lambda datasource, slots: pivot_table_v2(datasource, slots["metrics"], slots["groupbyRows"], slots.get("groupbyColumns", [])),
    "sunburst_v2": lambda datasource, slots: sunburst_v2(datasource, slots["columns"], slots["metric"][0]),
    "partition": lambda datasource, slots: partition(datasource, slots["groupby"], slots["metrics"]),
    "word_cloud": lambda datasource, slots: word_cloud(datasource, slots["series"][0], slots["metric"][0]),
    "cal_heatmap": lambda datasource, slots: cal_heatmap(datasource, slots["metrics"][0], slots["x_axis"][0]),
    "time_pivot": lambda datasource, slots: time_pivot(datasource, slots["metric"][0], slots["x_axis"][0]),
    "time_table": lambda datasource, slots: time_table(datasource, slots["metrics"], slots["x_axis"][0], slots.get("groupby", [])),
    "bubble_v2": lambda datasource, slots: bubble_v2(datasource, slots["entity"][0], slots["x"][0], slots["y"][0], slots["size"][0], (slots.get("series") or [None])[0]),
    "radar": lambda datasource, slots: radar(datasource, slots["groupby"], slots["metrics"]),
    "box_plot": lambda datasource, slots: box_plot(datasource, slots["groupby"], slots["metrics"]),
    "histogram_v2": lambda datasource, slots: histogram_v2(datasource, slots["column"][0].column["column_name"], slots.get("groupby", [])),
    "waterfall": lambda datasource, slots: waterfall(datasource, slots["x_axis"][0], slots["metric"][0], slots.get("groupby", [])),
    "mixed_timeseries": lambda datasource, slots: mixed_timeseries(datasource, slots["x_axis"][0], slots["metrics"], slots["metrics_b"]),
    "rose": lambda datasource, slots: rose(datasource, slots.get("groupby", []), slots["metrics"], slots["x_axis"][0]),
    "para": lambda datasource, slots: para(datasource, slots["series"][0], slots["metrics"]),
    "paired_ttest": lambda datasource, slots: paired_ttest(datasource, slots["groupby"], slots["metrics"]),
    "horizon": lambda datasource, slots: horizon(datasource, slots["series"][0], slots["metrics"][0], slots["x_axis"][0]),
    "gantt_chart": lambda datasource, slots: gantt_chart(datasource, slots["x_axis"][0], slots["end_time"][0], slots["series"][0]),
    "bullet": lambda datasource, slots: bullet(datasource, slots["metric"][0], slots.get("groupby", [])),
    "echarts_area": lambda datasource, slots: echarts_timeseries("echarts_area", datasource, slots["x_axis"][0], slots["metrics"][0], slots.get("groupby", [])),
    "echarts_timeseries_smooth": lambda datasource, slots: echarts_timeseries("echarts_timeseries_smooth", datasource, slots["x_axis"][0], slots["metrics"][0], slots.get("groupby", [])),
    "echarts_timeseries_step": lambda datasource, slots: echarts_timeseries("echarts_timeseries_step", datasource, slots["x_axis"][0], slots["metrics"][0], slots.get("groupby", [])),
    "echarts_timeseries": lambda datasource, slots: echarts_timeseries("echarts_timeseries", datasource, slots["x_axis"][0], slots["metrics"][0], slots.get("groupby", [])),
    "echarts_timeseries_scatter": lambda datasource, slots: echarts_timeseries("echarts_timeseries_scatter", datasource, slots["x_axis"][0], slots["metrics"][0], slots.get("groupby", [])),
}
