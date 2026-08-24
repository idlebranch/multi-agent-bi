"use strict";

const ui = {
    askButton: document.getElementById("ask-button"),
    questionInput: document.getElementById("question-input"),
    queryMessage: document.getElementById("query-message"),
    answerTitle: document.getElementById("answer-title"),
    answerStatus: document.getElementById("answer-status"),
    answerContent: document.getElementById("answer-content"),
    clarificationOptions: document.getElementById("clarification-options"),
    summaryGrid: document.getElementById("summary-grid"),
    sqlCaption: document.getElementById("sql-caption"),
    sqlView: document.getElementById("sql-view"),
    copySqlButton: document.getElementById("copy-sql-button"),
    resultCaption: document.getElementById("result-caption"),
    resultContainer: document.getElementById("result-container"),
    runDetails: document.getElementById("run-details"),
    timeline: document.getElementById("timeline"),
    debugToggle: document.getElementById("debug-toggle"),
    debugCard: document.getElementById("debug-card"),
    rawTrace: document.getElementById("raw-trace"),
    rawRouting: document.getElementById("raw-routing"),
    rawHandoffs: document.getElementById("raw-handoffs"),
    rawPolicy: document.getElementById("raw-policy"),
    rawState: document.getElementById("raw-state"),
    copyTraceButton: document.getElementById("copy-trace-button"),
    copyRunButton: document.getElementById("copy-run-button"),
    serviceState: document.getElementById("service-state"),
    serviceStateText: document.getElementById("service-state-text"),
    databaseButton: document.getElementById("database-button"),
    databaseModal: document.getElementById("database-modal"),
    databaseClose: document.getElementById("database-close"),
    databaseContent: document.getElementById("database-content"),
    databaseRefresh: document.getElementById("database-refresh"),
    databaseCheckedAt: document.getElementById("database-checked-at"),
    toast: document.getElementById("toast"),
};

let isRunning = false;
let lastRun = null;
let lastHealth = null;
let toastTimer = null;

const RESPONSE_META = {
    success: {label: "成功", className: "status-success", title: "分析完成"},
    clarification: {label: "需要澄清", className: "status-info", title: "请明确分析指标"},
    out_of_scope: {label: "超出数据范围", className: "status-warning", title: "当前数据无法回答"},
    rejected: {label: "安全拒绝", className: "status-danger", title: "请求已被安全策略拒绝"},
    no_data: {label: "无匹配数据", className: "status-warning", title: "查询完成但没有记录"},
    failed: {label: "系统失败", className: "status-danger", title: "本次分析未完成"},
    pending: {label: "运行中", className: "status-info", title: "Agent 正在分析"},
};

const STATUS_LABELS = {
    succeeded: "通过",
    success: "成功",
    passed: "通过",
    failed: "失败",
    rejected: "拒绝",
    not_started: "未开始",
    no_match: "无匹配",
    clarification: "待澄清",
    out_of_scope: "超出范围",
    no_data: "无数据",
};

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function renderInlineMarkdown(value) {
    return escapeHtml(value)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/__(.+?)__/g, "<strong>$1</strong>")
        .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
        .replace(/\`([^\`\n]+)\`/g, "<code>$1</code>");
}

function renderSafeMarkdown(markdown) {
    const lines = String(markdown ?? "").replaceAll("\r\n", "\n").split("\n");
    const output = [];
    let listType = "";
    let inCode = false;
    let codeLines = [];

    const closeList = () => {
        if (listType) {
            output.push(`</${listType}>`);
            listType = "";
        }
    };

    for (const rawLine of lines) {
        const line = rawLine.trimEnd();
        if (line.trim().startsWith("```")) {
            closeList();
            if (inCode) {
                output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
                codeLines = [];
                inCode = false;
            } else {
                inCode = true;
            }
            continue;
        }
        if (inCode) {
            codeLines.push(rawLine);
            continue;
        }
        if (!line.trim()) {
            closeList();
            continue;
        }

        const unordered = line.match(/^\s*[-*]\s+(.+)$/);
        const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
        if (unordered || ordered) {
            const desired = unordered ? "ul" : "ol";
            if (listType !== desired) {
                closeList();
                listType = desired;
                output.push(`<${desired}>`);
            }
            output.push(`<li>${renderInlineMarkdown((unordered || ordered)[1])}</li>`);
            continue;
        }

        closeList();
        const heading = line.match(/^\s*#{1,3}\s+(.+)$/);
        if (heading) {
            output.push(`<p><strong>${renderInlineMarkdown(heading[1])}</strong></p>`);
        } else {
            output.push(`<p>${renderInlineMarkdown(line.trim())}</p>`);
        }
    }
    if (inCode) {
        output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    }
    closeList();
    return output.join("");
}

function formatNumber(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
        return String(value ?? "");
    }
    return new Intl.NumberFormat("zh-CN", {
        maximumFractionDigits: 4,
    }).format(value);
}

function formatDuration(milliseconds) {
    const value = Number(milliseconds || 0);
    if (value >= 1000) {
        return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} 秒`;
    }
    return `${value.toFixed(value >= 100 ? 0 : 1)} ms`;
}

function formatJson(value) {
    return JSON.stringify(value ?? null, null, 2);
}

function showToast(message) {
    ui.toast.textContent = message;
    ui.toast.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => ui.toast.classList.remove("visible"), 2200);
}

async function copyText(value, successMessage) {
    try {
        await navigator.clipboard.writeText(String(value ?? ""));
        showToast(successMessage);
    } catch {
        showToast("复制失败，请手动选择文本。");
    }
}

function setLoading(running) {
    isRunning = running;
    ui.askButton.disabled = running;
    ui.questionInput.disabled = running;
    ui.askButton.classList.toggle("loading", running);
    ui.askButton.querySelector(".button-label").textContent = running ? "分析中" : "问 Agent";
}

function setPendingState() {
    const meta = RESPONSE_META.pending;
    ui.queryMessage.textContent = "";
    ui.answerTitle.textContent = meta.title;
    ui.answerStatus.textContent = meta.label;
    ui.answerStatus.className = `answer-status ${meta.className}`;
    ui.answerContent.className = "answer-content muted";
    ui.answerContent.textContent = "正在执行输入防护、Schema Linking、SQL 审核和只读查询…";
    ui.clarificationOptions.innerHTML = "";
    ui.timeline.innerHTML = '<div class="empty-state">Agent 正在运行，请稍候…</div>';
}

function statusText(value) {
    return STATUS_LABELS[String(value)] || String(value || "—");
}

function renderAnswer(data) {
    const meta = RESPONSE_META[data.response_status] || RESPONSE_META.failed;
    ui.answerTitle.textContent = meta.title;
    ui.answerStatus.textContent = meta.label;
    ui.answerStatus.className = `answer-status ${meta.className}`;
    ui.answerContent.className = "answer-content";
    ui.answerContent.innerHTML = renderSafeMarkdown(data.final_answer || "没有生成可展示的回答。");

    ui.clarificationOptions.innerHTML = "";
    for (const option of data.clarification_options || []) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = option.label || "继续";
        button.addEventListener("click", () => {
            ui.questionInput.value = option.question || "";
            askAgent();
        });
        ui.clarificationOptions.appendChild(button);
    }
}

function renderSummary(data) {
    const tables = (data.relevant_tables || []).join("、") || "未选择";
    const values = [
        ["总耗时", formatDuration(data.total_duration_ms)],
        ["迭代", formatNumber(data.iteration)],
        ["相关表", tables],
        ["Reviewer", statusText(data.review_status)],
        ["Safety", statusText(data.validation_status)],
        ["Executor", statusText(data.execution_status)],
        ["返回行数", `${formatNumber(data.result_row_count)}${data.result_truncated ? "+" : ""}`],
        ["自动修复", data.auto_repaired ? `${data.repair_count} 次` : "未发生"],
    ];
    ui.summaryGrid.innerHTML = values
        .map(
            ([label, value]) =>
                `<div class="summary-item"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`
        )
        .join("");
}

function formatSql(sql) {
    if (!sql) {
        return "";
    }
    return String(sql)
        .replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|LEFT JOIN|RIGHT JOIN|INNER JOIN|JOIN|UNION ALL|UNION)\s+/gi, "\n$1 ")
        .replace(/\s+(AND|OR)\s+/gi, "\n  $1 ")
        .trim();
}

function sqlAbsenceReason(data) {
    if (data.response_status === "clarification") {
        return "问题需要先明确指标，因此没有生成 SQL。";
    }
    if (data.response_status === "rejected") {
        return "请求被输入安全策略拒绝，因此没有生成 SQL。";
    }
    if (data.response_status === "out_of_scope") {
        return "当前数据库不包含所需业务领域，因此没有生成 SQL。";
    }
    if (data.schema_status === "failed") {
        return "Schema Linking 未完成，因此没有生成 SQL。";
    }
    return "本次运行没有生成可执行 SQL。";
}

function renderSql(data) {
    if (data.sql) {
        ui.sqlCaption.textContent = "已生成并按只读流程审核";
        ui.sqlView.firstElementChild.textContent = formatSql(data.sql);
        ui.copySqlButton.disabled = false;
    } else {
        ui.sqlCaption.textContent = "未生成 SQL";
        ui.sqlView.firstElementChild.textContent = sqlAbsenceReason(data);
        ui.copySqlButton.disabled = true;
    }
}

function emptyResultMessage(data) {
    if (data.response_status === "clarification") {
        return "问题需要澄清，数据库尚未执行。";
    }
    if (data.response_status === "rejected") {
        return "请求被安全策略拒绝，数据库未执行任何语句。";
    }
    if (data.review_status === "failed") {
        return "SQL Reviewer 未通过，数据库没有执行该 SQL。";
    }
    if (data.validation_status === "failed") {
        return "SQL 被只读安全校验拒绝，数据库没有执行该 SQL。";
    }
    if (data.execution_status === "not_started") {
        return "数据库查询尚未执行。";
    }
    if (data.execution_status === "failed") {
        return "数据库查询失败，没有可展示的结果。";
    }
    if (data.execution_status === "succeeded" && data.result_row_count === 0) {
        return "查询正常完成，但在当前数据范围内没有匹配记录。";
    }
    return "没有可展示的查询结果。";
}

function renderResult(data) {
    const rows = data.sql_result || [];
    ui.resultCaption.textContent =
        data.execution_status === "succeeded"
            ? `返回 ${formatNumber(data.result_row_count)} 行${data.result_truncated ? "（结果已截断）" : ""}`
            : "尚无查询结果";

    if (!rows.length) {
        ui.resultContainer.innerHTML = `<div class="empty-state">${escapeHtml(emptyResultMessage(data))}</div>`;
        return;
    }

    const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
    const head = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
    const body = rows
        .map((row) => {
            const cells = columns
                .map((column) => `<td>${escapeHtml(formatNumber(row[column]))}</td>`)
                .join("");
            return `<tr>${cells}</tr>`;
        })
        .join("");
    ui.resultContainer.innerHTML =
        `<table class="result-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderRunDetails(data) {
    const feedback = data.review_feedback || "没有 Reviewer 补充意见";
    const asOfDate = data.run_state?.as_of_date || "—";
    const details = [
        ["Run ID", data.run_id || "—"],
        ["正式版本", data.version || "Production"],
        ["数据库业务日期", asOfDate],
        ["策略版本", data.policy_decisions?.[0]?.policy_version || "—"],
        ["相关字段", formatJson(data.relevant_columns || {})],
        ["Reviewer 意见", feedback],
    ];
    ui.runDetails.innerHTML = details
        .map(
            ([label, value]) =>
                `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`
        )
        .join("");
}

function timelineClass(status) {
    const value = String(status || "");
    if (["succeeded", "success", "passed"].includes(value)) return "success";
    if (["rejected"].includes(value)) return "rejected";
    if (["failed"].includes(value)) return "failure";
    if (["no_match", "clarification", "out_of_scope", "no_data"].includes(value)) return "warning";
    return "";
}

function renderTimeline(data) {
    const timeline = data.timeline || [];
    if (!timeline.length) {
        ui.timeline.innerHTML = '<div class="empty-state">没有可展示的 Agent 时间线。</div>';
        return;
    }
    ui.timeline.innerHTML = timeline
        .map((event) => {
            const details = event.details && Object.keys(event.details).length
                ? `<details><summary>查看节点详情</summary><pre class="timeline-detail">${escapeHtml(formatJson(event.details))}</pre></details>`
                : "";
            const attempt = Number(event.attempt || 1) > 1
                ? ` · 第 ${escapeHtml(event.attempt)} 次`
                : "";
            return `
                <div class="timeline-item ${timelineClass(event.status)}">
                    <span class="timeline-marker" aria-hidden="true"></span>
                    <div class="timeline-topline">
                        <span class="timeline-label">${escapeHtml(event.label)}${attempt}</span>
                        <span class="timeline-time">${escapeHtml(formatDuration(event.duration_ms))}</span>
                    </div>
                    <p class="timeline-summary">${escapeHtml(event.summary)} · ${escapeHtml(statusText(event.status))}</p>
                    ${details}
                </div>`;
        })
        .join("");
}

function renderDebug(data) {
    ui.rawTrace.textContent = formatJson(data.trace);
    ui.rawRouting.textContent = formatJson(data.routing_history);
    ui.rawHandoffs.textContent = formatJson(data.handoff_history);
    ui.rawPolicy.textContent = formatJson(data.policy_decisions);
    ui.rawState.textContent = formatJson(data.run_state);
    ui.copyTraceButton.disabled = false;
    ui.copyRunButton.disabled = false;
}

function renderRun(data) {
    lastRun = data;
    renderAnswer(data);
    renderSummary(data);
    renderSql(data);
    renderResult(data);
    renderRunDetails(data);
    renderTimeline(data);
    renderDebug(data);
}

async function askAgent() {
    if (isRunning) return;
    const question = ui.questionInput.value.trim();
    if (!question) {
        ui.queryMessage.textContent = "请输入一个数据分析问题。";
        ui.questionInput.focus();
        return;
    }

    setLoading(true);
    setPendingState();
    try {
        const response = await fetch("/ask", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({question}),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error("服务未接受该请求");
        }
        renderRun(payload);
    } catch (error) {
        console.error("BI Agent request failed", error);
        renderAnswer({
            response_status: "failed",
            final_answer: "暂时无法连接 BI Agent 服务，请检查右上角服务状态后重试。",
            clarification_options: [],
        });
        ui.timeline.innerHTML = '<div class="empty-state">请求未进入 Agent 工作流。</div>';
    } finally {
        setLoading(false);
    }
}

function activateTab(panelId) {
    document.querySelectorAll(".tab").forEach((tab) => {
        tab.classList.toggle("active", tab.dataset.tab === panelId);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.classList.toggle("active", panel.id === panelId);
    });
}

function renderDatabaseHealth(health) {
    const database = health.database || {};
    const dateRange = database.date_range || [];
    const healthItems = [
        ["服务状态", health.status === "ok" ? "正常" : "降级"],
        ["Agent", health.agent_ready ? "就绪" : "未就绪"],
        ["数据库", database.database_label || (database.status === "ready" ? "可访问" : "不可访问")],
        ["数据库引擎", database.backend === "postgresql" ? "PostgreSQL" : "—"],
        ["服务端版本", database.server_version || "—"],
        ["数据库大小", database.size_mib != null ? `${formatNumber(database.size_mib)} MiB` : "—"],
        ["只读连接", database.read_only ? "是" : "否"],
        ["查询超时", database.statement_timeout || "—"],
        ["业务日期", dateRange.filter(Boolean).join(" 至 ") || database.as_of_date || "—"],
        ["正式版本", `${health.mode || "Production"} ${health.version || ""}`.trim()],
    ];
    const cards = healthItems
        .map(
            ([label, value]) =>
                `<div class="health-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
        )
        .join("");
    const counts = {
        ...(database.table_counts || {}),
        ...(database.semantic_table_counts || {}),
    };
    const rows = Object.entries(counts)
        .map(
            ([table, count]) =>
                `<tr><td>${escapeHtml(table)}</td><td>${escapeHtml(formatNumber(count))}</td></tr>`
        )
        .join("");
    ui.databaseContent.innerHTML =
        `<div class="health-grid">${cards}</div>` +
        (rows
            ? `<table class="health-table"><caption>实际表与语义层行数</caption><thead><tr><th>表</th><th>行数</th></tr></thead><tbody>${rows}</tbody></table>`
            : "");

    const checkedAt = Number(database.checked_at_epoch || 0);
    ui.databaseCheckedAt.textContent = checkedAt
        ? `检查时间：${new Date(checkedAt * 1000).toLocaleString("zh-CN")}`
        : "尚无检查时间";
}

async function loadHealth(forceRefresh = false) {
    try {
        const response = await fetch(`/health${forceRefresh ? "?refresh=true" : ""}`, {
            cache: "no-store",
        });
        const health = await response.json();
        if (!response.ok) throw new Error("health request failed");
        lastHealth = health;
        ui.serviceState.className =
            health.status === "ok" ? "service-state ready" : "service-state degraded";
        ui.serviceStateText.textContent = health.agent_ready ? "服务与 Agent 已就绪" : "服务已启动，Agent 未完全就绪";
        renderDatabaseHealth(health);
        return health;
    } catch (error) {
        console.error("Health check failed", error);
        ui.serviceState.className = "service-state degraded";
        ui.serviceStateText.textContent = "服务状态不可用";
        ui.databaseContent.innerHTML =
            '<div class="empty-state">无法读取健康状态，请确认本地服务正在运行。</div>';
        return null;
    }
}

function setDebugMode(enabled) {
    ui.debugToggle.checked = enabled;
    ui.debugCard.hidden = !enabled;
    localStorage.setItem("bi-agent-debug", enabled ? "1" : "0");
}

document.querySelectorAll(".case-group button").forEach((button) => {
    button.addEventListener("click", () => {
        ui.questionInput.value = button.dataset.question || "";
        document.getElementById("case-menu").open = false;
        ui.questionInput.focus();
    });
});

document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
});

ui.askButton.addEventListener("click", askAgent);
ui.questionInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        askAgent();
    }
});
ui.debugToggle.addEventListener("change", () => setDebugMode(ui.debugToggle.checked));
ui.copySqlButton.addEventListener("click", () => copyText(lastRun?.sql, "SQL 已复制"));
ui.copyTraceButton.addEventListener("click", () => copyText(formatJson(lastRun?.trace), "Trace 已复制"));
ui.copyRunButton.addEventListener("click", () => copyText(formatJson(lastRun), "运行 JSON 已复制"));

ui.databaseButton.addEventListener("click", async () => {
    ui.databaseModal.hidden = false;
    if (!lastHealth) await loadHealth(false);
});
ui.databaseClose.addEventListener("click", () => {
    ui.databaseModal.hidden = true;
});
ui.databaseModal.addEventListener("click", (event) => {
    if (event.target === ui.databaseModal) ui.databaseModal.hidden = true;
});
ui.databaseRefresh.addEventListener("click", async () => {
    ui.databaseRefresh.disabled = true;
    ui.databaseRefresh.textContent = "刷新中…";
    await loadHealth(true);
    ui.databaseRefresh.disabled = false;
    ui.databaseRefresh.textContent = "手动刷新";
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") ui.databaseModal.hidden = true;
});

setDebugMode(localStorage.getItem("bi-agent-debug") === "1");
loadHealth(false);
