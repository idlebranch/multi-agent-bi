"use strict";

const debugPort = Number(process.argv[2] || 9223);
const appUrl = process.argv[3] || "http://127.0.0.1:8000/";
const debuggerBase = `http://127.0.0.1:${debugPort}`;

async function main() {
    const target = await fetch(
        `${debuggerBase}/json/new?${encodeURIComponent(appUrl)}`,
        {method: "PUT"},
    ).then((response) => response.json());
    const socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
        socket.addEventListener("open", resolve, {once: true});
        socket.addEventListener("error", reject, {once: true});
    });

    let nextId = 1;
    const pending = new Map();
    const browserErrors = [];
    socket.addEventListener("message", (event) => {
        const message = JSON.parse(event.data);
        if (message.id && pending.has(message.id)) {
            const {resolve, reject} = pending.get(message.id);
            pending.delete(message.id);
            if (message.error) reject(new Error(message.error.message));
            else resolve(message.result);
            return;
        }
        if (message.method === "Runtime.exceptionThrown") {
            browserErrors.push(message.params.exceptionDetails.text);
        }
        if (
            message.method === "Log.entryAdded"
            && message.params.entry.level === "error"
        ) {
            browserErrors.push(message.params.entry.text);
        }
    });

    function send(method, params = {}) {
        const id = nextId++;
        return new Promise((resolve, reject) => {
            pending.set(id, {resolve, reject});
            socket.send(JSON.stringify({id, method, params}));
        });
    }

    await send("Runtime.enable");
    await send("Log.enable");
    const expression = `
        (async () => {
            await new Promise((resolve) => setTimeout(resolve, 800));
            const serviceReady = document.getElementById("service-state-text")
                .textContent.includes("就绪");
            document.getElementById("database-button").click();
            await new Promise((resolve) => setTimeout(resolve, 1200));
            const modal = document.getElementById("database-modal");
            const databaseText = document.getElementById("database-content").textContent;
            const debugToggle = document.getElementById("debug-toggle");
            debugToggle.click();
            const debugCard = document.getElementById("debug-card");
            const resultTab = document.querySelector('[data-tab="result-panel"]');
            resultTab.click();
            const markdown = renderSafeMarkdown(
                "**health_beauty**\\n\\n<img src=x onerror=alert(1)>"
            );
            const workspaceColumns = getComputedStyle(
                document.querySelector(".workspace")
            ).gridTemplateColumns;
            return {
                serviceReady,
                databaseModalVisible: !modal.hidden,
                databaseHasActualOrders: databaseText.includes("99,441"),
                databaseHasActualItems: databaseText.includes("112,650"),
                debugVisible: !debugCard.hidden,
                debugPersisted: localStorage.getItem("bi-agent-debug") === "1",
                rawSectionsCollapsed: [...debugCard.querySelectorAll("details")]
                    .every((item) => !item.open),
                resultTabActivated: document.getElementById("result-panel")
                    .classList.contains("active"),
                markdownBold: markdown.includes("<strong>health_beauty</strong>"),
                markdownEscapesHtml: markdown.includes("&lt;img")
                    && !markdown.includes("<img"),
                noHorizontalOverflow: document.body.scrollWidth
                    <= document.documentElement.clientWidth + 1,
                responsiveSingleColumn: workspaceColumns.trim().split(/\\s+/).length === 1,
                viewportWidth: window.innerWidth,
                workspaceColumns,
            };
        })()
    `;
    const evaluated = await send("Runtime.evaluate", {
        expression,
        awaitPromise: true,
        returnByValue: true,
    });
    const checks = evaluated.result.value;
    checks.browserErrorCount = browserErrors.length;
    checks.browserErrors = browserErrors;
    socket.close();

    const failed = Object.entries(checks)
        .filter(([key, value]) => (
            !["viewportWidth", "workspaceColumns", "browserErrors"].includes(key)
            && (key === "browserErrorCount" ? value !== 0 : value !== true)
        ))
        .map(([key]) => key);
    console.log(JSON.stringify({passed: failed.length === 0, failed, checks}, null, 2));
    if (failed.length) process.exitCode = 1;
}

main().catch((error) => {
    console.error(JSON.stringify({passed: false, error: error.message}));
    process.exitCode = 1;
});
