// State
let currentModalBook = null;
let selectedServer = "ebook";

// Navigation
document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", (e) => {
        e.preventDefault();
        document.querySelectorAll(".nav-link").forEach((l) => l.classList.remove("active"));
        link.classList.add("active");
        document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
        const pageId = "page-" + link.dataset.page;
        document.getElementById(pageId).classList.add("active");
        if (link.dataset.page === "requests") loadRequests();
        if (link.dataset.page === "settings") loadConfig();
    });
});

// Search
const searchInput = document.getElementById("search-input");
const searchBtn = document.getElementById("search-btn");

searchBtn.addEventListener("click", doSearch);
searchInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

let searchTimeout;
searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(doSearch, 500);
});

async function doSearch() {
    const query = searchInput.value.trim();
    const grid = document.getElementById("search-results");

    if (!query) {
        grid.innerHTML = '<div class="empty-state">Search for books by title, author, or ISBN</div>';
        return;
    }

    grid.innerHTML = '<div class="empty-state"><div class="spinner"></div> Searching...</div>';

    try {
        const resp = await fetch("/api/search?q=" + encodeURIComponent(query));
        const data = await resp.json();
        if (data.error) {
            grid.innerHTML = `<div class="empty-state">Error: ${data.error}</div>`;
            return;
        }
        if (!data.length) {
            grid.innerHTML = '<div class="empty-state">No results found</div>';
            return;
        }
        grid.innerHTML = data.map(renderBookCard).join("");
        grid.querySelectorAll(".book-card").forEach((card) => {
            card.addEventListener("click", () => openDownloadModal(JSON.parse(card.dataset.book)));
        });
    } catch (err) {
        grid.innerHTML = `<div class="empty-state">Error: ${err.message}</div>`;
    }
}

function renderBookCard(book) {
    const title = book.title || "Unknown Title";
    const author = book.author?.authorName || "Unknown Author";
    const year = book.releaseDate ? book.releaseDate.substring(0, 4) : "";
    let cover = "";
    if (book.author?.images?.length) cover = book.author.images[0].url;
    if (!cover && book.images?.length) cover = book.images[0].url;
    if (!cover) cover = "https://via.placeholder.com/200x300/2d2030/e85d9a?text=No+Cover";
    const bookJson = JSON.stringify(book).replace(/"/g, "&quot;");

    return `
        <div class="book-card" data-book="${bookJson}">
            <img class="book-cover" src="${cover}" alt="${title}" loading="lazy"
                 onerror="this.src='https://via.placeholder.com/200x300/2d2030/e85d9a?text=No+Cover'">
            <div class="book-info">
                <div class="book-title" title="${title}">${title}</div>
                <div class="book-author">${author}</div>
                ${year ? `<div class="book-year">${year}</div>` : ""}
            </div>
        </div>`;
}

// Download Modal
async function openDownloadModal(book) {
    currentModalBook = book;
    selectedServer = "ebook";

    document.getElementById("modal-title").textContent = "Download: " + (book.title || "Unknown");
    document.querySelectorAll(".server-btn").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.server === selectedServer);
        btn.onclick = () => selectServer(btn.dataset.server);
    });

    document.getElementById("download-modal").classList.add("active");
    await loadModalOptions(selectedServer);
}

function closeModal() {
    document.getElementById("download-modal").classList.remove("active");
    currentModalBook = null;
}

async function selectServer(server) {
    selectedServer = server;
    document.querySelectorAll(".server-btn").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.server === server);
    });
    await loadModalOptions(server);
}

async function loadModalOptions(server) {
    const profileSelect = document.getElementById("quality-profile");
    const folderSelect = document.getElementById("root-folder");
    profileSelect.innerHTML = '<option>Loading...</option>';
    folderSelect.innerHTML = '<option>Loading...</option>';

    try {
        const [profilesResp, foldersResp] = await Promise.all([
            fetch("/api/profiles/" + server),
            fetch("/api/rootfolders/" + server),
        ]);
        const profiles = await profilesResp.json();
        const folders = await foldersResp.json();

        if (profiles.error) {
            profileSelect.innerHTML = `<option disabled>${profiles.error}</option>`;
        } else {
            profileSelect.innerHTML = profiles
                .map((p) => `<option value="${p.id}">${p.name}</option>`)
                .join("");
        }

        if (folders.error) {
            folderSelect.innerHTML = `<option disabled>${folders.error}</option>`;
        } else {
            folderSelect.innerHTML = folders
                .map((f) => `<option value="${f.path}">${f.path}</option>`)
                .join("");
        }
    } catch (err) {
        profileSelect.innerHTML = '<option disabled>Error loading</option>';
        folderSelect.innerHTML = '<option disabled>Error loading</option>';
    }
}

document.getElementById("confirm-download-btn").addEventListener("click", async () => {
    if (!currentModalBook) return;

    const btn = document.getElementById("confirm-download-btn");
    btn.disabled = true;
    btn.textContent = "Sending...";

    const qualityProfileId = parseInt(document.getElementById("quality-profile").value);
    const rootFolder = document.getElementById("root-folder").value;

    if (!qualityProfileId || !rootFolder) {
        alert("Please select a quality profile and root folder.");
        btn.disabled = false;
        btn.textContent = "Download";
        return;
    }

    try {
        const resp = await fetch("/api/request", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                server_type: selectedServer,
                book: currentModalBook,
                quality_profile_id: qualityProfileId,
                root_folder: rootFolder,
            }),
        });
        const data = await resp.json();
        if (data.error) {
            alert("Error: " + data.error);
        } else {
            closeModal();
            // Switch to requests page
            document.querySelector('[data-page="requests"]').click();
        }
    } catch (err) {
        alert("Error: " + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "Download";
    }
});

// Requests
async function loadRequests() {
    const list = document.getElementById("requests-list");
    try {
        const resp = await fetch("/api/requests");
        const data = await resp.json();
        if (!data.length) {
            list.innerHTML = '<div class="empty-state">No requests yet. Search for books and download them!</div>';
            return;
        }
        list.innerHTML = data.map(renderRequest).join("");
        list.querySelectorAll(".delete-btn").forEach((btn) => {
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                const id = parseInt(btn.dataset.id);
                await fetch("/api/requests/" + id, { method: "DELETE" });
                loadRequests();
            });
        });
    } catch (err) {
        list.innerHTML = `<div class="empty-state">Error loading requests</div>`;
    }
}

function renderRequest(req) {
    const cover = req.cover_url || "https://via.placeholder.com/50x75/2d2030/e85d9a?text=N/A";
    const statusClass = req.status;
    const progress = req.progress || 0;
    const fillClass = req.status === "completed" ? "complete" : req.status === "error" ? "error" : "";

    return `
        <div class="request-item">
            <img class="request-cover" src="${cover}" alt="${req.title}"
                 onerror="this.src='https://via.placeholder.com/50x75/2d2030/e85d9a?text=N/A'">
            <div class="request-details">
                <div class="request-title">${req.title}</div>
                <div class="request-meta">${req.author || ""}</div>
                <span class="request-server ${req.server_type}">${req.server_type}</span>
            </div>
            <div class="request-status">
                <span class="status-label ${statusClass}">${req.status}</span>
                ${req.status !== "error" ? `
                    <div class="progress-bar">
                        <div class="progress-fill ${fillClass}" style="width: ${progress}%"></div>
                    </div>` : ""}
                ${req.error ? `<div class="request-error" title="${req.error}">${req.error}</div>` : ""}
                <button class="btn btn-small btn-danger delete-btn" data-id="${req.id}" style="margin-top: 0.4rem">Remove</button>
            </div>
        </div>`;
}

document.getElementById("refresh-btn").addEventListener("click", async () => {
    const btn = document.getElementById("refresh-btn");
    btn.disabled = true;
    btn.textContent = "Refreshing...";
    try {
        await fetch("/api/requests/refresh", { method: "POST" });
        await loadRequests();
    } finally {
        btn.disabled = false;
        btn.textContent = "Refresh Status";
    }
});

// Settings
async function loadConfig() {
    try {
        const resp = await fetch("/api/config");
        const data = await resp.json();
        document.getElementById("ebook-url").value = data.ebook.url || "";
        document.getElementById("ebook-api").value = data.ebook.api_key || "";
        document.getElementById("audiobook-url").value = data.audiobook.url || "";
        document.getElementById("audiobook-api").value = data.audiobook.api_key || "";
    } catch (err) {
        console.error("Failed to load config", err);
    }
}

window.saveConfig = async function (type) {
    const url = document.getElementById(type + "-url").value;
    const api_key = document.getElementById(type + "-api").value;
    const statusEl = document.getElementById(type + "-status");

    try {
        const resp = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ server_type: type, url, api_key }),
        });
        const data = await resp.json();
        statusEl.className = "status-msg success";
        statusEl.textContent = "Configuration saved!";
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }

    setTimeout(() => { statusEl.textContent = ""; }, 3000);
};

window.testConnection = async function (type) {
    const url = document.getElementById(type + "-url").value;
    const api_key = document.getElementById(type + "-api").value;
    const statusEl = document.getElementById(type + "-status");

    statusEl.className = "status-msg";
    statusEl.textContent = "Testing...";

    try {
        const resp = await fetch("/api/config/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url, api_key }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.className = "status-msg success";
            statusEl.textContent = "Connected! Version: " + (data.status.version || "unknown");
        } else {
            statusEl.className = "status-msg error";
            statusEl.textContent = "Failed: " + data.error;
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
};

// Close modal on background click
document.getElementById("download-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
});

// Load config on page load
loadConfig();
