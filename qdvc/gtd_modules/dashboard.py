"""
Performance-dashboard rendering for `gtd.py generate_dashboard`: turn the
per-email metrics (from metrics.compute_email_metrics) into the *Performance*
page of the generated site — its chart payload plus the pre-rendered HTML tables
and stat cards.

This module owns metrics → HTML/chart-data. It does NOT own the page: `webui.py`
wraps what `build_performance_page` returns in the site chrome (sidebar,
toolbar, status bar) and writes the Plotly bundle out as a sibling asset, so the
whole site still renders with no network access and no CDN. Colours are left to
the stylesheet and `perf.js`, which re-theme the charts for light/dark mode; only
theme-neutral values (e.g. the translucent Sankey link grey) are set here.

Charts drawn (all via the bundled Plotly):
  * Headline KPI table — mean & median of ttS, Td, Wd, tttR across the ongoing,
    resolved, and combined ("both") corpora.
  * Box-and-whisker plots — one per metric, split by corpus.
  * Horizontal stacked bar — one bar per resolved email, segments ttS / Td / Wd
    (some may be zero-width where a stage was backfilled to a same-day stamp).
  * Sankey — flow from "took place" through triage / actionable to the
    resolution states, with same-date steps collapsed into "hops".

Everything is plain data assembled in Python and handed to Plotly by the site's
`perf.js`; no build step or framework.
"""

import html
import os
from datetime import date, timedelta

# The metrics we report on, in display order, with their full names.
METRIC_ORDER = ["ttS", "Td", "Wd", "tttR"]
METRIC_FULLNAME = {
    "ttS": "Time-to-system (ttS)",
    "Td": "Triage duration (Td)",
    "Wd": "Work duration (Wd)",
    "tttR": "Total-time-to-resolution (tttR)",
}
# View keys for the account filter: the always-present "everything" view and the
# catch-all for emails that matched none of the configured own-accounts.
ALL_ACCOUNTS = "All accounts"
UNATTRIBUTED = "(unattributed)"

# Corpora (subsets of emails) each metric is summarised over.
CORPORA = ["ongoing", "resolved", "both"]
CORPUS_LABEL = {"ongoing": "Ongoing", "resolved": "Resolved",
                "both": "Both (ongoing + resolved)"}

# Backlog-age threshold (days): ongoing emails older than this are "stale". The
# user asked for 14 days.
STALE_THRESHOLD_DAYS = 14

# "Resolved within N days" hit-rate buckets (tttR), and the percentiles reported.
HIT_RATE_DAYS = [1, 7, 14, 30]
PERCENTILES = [50, 90, 95]

# Sankey canvas heights (px). The fan-out layout is taller so the spread-out
# terminus nodes have vertical room to splay into a "<" fan; the straight layout
# uses Plotly's own compact automatic placement.
SANKEY_STRAIGHT_HEIGHT_PX = 460
SANKEY_FANOUT_HEIGHT_PX = 640


def _mean(values):
    """Arithmetic mean of a list of numbers, or None if empty."""
    return sum(values) / len(values) if values else None


def _median(values):
    """
    Median of a list of numbers, or None if empty. For an even count, the mean
    of the two middle values.

    Example:
        _median([1, 2, 3, 4])  # -> 2.5
    """
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2


def _percentile(values, pct):
    """
    The pct-th percentile (0-100) of a list of numbers using linear
    interpolation between closest ranks, or None if empty.

    Example:
        _percentile([1, 2, 3, 4], 50)  # -> 2.5
        _percentile([1, 2, 3, 4], 90)  # -> 3.7
    """
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    rank = (pct / 100) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _fmt(value):
    """Format a metric summary value: one decimal place, or an en-dash if None."""
    if value is None:
        return "\u2013"
    return f"{value:.1f}"


def collect_metric_values(computed):
    """
    Bucket the metric values by (metric, corpus). `computed` is the list of
    per-email dicts from metrics.compute_email_metrics. Weird emails carry no
    metrics, so they contribute nothing. The "both" corpus is ongoing ∪
    resolved (i.e. every non-weird email that has the metric).

    Returns {metric: {corpus: [values...]}}.

    Example:
        collect_metric_values([...])["ttS"]["resolved"]  # -> [2, 4, 0, ...]
    """
    buckets = {m: {c: [] for c in CORPORA} for m in METRIC_ORDER}
    for email in computed:
        status = email["status"]
        if status == "weird":
            continue
        for metric, value in email["metrics"].items():
            if metric not in buckets:
                continue
            if status in ("ongoing", "resolved"):
                buckets[metric][status].append(value)
            buckets[metric]["both"].append(value)
    return buckets


def build_kpi_rows(buckets):
    """
    Build the headline KPI table rows: for each metric and corpus, the mean,
    median, and count. Returns a list of dicts ready to render as table rows.

    Example:
        build_kpi_rows(buckets)[0]
        # -> {"metric": "ttS", "corpus": "ongoing", "n": 3,
        #     "mean": 1.7, "median": 2.0}
    """
    rows = []
    for metric in METRIC_ORDER:
        for corpus in CORPORA:
            values = buckets[metric][corpus]
            rows.append({
                "metric": metric,
                "corpus": corpus,
                "n": len(values),
                "mean": _mean(values),
                "median": _median(values),
            })
    return rows


def build_box_traces(buckets):
    """
    Build Plotly box-plot traces: one trace per corpus, with a box for each
    metric on the x-axis (grouped). Returns a list of trace dicts.

    Example:
        build_box_traces(buckets)  # -> [{"type": "box", "name": "Ongoing", ...}]
    """
    traces = []
    for corpus in CORPORA:
        xs, ys = [], []
        for metric in METRIC_ORDER:
            for value in buckets[metric][corpus]:
                xs.append(METRIC_FULLNAME[metric])
                ys.append(value)
        traces.append({
            "type": "box", "name": CORPUS_LABEL[corpus],
            "x": xs, "y": ys, "boxmean": True,
        })
    return traces


def build_stacked_bar(computed):
    """
    Build the horizontal stacked-bar traces for the resolved corpus: one
    horizontal bar per resolved email, stacked from ttS + Td + Wd. Emails are
    ordered by tttR (largest at the top). Returns a list of three traces (one
    per component). Zero-width segments (from same-day backfills) are kept; they
    simply render invisibly.

    Example:
        build_stacked_bar(computed)  # -> [ttS trace, Td trace, Wd trace]
    """
    resolved = [e for e in computed if e["status"] == "resolved"]
    resolved.sort(key=lambda e: e["metrics"].get("tttR", 0))
    labels = [e["filename"] for e in resolved]
    components = [("ttS", "Time-to-system"), ("Td", "Triage duration"),
                  ("Wd", "Work duration")]
    traces = []
    for key, name in components:
        traces.append({
            "type": "bar", "orientation": "h", "name": name,
            "y": labels,
            "x": [e["metrics"].get(key, 0) for e in resolved],
        })
    return traces


def build_sankey(computed):
    """
    Build Plotly Sankey traces for the non-weird corpus (resolved AND ongoing
    emails; weird ones are excluded). Each email contributes one path through its
    distinct-date stages (consecutive stages sharing a date are "hops" and are
    skipped, so the path links the stages that actually advanced the date).
    Resolved emails terminate at their final resting state; an email that passed
    through several resolution states shows each in workflow order (delegated ->
    reference -> archive), with same-date states collapsing to the most-resolved.
    Emails not yet resolved terminate at a shared "ongoing" node.

    Returns (straight_trace, fanout_trace):
      * straight_trace — Plotly's own automatic node layout (no pinned positions);
      * fanout_trace   — the same flows and the same nodes, but with the vertical
        spacing between nodes GROWING as x increases (left to right), so the
        diagram splays open into a "<" fan. No node is treated as a special
        terminus; each is placed by its workflow column and spread within it.

    The dashboard offers these as two tabs ("Fan-out Sankey" / "Straight Sankey").

    Example:
        straight, fanout = build_sankey(computed)
    """
    node_labels = []
    node_index = {}

    def node(label):
        if label not in node_index:
            node_index[label] = len(node_labels)
            node_labels.append(label)
        return node_index[label]

    link_counts = {}  # (src_idx, dst_idx) -> count

    for email in computed:
        status = email["status"]
        if status not in ("resolved", "ongoing"):
            continue  # weird emails are excluded from the Sankey
        # The flow terminates at a resolution node (resolved emails) or at a
        # shared "ongoing" node (emails not yet resolved).
        #
        # For RESOLVED emails the resolution stage is a real dated stage, so it
        # takes part in same-date collapsing like any other: all stages are
        # collapsed together, and where dates tie the FURTHEST-progressed label
        # wins (so a resolution sharing the last stage's date absorbs it). For
        # ONGOING emails there is no resolution stage; we collapse the real
        # stages and then append a SYNTHETIC "ongoing" terminus, which — not
        # being a real date — never absorbs a same-date intermediate.
        stages = email["stages"]
        if status == "resolved":
            collapse_stages, synthetic_terminus = stages, None
        else:  # ongoing
            collapse_stages, synthetic_terminus = stages, "ongoing"
        terminus_label = stages[-1][0]  # resolution label (resolved) or last real stage
        # Collapse hops: stages sharing a date are the "same moment" and collapse
        # to ONE node. Which label survives?
        #   * "took place" (the origin) always wins if it is in the tie, so the
        #     arrival is never hidden: an email that took place, was triaged, was
        #     actioned, and was archived all on one date flows "took place ->
        #     archive" (the resolution still surfaces, see the guard below).
        #   * otherwise the FURTHEST-progressed (latest) stage at that date wins,
        #     reflecting how far the email had advanced by then: triage+actionable
        #     on one date -> "actionable"; actionable+archive on one date ->
        #     "archive".
        kept = []
        last_date = None
        for label, when in collapse_stages:
            if last_date is None or when != last_date:
                kept.append(label)
                last_date = when
            else:
                # same date as the last kept stage -> a hop; keep the furthest
                # label, but never overwrite the origin ("took place").
                if kept[-1] != "took place":
                    kept[-1] = label
        # For resolved emails, the resolution must always end the flow, even when
        # every stage (including it) shared the arrival date and collapsed into
        # "took place": force the resolution terminus on if the collapse dropped
        # it. For ongoing emails, append the synthetic "ongoing" terminus.
        if synthetic_terminus is not None:
            kept.append(synthetic_terminus)
        elif kept[-1] != terminus_label:
            kept.append(terminus_label)
        for src, dst in zip(kept, kept[1:]):
            si, di = node(src), node(dst)
            link_counts[(si, di)] = link_counts.get((si, di), 0) + 1

    sources = [s for (s, _) in link_counts]
    targets = [t for (_, t) in link_counts]
    values = [link_counts[k] for k in link_counts]

    # Leave node colours to Plotly (it assigns a distinct colour per node, which
    # keeps the stages visually separable). Only the LINKS need help: Plotly's
    # default link fill is a near-transparent light grey that disappears over a
    # transparent background, so give links an explicit translucent grey that
    # stays visible in both light and dark themes without clashing with the
    # multi-coloured nodes.
    link_colour = "rgba(128, 128, 128, 0.4)"

    common_node = {"label": node_labels, "pad": 18, "thickness": 18}
    common_link = {"source": sources, "target": targets, "value": values,
                   "color": [link_colour] * len(sources)}

    # --- straight layout: let Plotly place the nodes (no pinned x/y). ---------
    straight_trace = {
        "type": "sankey",
        "node": dict(common_node),
        "link": dict(common_link),
    }

    # --- fan-out layout: same flows as straight, but the vertical spacing
    # between nodes GROWS as x increases, so the diagram splays open into a "<"
    # fan left-to-right. No node is treated as a special "terminus": every node
    # is placed purely by its workflow column (x) and spread within that column.
    # Plotly honours node x/y under the default "snap" arrangement (nodes stay
    # draggable) but needs coords on EVERY node once any are set, and both must
    # be strictly inside (0, 1).
    #
    # Each label maps to a workflow column; the resolution states and "ongoing"
    # share the final column. Within a column the nodes are spread symmetrically
    # about the vertical centre, with a half-span that increases with the
    # column's x (SPAN_AT_LEFT at x=0 growing to SPAN_AT_RIGHT at x=1) — that
    # widening is what produces the fan.
    COLUMN_INDEX = {
        "took place": 0,
        "triage": 1,
        "actionable": 2,
        "delegated": 3, "reference": 3, "archive": 3, "ongoing": 3,
    }
    n_cols = 4
    col_x = [0.02, 0.35, 0.68, 0.98]  # x per column index (strictly inside 0..1)
    SPAN_AT_LEFT, SPAN_AT_RIGHT = 0.06, 0.94  # total vertical span by x

    # Group node indices by column (preserving encounter order for stable slots).
    cols = {c: [] for c in range(n_cols)}
    for i, lbl in enumerate(node_labels):
        cols[COLUMN_INDEX.get(lbl, n_cols - 1)].append(i)

    node_x = [0.5] * len(node_labels)
    node_y = [0.5] * len(node_labels)
    for c, members in cols.items():
        x = col_x[c]
        # Total vertical span for this column grows linearly with x.
        frac = x  # x is already in (0, 1)
        span = SPAN_AT_LEFT + (SPAN_AT_RIGHT - SPAN_AT_LEFT) * frac
        k = len(members)
        for slot, idx in enumerate(sorted(members, key=lambda i: node_labels[i])):
            node_x[idx] = x
            if k == 1:
                node_y[idx] = 0.5
            else:
                # Spread evenly across [0.5 - span/2, 0.5 + span/2].
                top = 0.5 - span / 2
                node_y[idx] = top + slot * (span / (k - 1))
            # Clamp strictly inside (0, 1) for Plotly.
            node_y[idx] = min(0.98, max(0.02, node_y[idx]))

    fanout_node = dict(common_node)
    fanout_node["x"] = node_x
    fanout_node["y"] = node_y
    fanout_trace = {
        "type": "sankey",
        "arrangement": "snap",
        "node": fanout_node,
        "link": dict(common_link),
    }

    return straight_trace, fanout_trace


def build_status_counts(computed):
    """
    Count emails by status. Returns {"ongoing": n, "resolved": n, "weird": n}.

    Example:
        build_status_counts(computed)  # -> {"ongoing": 4, "resolved": 9, "weird": 1}
    """
    counts = {"ongoing": 0, "resolved": 0, "weird": 0}
    for email in computed:
        counts[email["status"]] = counts.get(email["status"], 0) + 1
    return counts


def build_backlog_stats(computed, today):
    """
    Current open-backlog aging, from the ongoing (non-weird) emails. `today` is a
    datetime.date. "Open age" is today - arrival; "stall" is today - the email's
    latest reached stage date (ds_actionable if set, else ds_triage, else
    arrival). Returns a dict:
        {"count", "mean_age", "median_age", "oldest_age", "oldest_filename",
         "stale_count", "stale_threshold",
         "longest_stall_days", "longest_stall_filename", "longest_stall_stage"}
    with None/0 where there are no ongoing emails.

    Example:
        build_backlog_stats(computed, date(2026, 7, 3))
        # -> {"count": 4, "median_age": 12.0, "oldest_age": 40, ...}
    """
    ongoing = [e for e in computed if e["status"] == "ongoing" and e["email_date"]]
    ages = [(today - e["email_date"]).days for e in ongoing]
    result = {
        "count": len(ongoing),
        "mean_age": _mean(ages),
        "median_age": _median(ages),
        "oldest_age": max(ages) if ages else None,
        "oldest_filename": None,
        "stale_count": sum(1 for a in ages if a > STALE_THRESHOLD_DAYS),
        "stale_threshold": STALE_THRESHOLD_DAYS,
        "longest_stall_days": None,
        "longest_stall_filename": None,
        "longest_stall_stage": None,
    }
    if ongoing:
        oldest = max(ongoing, key=lambda e: (today - e["email_date"]).days)
        result["oldest_filename"] = oldest["filename"]
        # Stall = days since the email last advanced a stage.
        def stall(e):
            latest = e["actionable_date"] or e["triage_date"] or e["email_date"]
            stage = ("actionable" if e["actionable_date"] else
                     "triage" if e["triage_date"] else "arrival")
            return (today - latest).days, stage
        stalled = max(ongoing, key=lambda e: stall(e)[0])
        days, stage = stall(stalled)
        result["longest_stall_days"] = days
        result["longest_stall_filename"] = stalled["filename"]
        result["longest_stall_stage"] = stage
    return result


def build_backlog_age_histogram(computed, today):
    """
    Bucket ongoing (non-weird) emails by open age into fixed bands. Returns
    (labels, counts) ready for a bar chart.

    Example:
        build_backlog_age_histogram(computed, date(2026, 7, 3))
        # -> (["0-7", "8-14", "15-30", "31-90", "90+"], [2, 1, 0, 3, 1])
    """
    bands = [(0, 7, "0\u20137"), (8, 14, "8\u201314"), (15, 30, "15\u201330"),
             (31, 90, "31\u201390"), (91, None, "90+")]
    labels = [b[2] for b in bands]
    counts = [0] * len(bands)
    for e in computed:
        if e["status"] != "ongoing" or not e["email_date"]:
            continue
        age = (today - e["email_date"]).days
        for i, (lo, hi, _) in enumerate(bands):
            if age >= lo and (hi is None or age <= hi):
                counts[i] += 1
                break
    return labels, counts


def build_percentile_rows(buckets):
    """
    Percentile summary of each metric (p50/p90/p95) over the "both" corpus.
    Returns a list of {"metric", "p50", "p90", "p95"} dicts.

    Example:
        build_percentile_rows(buckets)[0]
        # -> {"metric": "ttS", "p50": 2.0, "p90": 7.4, "p95": 9.1}
    """
    rows = []
    for metric in METRIC_ORDER:
        values = buckets[metric]["both"]
        row = {"metric": metric}
        for p in PERCENTILES:
            row[f"p{p}"] = _percentile(values, p)
        rows.append(row)
    return rows


def build_hit_rate_rows(computed):
    """
    "Resolved within N days" hit rates over the resolved corpus, using tttR.
    Returns a list of {"days", "count", "rate"} dicts (rate is a 0..1 fraction),
    plus the resolved total is implicit in count/rate. Empty resolved corpus ->
    rates of None.

    Example:
        build_hit_rate_rows(computed)[0]
        # -> {"days": 1, "count": 5, "rate": 0.42}
    """
    tttrs = [e["metrics"]["tttR"] for e in computed
             if e["status"] == "resolved" and "tttR" in e["metrics"]]
    total = len(tttrs)
    rows = []
    for n in HIT_RATE_DAYS:
        c = sum(1 for v in tttrs if v <= n)
        rows.append({"days": n, "count": c,
                     "rate": (c / total) if total else None})
    return rows


def _period_key(d, period):
    """
    Map a date to its bucket key for a period. Weekly keys are the Monday of the
    week (ISO), monthly keys are the first of the month; both as ISO date
    strings so they sort lexicographically.

    Example:
        _period_key(date(2026, 7, 3), "weekly")   # -> "2026-06-29" (that Monday)
        _period_key(date(2026, 7, 3), "monthly")  # -> "2026-07-01"
    """
    if period == "weekly":
        monday = d - timedelta(days=d.weekday())
        return monday.isoformat()
    return d.replace(day=1).isoformat()


def build_flow_series(computed, period):
    """
    Arrivals vs resolutions per period, plus the cumulative backlog (running
    arrivals minus running resolutions) at the end of each period. Non-weird
    emails only; arrivals bucket by email_date, resolutions by resolution_date.
    Returns a dict of parallel arrays:
        {"periods": [...], "arrivals": [...], "resolutions": [...],
         "cumulative_arrivals": [...], "cumulative_resolutions": [...],
         "backlog": [...]}
    covering every period from the first activity to the last (no gaps).

    Example:
        build_flow_series(computed, "monthly")
        # -> {"periods": ["2026-06-01", "2026-07-01"], "arrivals": [10, 4], ...}
    """
    arrivals = {}
    resolutions = {}
    for e in computed:
        if e["status"] == "weird":
            continue
        if e["email_date"]:
            k = _period_key(e["email_date"], period)
            arrivals[k] = arrivals.get(k, 0) + 1
        if e["status"] == "resolved" and e["resolution_date"]:
            k = _period_key(e["resolution_date"], period)
            resolutions[k] = resolutions.get(k, 0) + 1

    keys = set(arrivals) | set(resolutions)
    if not keys:
        return {"periods": [], "arrivals": [], "resolutions": [],
                "cumulative_arrivals": [], "cumulative_resolutions": [],
                "backlog": []}

    # Fill every period between the first and last key so the lines have no gaps.
    start = date.fromisoformat(min(keys))
    end = date.fromisoformat(max(keys))
    periods = []
    cur = start
    while cur <= end:
        periods.append(cur.isoformat())
        if period == "weekly":
            cur = cur + timedelta(days=7)
        else:
            # advance one month
            y, m = cur.year, cur.month
            cur = date(y + (m // 12), (m % 12) + 1, 1)

    arr = [arrivals.get(k, 0) for k in periods]
    res = [resolutions.get(k, 0) for k in periods]
    cum_a, cum_r, backlog = [], [], []
    ra = rr = 0
    for a, r in zip(arr, res):
        ra += a
        rr += r
        cum_a.append(ra)
        cum_r.append(rr)
        backlog.append(ra - rr)
    return {"periods": periods, "arrivals": arr, "resolutions": res,
            "cumulative_arrivals": cum_a, "cumulative_resolutions": cum_r,
            "backlog": backlog}


def read_plotly_bundle(plotly_js_path):
    """
    Read the local minified Plotly.js bundle to inline into the dashboard.
    Raises FileNotFoundError with a clear message if the path is unset or the
    file is missing — the caller turns that into a friendly CLI error, since
    running offline is a hard requirement (no CDN fallback).

    Example:
        read_plotly_bundle("/home/me/assets/plotly.min.js")  # -> "<the JS>"
    """
    if not (plotly_js_path or "").strip():
        raise FileNotFoundError(
            "no plotly_js_path is set in config.yml. Download the minified "
            "Plotly.js bundle (see README.md) and point plotly_js_path at it.")
    if not os.path.isfile(plotly_js_path):
        raise FileNotFoundError(
            f"plotly_js_path points at '{plotly_js_path}', which does not exist. "
            f"Download the minified Plotly.js bundle (see README.md) and set "
            f"plotly_js_path to its location.")
    with open(plotly_js_path, encoding="utf-8") as f:
        return f.read()


def _kpi_table_html(kpi_rows):
    """
    Render the KPI rows as an HTML table (escaped): one row per metric, one
    column per corpus (Ongoing / Resolved / Both), and each cell a right-aligned
    three-line "card" showing Mean, Median, and n. `kpi_rows` is the flat list
    from build_kpi_rows; we pivot it into a {metric: {corpus: row}} lookup.
    """
    by_metric_corpus = {}
    for row in kpi_rows:
        by_metric_corpus.setdefault(row["metric"], {})[row["corpus"]] = row

    header_cells = "".join(
        f"<th>{html.escape(CORPUS_LABEL[c])}</th>" for c in CORPORA)
    out = ['<table class="kpi">',
           f"<thead><tr><th>Metric</th>{header_cells}</tr></thead>", "<tbody>"]
    for metric in METRIC_ORDER:
        cells = [f"<th scope=\"row\">{html.escape(METRIC_FULLNAME[metric])}</th>"]
        for corpus in CORPORA:
            row = by_metric_corpus.get(metric, {}).get(corpus, {})
            mean = _fmt(row.get("mean"))
            median = _fmt(row.get("median"))
            n = row.get("n", 0)
            cells.append(
                '<td><div class="kpi-card">'
                f'<div><span class="kpi-lbl">Mean</span> = <span class="kpi-val">{mean}</span></div>'
                f'<div><span class="kpi-lbl">Median</span> = <span class="kpi-val">{median}</span></div>'
                f'<div><span class="kpi-lbl">n</span> = <span class="kpi-val">{n}</span></div>'
                "</div></td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def _backlog_html(bl):
    """Render the open-backlog KPI cards (escaped). `bl` is build_backlog_stats."""
    if not bl["count"]:
        return '<p class="sub">No ongoing emails &mdash; the backlog is empty.</p>'

    def card(label, value, sub=""):
        sub_html = f'<div class="kpi-lbl">{html.escape(sub)}</div>' if sub else ""
        return ('<div class="stat">'
                f'<div class="stat-val">{html.escape(str(value))}</div>'
                f'<div class="stat-lbl">{html.escape(label)}</div>'
                f'{sub_html}</div>')

    oldest = (f"{bl['oldest_age']} days" if bl['oldest_age'] is not None else "\u2013")
    stall = (f"{bl['longest_stall_days']} days"
             if bl['longest_stall_days'] is not None else "\u2013")
    cards = [
        card("open now", bl["count"]),
        card("median age", f"{_fmt(bl['median_age'])} days"),
        card("mean age", f"{_fmt(bl['mean_age'])} days"),
        card(f"older than {bl['stale_threshold']} days", bl["stale_count"]),
        card("oldest open", oldest, bl["oldest_filename"] or ""),
        card("longest stall", stall,
             (f"in {bl['longest_stall_stage']}: {bl['longest_stall_filename']}"
              if bl["longest_stall_filename"] else "")),
    ]
    return '<div class="stat-row">' + "".join(cards) + "</div>"


def _percentile_table_html(rows):
    """Render the percentile table (escaped). `rows` is build_percentile_rows."""
    head = "".join(f"<th>p{p}</th>" for p in PERCENTILES)
    out = ['<table class="kpi">',
           f"<thead><tr><th>Metric</th>{head}</tr></thead>", "<tbody>"]
    for row in rows:
        cells = [f'<th scope="row">{html.escape(METRIC_FULLNAME[row["metric"]])}</th>']
        for p in PERCENTILES:
            cells.append(f'<td class="num">{_fmt(row.get(f"p{p}"))}</td>')
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def _hit_rate_html(rows):
    """Render the resolved-within-N-days hit-rate cards (escaped)."""
    cards = []
    for row in rows:
        rate = ("\u2013" if row["rate"] is None else f"{row['rate'] * 100:.0f}%")
        cards.append('<div class="stat">'
                     f'<div class="stat-val">{rate}</div>'
                     f'<div class="stat-lbl">within {row["days"]} '
                     f'day{"s" if row["days"] != 1 else ""}</div>'
                     f'<div class="kpi-lbl">{row["count"]} resolved</div>'
                     "</div>")
    return '<div class="stat-row">' + "".join(cards) + "</div>"


def _account_view(subset, today):
    """
    Compute one account's dashboard view from its subset of computed emails.
    Returns (payload, blocks) where `payload` is the JS chart data for this
    subset and `blocks` is a dict of pre-rendered HTML fragments (counts, KPI
    table, backlog cards, percentile table, hit-rate cards, weird note). One of
    these is produced per account (plus "All accounts"); the browser shows the
    selected account's blocks and redraws charts from its payload.
    """
    buckets = collect_metric_values(subset)
    counts = build_status_counts(subset)
    backlog = build_backlog_stats(subset, today)
    age_labels, age_counts = build_backlog_age_histogram(subset, today)

    weird_note = ("" if not counts["weird"] else
                  f'<p class="note"><strong>{counts["weird"]}</strong> email(s) '
                  "were classified <em>weird</em> (their date progression is "
                  "inconsistent — e.g. triaged before the email date, or "
                  "resolution states out of the delegated&nbsp;&rarr;&nbsp;"
                  "reference&nbsp;&rarr;&nbsp;archive order) and are excluded "
                  "from every metric and chart above.</p>")

    sankey_straight, sankey_fanout = build_sankey(subset)
    payload = {
        "box": build_box_traces(buckets),
        "bar": build_stacked_bar(subset),
        "sankeyStraight": [sankey_straight],
        "sankeyFanout": [sankey_fanout],
        "sankeyStraightHeight": SANKEY_STRAIGHT_HEIGHT_PX,
        "sankeyFanoutHeight": SANKEY_FANOUT_HEIGHT_PX,
        "ageLabels": age_labels,
        "ageCounts": age_counts,
        "flowWeekly": build_flow_series(subset, "weekly"),
        "flowMonthly": build_flow_series(subset, "monthly"),
    }
    blocks = {
        "counts": (f"<span><strong>{counts['ongoing']}</strong> ongoing</span>"
                   f"<span><strong>{counts['resolved']}</strong> resolved</span>"
                   f"<span><strong>{counts['weird']}</strong> weird</span>"),
        "backlog": _backlog_html(backlog),
        "kpi": _kpi_table_html(build_kpi_rows(buckets)) + weird_note,
        "percentile": _percentile_table_html(build_percentile_rows(buckets)),
        "hit_rate": _hit_rate_html(build_hit_rate_rows(subset)),
    }
    return payload, blocks


def build_views(computed, today=None, account_names=None):
    """
    Precompute one dashboard view per account filter. Returns
    (view_keys, payloads, blocks): the ordered view keys — "All accounts", then
    each configured account, then "(unattributed)" if any email matched none —
    and, keyed by view, that subset's chart payload and its pre-rendered HTML
    blocks (see `_account_view`).

    All aggregation therefore happens in Python: the browser only chooses which
    precomputed view to display. With no accounts configured there is a single
    "All accounts" view and the UI omits the selector.

    Example:
        keys, payloads, blocks = build_views(computed, account_names=["Work"])
        # keys -> ["All accounts", "Work", "(unattributed)"]
    """
    today = today or date.today()
    view_keys = [ALL_ACCOUNTS]
    for name in (account_names or []):
        if name not in view_keys:
            view_keys.append(name)
    if (account_names or []) and any(e.get("account") is None for e in computed):
        view_keys.append(UNATTRIBUTED)

    def subset_for(key):
        if key == ALL_ACCOUNTS:
            return computed
        if key == UNATTRIBUTED:
            return [e for e in computed if e.get("account") is None]
        return [e for e in computed if e.get("account") == key]

    payloads, blocks = {}, {}
    for key in view_keys:
        payloads[key], blocks[key] = _account_view(subset_for(key), today)
    return view_keys, payloads, blocks


def _account_blocks_html(view_keys, blocks, block_key):
    """
    Emit one container per view for a given block, with only the first visible.
    `selectAccount` in perf.js flips the `hidden` attribute by `data-account`.
    """
    parts = []
    for i, key in enumerate(view_keys):
        hidden = "" if i == 0 else " hidden"
        parts.append(f'<div class="acct-block" data-block="{html.escape(block_key)}" '
                     f'data-account="{html.escape(key)}"{hidden}>'
                     f"{blocks[key][block_key]}</div>")
    return "\n".join(parts)


def _account_selector_html(view_keys):
    """The account dropdown, or "" when only the single all-accounts view exists."""
    if len(view_keys) <= 1:
        return ""
    options = "".join(f'<option value="{html.escape(k)}">{html.escape(k)}</option>'
                      for k in view_keys)
    return ('<div class="acct-filter panel-inset">'
            '<label for="acct">Account:</label> '
            f'<select id="acct" onchange="selectAccount(this.value)">{options}</select>'
            "</div>")


def build_performance_page(computed, generated_on=None, today=None,
                           account_names=None):
    """
    Build the *Performance* page of the generated site: its body HTML (the
    account filter, status counts, and every section with its tables and chart
    containers) and the chart payload `perf.js` draws from.

    Returns (body_html, data) where `data` is {"views": ..., "keys": ...},
    serialised by webui.py into the site's data/performance.js. The page chrome
    (sidebar, toolbar, status bar) and the Plotly asset are webui.py's job.

    Example:
        body_html, data = build_performance_page(computed)
        # -> ("<section>…", {"views": {...}, "keys": ["All accounts"]})
    """
    today = today or date.today()
    generated_on = generated_on or today.isoformat()
    view_keys, payloads, blocks = build_views(computed, today, account_names)

    def section(title, prose, inner):
        return (f"<section>\n<h2>{title}</h2>\n<p class=\"prose\">{prose}</p>\n"
                f"{inner}\n</section>")

    parts = [
        _account_selector_html(view_keys),
        '<div class="counts" id="counts-container">',
        _account_blocks_html(view_keys, blocks, "counts"),
        "</div>",
        section(
            "Open backlog (right now)",
            "Aging of the emails still open (ongoing), as of "
            f"{html.escape(generated_on)}. Unlike the metrics below &mdash; which "
            "summarise emails you have already finished &mdash; these describe the "
            "current pile and whether it is going stale.",
            _account_blocks_html(view_keys, blocks, "backlog")
            + '\n<div id="ageHist" class="chart" style="height: 320px;"></div>'),
        section(
            "Throughput &amp; backlog over time",
            "Arrivals vs resolutions per period, and the running backlog "
            "(cumulative arrivals minus resolutions). If the backlog line trends "
            "upward, work is arriving faster than it is being resolved.",
            '<div class="tabs" role="tablist" aria-label="Flow period">'
            '<button id="flow-monthly" role="tab" aria-controls="flow" '
            'aria-selected="true" onclick="selectFlow(\'monthly\')">Monthly</button>'
            '<button id="flow-weekly" role="tab" aria-controls="flow" '
            'aria-selected="false" onclick="selectFlow(\'weekly\')">Weekly</button>'
            "</div>\n"
            '<div id="flowBars" class="chart" style="height: 320px;"></div>\n'
            '<div id="flow" class="chart" style="height: 320px;"></div>'),
        section(
            "Headline KPIs",
            "Mean, median (in days), and count (n) of each metric across the "
            "ongoing, resolved, and combined corpora. Weird emails are excluded.",
            _account_blocks_html(view_keys, blocks, "kpi")),
        section(
            "Percentiles",
            "Percentiles (in days) of each metric across all non-weird emails "
            "that have it. p90 means 90% of emails came in at or under that "
            "value &mdash; less skewed by outliers than the mean.",
            _account_blocks_html(view_keys, blocks, "percentile")),
        section(
            "Resolution hit rate",
            "Share of resolved emails whose total-time-to-resolution fell within "
            "each window.",
            _account_blocks_html(view_keys, blocks, "hit_rate")),
        section(
            "Distributions (box &amp; whisker)",
            "Each box spans the interquartile range; the line is the median and "
            "the dashed marker the mean. Metrics are grouped by corpus.",
            '<div id="box" class="chart"></div>'),
        section(
            "Resolved emails: time composition",
            "One horizontal bar per resolved email, stacked as time-to-system + "
            "triage duration + work duration (which sum to "
            "total-time-to-resolution). Ordered by total-time-to-resolution. Some "
            "segments may be zero-width where a stage was recorded on the same "
            "day as the previous one.",
            '<div id="bar" class="chart" style="height: 620px;"></div>'),
        section(
            "Flow from arrival to resolution (Sankey)",
            "How emails flowed from taking place, through triage and actionable, "
            "to their resolution state &mdash; or to an <em>ongoing</em> node for "
            "emails not yet resolved. Steps sharing a date are collapsed "
            "(&ldquo;hops&rdquo;), so an email that was triaged and actioned on "
            "the same day links straight through. Weird emails are excluded.",
            '<div class="tabs" role="tablist" aria-label="Sankey layout">'
            '<button id="tab-fanout" role="tab" aria-controls="sankey" '
            'aria-selected="true" onclick="selectSankey(\'fanout\')">Fan-out Sankey</button>'
            '<button id="tab-straight" role="tab" aria-controls="sankey" '
            'aria-selected="false" onclick="selectSankey(\'straight\')">Straight Sankey</button>'
            "</div>\n"
            '<p class="prose" id="sankey-caption">Node spacing widens left to '
            "right, so the flows fan out.</p>\n"
            '<div id="sankey" class="chart"></div>'),
    ]
    body_html = "\n".join(part for part in parts if part)
    return body_html, {"views": payloads, "keys": view_keys}
