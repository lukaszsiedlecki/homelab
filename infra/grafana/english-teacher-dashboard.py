#!/usr/bin/env python3
"""Generates dashboards/english-teacher.json ("English Teacher: AI": cost, latency, failures, prompt versions, logs).

Edit this script, not the JSON, then regenerate and copy the JSON to the Grafana LXC:

    python3 infra/grafana/english-teacher-dashboard.py
    ssh root@192.168.0.104 "pct exec 118 -- sh -c 'cat > /var/lib/grafana/dashboards/homelab/english-teacher.json'" \
        < infra/grafana/dashboards/english-teacher.json

Data sources (fixed uids): Prometheus (rates, latency, failures, tokens), Loki (log lines), and english-teacher's
Postgres table ai_usage for exact totals: Prometheus' increase() misses the first value of a new series, which
undercounts at low traffic. Metrics and log fields come from the backend's ai.internal.AiCalls.
"""
import json
import os
import sys

P = {"type": "prometheus", "uid": "prometheus-k8s"}
L = {"type": "loki", "uid": "loki-k8s"}
PG = {"type": "grafana-postgresql-datasource", "uid": "english-teacher-db"}

NS = 'namespace="english-teacher"'
F = NS + ', operation=~"$operation", prompt=~"$prompt"'
SQL_OPS = "operation IN (${operation:sqlstring})"
SQL_PROMPT = "COALESCE(prompt, 'none') IN (${prompt:sqlstring})"
PROVIDER = ("CASE WHEN operation = 'transcribe' THEN 'elevenlabs' "
            "WHEN operation IN ('speech', 'image') THEN 'openai' ELSE 'anthropic' END")
LOGS = '{namespace="english-teacher", container="english-teacher-backend"}'
TZ = "Europe/Warsaw"

panels = []
next_id = [1]
y = [0]


def pid():
    next_id[0] += 1
    return next_id[0]


def row(title):
    panels.append({"type": "row", "title": title, "id": pid(), "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y[0]}, "panels": []})
    y[0] += 1


def place(items, h):
    """items: list of (width, panel) laid out left to right on one line."""
    x = 0
    for w, p in items:
        p["id"] = pid()
        p["gridPos"] = {"h": h, "w": w, "x": x, "y": y[0]}
        panels.append(p)
        x += w
    y[0] += h


def prom(expr, legend="", instant=False, ref="A"):
    t = {"datasource": P, "refId": ref, "expr": expr, "legendFormat": legend}
    if instant:
        t.update({"instant": True, "range": False})
    return t


def sql(raw, fmt="time_series", ref="A"):
    return {"datasource": PG, "refId": ref, "editorMode": "code", "format": fmt, "rawQuery": True, "rawSql": raw}


def loki(expr, ref="A"):
    return {"datasource": L, "refId": ref, "expr": expr, "queryType": "range"}


def stat(title, target, unit, ds, decimals=None, description=""):
    defaults = {"unit": unit, "color": {"mode": "fixed", "fixedColor": "text"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {"type": "stat", "title": title, "description": description, "datasource": ds, "targets": [target],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "value", "graphMode": "none", "textMode": "value", "justifyMode": "center"}}


def series(title, targets, unit, ds, bars=False, stack=False, interval=None, description="", points=False):
    custom = {"drawStyle": "bars" if bars else ("points" if points else "line"), "fillOpacity": 80 if bars else 10,
              "lineWidth": 1, "pointSize": 6, "showPoints": "auto", "spanNulls": True,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    p = {"type": "timeseries", "title": title, "description": description, "datasource": ds, "targets": targets,
         "fieldConfig": {"defaults": {"unit": unit, "custom": custom}, "overrides": []},
         "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["sum"] if bars else ["mean", "max"]},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}
    if interval:
        p["interval"] = interval
    return p


def logs(title, expr, description=""):
    return {"type": "logs", "title": title, "description": description, "datasource": L, "targets": [loki(expr)],
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                        "dedupStrategy": "none", "prettifyLogMessage": False}}


# ---------------------------------------------------------------- Costs
row("Costs")
place([
    (4, stat("Cost today", sql(
        f"SELECT COALESCE(sum(cost_usd), 0) AS cost FROM ai_usage "
        f"WHERE created_at >= date_trunc('day', now() AT TIME ZONE '{TZ}') AT TIME ZONE '{TZ}' AND {SQL_OPS}", "table"),
        "currencyUSD", PG, 2, "Since midnight (Europe/Warsaw), from ai_usage.cost_usd.")),
    (4, stat("Cost this month", sql(
        f"SELECT COALESCE(sum(cost_usd), 0) AS cost FROM ai_usage "
        f"WHERE created_at >= date_trunc('month', now() AT TIME ZONE '{TZ}') AT TIME ZONE '{TZ}' AND {SQL_OPS}", "table"),
        "currencyUSD", PG, 2, "Calendar month (Europe/Warsaw), from ai_usage.cost_usd.")),
    (4, stat("Cost in range", sql(
        f"SELECT COALESCE(sum(cost_usd), 0) AS cost FROM ai_usage WHERE $__timeFilter(created_at) AND {SQL_OPS} AND {SQL_PROMPT}",
        "table"), "currencyUSD", PG, 2, "The dashboard's time range.")),
    (4, stat("AI calls in range", sql(
        f"SELECT count(*) AS calls FROM ai_usage WHERE $__timeFilter(created_at) AND {SQL_OPS} AND {SQL_PROMPT}", "table"),
        "short", PG, 0, "Successful calls recorded in ai_usage.")),
    (4, stat("Failed calls in range", prom(
        f'(sum(increase(english_teacher_ai_call_seconds_count{{{F}, outcome="error"}}[$__range])) or vector(0)) '
        f"/ sum(increase(english_teacher_ai_call_seconds_count{{{F}}}[$__range]))", instant=True),
        "percentunit", P, 1)),
    (4, stat("Latency p95 in range", prom(
        f"histogram_quantile(0.95, sum by (le) (increase(english_teacher_ai_call_seconds_bucket{{{F}, outcome=\"success\"}}[$__range])))",
        instant=True), "s", P, 1, "How long the provider took to answer (successful calls).")),
], 5)

cost_day = series("Cost per day by provider", [sql(
    f"SELECT date_trunc('day', created_at AT TIME ZONE '{TZ}') AT TIME ZONE '{TZ}' AS time, {PROVIDER} AS metric, "
    f"sum(cost_usd) AS cost FROM ai_usage WHERE $__timeFilter(created_at) AND {SQL_OPS} "
    f"GROUP BY 1, 2 ORDER BY 1")], "currencyUSD", PG, bars=True, stack=True,
    description="From Postgres (ai_usage), so it reaches back further than Prometheus. Set the range to 30 days.")
cost_op = {"type": "bargauge", "title": "Cost by operation (range)", "datasource": PG,
           "targets": [sql(f"SELECT operation, sum(cost_usd) AS cost FROM ai_usage WHERE $__timeFilter(created_at) "
                           f"AND {SQL_OPS} AND {SQL_PROMPT} GROUP BY 1 ORDER BY 2 DESC", "table")],
           "fieldConfig": {"defaults": {"unit": "currencyUSD", "decimals": 3, "min": 0,
                                        "color": {"mode": "continuous-BlYlRd"}}, "overrides": []},
           "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                       "reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^cost$/", "values": True}}}
place([(14, cost_day), (10, cost_op)], 10)

# ---------------------------------------------------------------- Calls, latency, errors
row("Calls, latency and errors")
place([
    (12, series("Calls by operation", [prom(
        f"sum by (operation) (increase(english_teacher_ai_call_seconds_count{{{F}}}[$__interval]))", "{{operation}}")],
        "short", P, bars=True, stack=True, interval="15m")),
    (12, series("Latency p95 by operation", [prom(
        f"histogram_quantile(0.95, sum by (operation, le) (increase(english_teacher_ai_call_seconds_bucket{{{F}, outcome=\"success\"}}[$__interval])))",
        "{{operation}}")], "s", P, interval="15m", points=True,
        description="Buckets 0.5 s to 120 s, so the value is an upper bound of the bucket.")),
], 9)
place([
    (12, series("Failed calls by kind", [prom(
        f'sum by (operation, error) (increase(english_teacher_ai_call_seconds_count{{{F}, outcome="error"}}[$__interval]))',
        "{{operation}}: {{error}}")], "short", P, bars=True, stack=True, interval="15m",
        description="credits, auth, rate_limit, overloaded, timeout, server_error, refused, incomplete, network, unexpected")),
    (12, series("Tokens by operation", [prom(
        f"sum by (operation, type) (increase(english_teacher_ai_tokens_total{{{F}}}[$__interval]))",
        "{{operation}} {{type}}")], "short", P, bars=True, stack=True, interval="15m")),
], 9)

# ---------------------------------------------------------------- Prompt versions
row("Prompt versions")
versions = {"type": "table", "title": "Prompt versions", "datasource": PG,
            "description": "Every prompt version seen in the range: when it first and last answered, how big its answers are, what it cost.",
            "targets": [sql(
                f"SELECT prompt AS \"Prompt\", operation AS \"Operation\", min(created_at) AS \"First seen\", "
                f"max(created_at) AS \"Last seen\", count(*) AS \"Calls\", round(avg(input_tokens)) AS \"Avg input tokens\", "
                f"round(avg(output_tokens)) AS \"Avg output tokens\", sum(cost_usd) AS \"Cost\", "
                f"sum(cost_usd) / count(*) AS \"Cost per call\" FROM ai_usage "
                f"WHERE $__timeFilter(created_at) AND prompt IS NOT NULL AND {SQL_OPS} AND {SQL_PROMPT} "
                f"GROUP BY 1, 2 ORDER BY 2, 3 DESC", "table")],
            "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": [
                {"matcher": {"id": "byName", "options": "Cost"}, "properties": [{"id": "unit", "value": "currencyUSD"}, {"id": "decimals", "value": 4}]},
                {"matcher": {"id": "byName", "options": "Cost per call"}, "properties": [{"id": "unit", "value": "currencyUSD"}, {"id": "decimals", "value": 5}]},
                {"matcher": {"id": "byRegexp", "options": "First seen|Last seen"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}]}]},
            "options": {"showHeader": True, "sortBy": [{"displayName": "Operation", "desc": False}]}}
place([(24, versions)], 8)
place([
    (12, series("Output tokens per call by prompt version", [prom(
        f'sum by (prompt) (increase(english_teacher_ai_tokens_total{{{F}, type="output"}}[$__interval])) '
        f'/ sum by (prompt) (increase(english_teacher_ai_call_seconds_count{{{F}, outcome="success"}}[$__interval]))',
        "{{prompt}}")], "short", P, interval="1h", points=True,
        description="Did editing a prompt make its answers longer or shorter? The dashed lines mark a new version.")),
    (12, series("Average latency by prompt version", [prom(
        f'sum by (prompt) (increase(english_teacher_ai_call_seconds_sum{{{F}, outcome="success"}}[$__interval])) '
        f'/ sum by (prompt) (increase(english_teacher_ai_call_seconds_count{{{F}, outcome="success"}}[$__interval]))',
        "{{prompt}}")], "s", P, interval="1h", points=True)),
], 9)

# ---------------------------------------------------------------- Logs
row("Logs")
line = ('{{.ai_operation}}  {{.ai_prompt}}  {{.ai_model}}  {{.ai_duration_ms}} ms  '
        'tokens {{.ai_input_tokens}}/{{.ai_output_tokens}}  {{.ai_cost_usd}} USD  {{.ai_response_id}}  trace={{.traceId}}')
place([
    (24, logs("AI calls", f'{LOGS} |= "AI call" | json | ai_operation=~"$operation" | ai_prompt=~"$prompt" '
                          f'| line_format "{line}"',
              "One line per call: operation, prompt version, model, time, tokens, cost, Anthropic response id, trace id. "
              "Copy a trace id into the Trace field above to see everything that request logged.")),
], 10)
place([
    (12, logs("Warnings and errors", f'{LOGS} | json | log_level=~"WARN|ERROR" | line_format "{{{{.log_level}}}} {{{{.log_logger}}}}: {{{{.message}}}}"')),
    (12, logs("Trace", '{namespace="english-teacher"} | json | traceId="$trace" | traceId!="" '
                       '| line_format "{{.process_thread_name}} {{.log_logger}}: {{.message}}"',
              "Paste a trace id into the Trace field: every log line of that request, background workers included.")),
], 10)

# ---------------------------------------------------------------- Backend
row("Backend")
http = f'{NS}, container="english-teacher-backend", uri!~"/actuator.*|/api/v1/health"'
place([
    (8, series("Requests / s", [prom(f"sum(rate(http_server_requests_seconds_count{{{http}}}[$__rate_interval]))", "requests")], "reqps", P)),
    (8, series("HTTP average latency by endpoint (top 8)", [prom(
        f"topk(8, sum by (uri) (rate(http_server_requests_seconds_sum{{{http}}}[$__rate_interval])) "
        f"/ sum by (uri) (rate(http_server_requests_seconds_count{{{http}}}[$__rate_interval])))",
        "{{uri}}")], "s", P, points=True)),
    (8, series("Heap used", [prom(f'sum(jvm_memory_used_bytes{{{NS}, area="heap"}})', "heap")], "bytes", P)),
], 8)


def var_query(name, label, query):
    return {"name": name, "label": label, "type": "query", "datasource": P,
            "query": {"query": query, "refId": name}, "definition": query, "refresh": 2, "sort": 1,
            "includeAll": True, "multi": True, "current": {"text": "All", "value": "$__all"}}


dashboard = {
    "title": "English Teacher: AI",
    "uid": "english-teacher-ai",
    "description": "Every AI call of english-teacher: cost, latency, failures, prompt versions and their logs.",
    "tags": ["english-teacher", "ai", "homelab"],
    "timezone": "browser",
    "schemaVersion": 41,
    "editable": True,
    "graphTooltip": 1,
    "time": {"from": "now-7d", "to": "now"},
    "refresh": "1m",
    "templating": {"list": [
        var_query("operation", "Operation", "label_values(english_teacher_ai_call_seconds_count, operation)"),
        var_query("prompt", "Prompt version", 'label_values(english_teacher_ai_call_seconds_count{operation=~"$operation"}, prompt)'),
        {"name": "trace", "label": "Trace", "type": "textbox", "query": "", "current": {"text": "", "value": ""}},
    ]},
    "annotations": {"list": [
        {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
         "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
        {"name": "New prompt version", "datasource": PG, "enable": True, "iconColor": "purple",
         "target": sql("SELECT min(created_at) AS time, 'New prompt version: ' || prompt AS text, operation AS tags "
                       "FROM ai_usage WHERE prompt IS NOT NULL GROUP BY prompt, operation "
                       "HAVING min(created_at) BETWEEN $__timeFrom() AND $__timeTo()", "table")},
    ]},
    "panels": panels,
}

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                          "dashboards", "english-teacher.json")
with open(out, "w") as f:
    json.dump(dashboard, f, indent=2)
    f.write("\n")
print("panels:", sum(1 for p in panels if p["type"] != "row"))
