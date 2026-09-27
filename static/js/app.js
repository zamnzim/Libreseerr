// State
let currentModalBook = null;
let selectedServer = "ebook";
let currentUser = null;
let editingUsername = null;
let cachedAvailability = null;

const DISCOVERY_CATEGORIES = [
    { key: "new_releases", title: "New Releases" },
    { key: "trending", title: "Trending" },
    { key: "best_sellers", title: "Best Sellers" },
    { key: "fiction", title: "Popular Fiction" },
    { key: "science_fiction", title: "Science Fiction" },
    { key: "mystery", title: "Mystery & Thriller" },
    { key: "fantasy", title: "Fantasy" },
    { key: "romance", title: "Romance" },
    { key: "nonfiction", title: "Non-Fiction" },
    { key: "history", title: "History" },
    { key: "classics", title: "Classics" },
];

// ─── Auth ───

async function loadCurrentUser() {
    try {
        const resp = await fetch("/api/auth/me");
        if (resp.status === 401) {
            window.location.href = "/login";
            return;
        }
        currentUser = await resp.json();

        // Show admin-only elements if user is admin
        if (currentUser.role === "admin") {
            document.body.classList.add("is-admin");
        }

        // Set sidebar user info
        document.getElementById("sidebar-username").textContent = currentUser.username;
        document.getElementById("sidebar-role").textContent = currentUser.role;
    } catch (err) {
        window.location.href = "/login";
    }
}

async function doLogout() {
    try {
        await fetch("/api/auth/logout", { method: "POST" });
    } catch (err) {
        // ignore
    }
    window.location.href = "/login";
}

// ─── 401 Interceptor ───

const originalFetch = window.fetch;
window.fetch = async function (...args) {
    const resp = await originalFetch.apply(this, args);
    if (resp.status === 401) {
        window.location.href = "/login";
    }
    return resp;
};

// ─── Sidebar ───

function openSidebar() {
    document.getElementById("sidebar").classList.add("open");
    document.getElementById("sidebar-overlay").classList.add("active");
}

function closeSidebar() {
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("sidebar-overlay").classList.remove("active");
}

// ─── Navigation ───

document.querySelectorAll(".sidebar-link").forEach((link) => {
    link.addEventListener("click", (e) => {
        e.preventDefault();
        document.querySelectorAll(".sidebar-link").forEach((l) => l.classList.remove("active"));
        link.classList.add("active");
        document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
        const pageId = "page-" + link.dataset.page;
        document.getElementById(pageId).classList.add("active");
        if (link.dataset.page === "requests") loadRequests();
        if (link.dataset.page === "settings") loadConfig();
        if (link.dataset.page === "users") { loadUsers(); loadLDAP(); loadOIDC(); }
        closeSidebar();
    });
});

// ─── Search ───

const searchInput = document.getElementById("search-input");

searchInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

let searchTimeout;
searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(doSearch, 500);
});

// Search options (field / sort / English / hide coverless), paging, remembered prefs.
const searchState = { query: "", page: 1, total: 0, loaded: 0, seq: 0 };
const PREFS_KEY = "libreseerr-search-prefs";

function loadSearchPrefs() {
    try {
        const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
        if (p.sort) document.getElementById("search-sort").value = p.sort;
        if (typeof p.english === "boolean") document.getElementById("search-english").checked = p.english;
        if (typeof p.covers === "boolean") document.getElementById("search-covers").checked = p.covers;
        if (p.field) setSearchField(p.field);
    } catch { /* storage unavailable: keep defaults */ }
}

function saveSearchPrefs() {
    try {
        localStorage.setItem(PREFS_KEY, JSON.stringify({
            sort: document.getElementById("search-sort").value,
            english: document.getElementById("search-english").checked,
            covers: document.getElementById("search-covers").checked,
            field: currentSearchField(),
        }));
    } catch { /* ignore */ }
}

function currentSearchField() {
    return document.querySelector("#search-field .seg-btn.active")?.dataset.field || "all";
}

function setSearchField(field) {
    document.querySelectorAll("#search-field .seg-btn").forEach((b) => {
        b.classList.toggle("active", b.dataset.field === field);
    });
}

document.querySelectorAll("#search-field .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        setSearchField(btn.dataset.field);
        saveSearchPrefs();
        doSearch();
    });
});
["search-sort", "search-english"].forEach((id) => {
    document.getElementById(id).addEventListener("change", () => { saveSearchPrefs(); doSearch(); });
});
// Hiding coverless books is purely visual; no need to re-query.
document.getElementById("search-covers").addEventListener("change", () => {
    saveSearchPrefs();
    applyCoverFilter();
});
document.getElementById("load-more-btn").addEventListener("click", () => fetchSearchPage(true));

function applyCoverFilter() {
    const hide = document.getElementById("search-covers").checked;
    document.getElementById("search-results").classList.toggle("hide-coverless", hide);
}

function setSearchMode(on) {
    document.getElementById("discovery-content").style.display = on ? "none" : "";
    document.getElementById("search-results").style.display = on ? "" : "none";
    document.getElementById("search-toolbar").style.display = on ? "" : "none";
    if (!on) document.getElementById("search-more").style.display = "none";
}

async function doSearch() {
    const query = searchInput.value.trim();

    // Make sure we're on the search page
    if (!document.getElementById("page-search").classList.contains("active")) {
        document.querySelector('[data-page="search"]').click();
    }

    if (!query) {
        loadDiscovery();
        return;
    }

    setSearchMode(true);
    searchState.query = query;
    searchState.page = 1;
    searchState.loaded = 0;
    await fetchSearchPage(false);
}

async function fetchSearchPage(append) {
    const grid = document.getElementById("search-results");
    const more = document.getElementById("search-more");
    const moreBtn = document.getElementById("load-more-btn");
    const count = document.getElementById("search-count");
    const seq = ++searchState.seq;   // drop responses from superseded searches
    const page = append ? searchState.page + 1 : 1;

    if (append) {
        moreBtn.disabled = true;
        moreBtn.textContent = "Loading...";
    } else {
        grid.innerHTML = '<div class="empty-state"><div class="spinner"></div> Searching...</div>';
        more.style.display = "none";
        count.textContent = "";
    }

    const params = new URLSearchParams({
        q: searchState.query,
        field: currentSearchField(),
        sort: document.getElementById("search-sort").value,
        english: document.getElementById("search-english").checked ? "1" : "0",
        page: String(page),
    });

    try {
        const resp = await fetch("/api/search?" + params);
        const data = await resp.json();
        if (seq !== searchState.seq) return;
        if (data.error) {
            if (!append) grid.innerHTML = `<div class="empty-state">Error: ${escapeHtml(data.error)}</div>`;
            return;
        }
        const results = data.results || [];
        searchState.page = page;
        searchState.total = data.total || 0;

        if (!append && !results.length) {
            grid.innerHTML = '<div class="empty-state">No results found. Check the spelling, or try searching by Title or Author.</div>';
            return;
        }
        const html = results.map(renderBookCard).join("");
        if (append) grid.insertAdjacentHTML("beforeend", html);
        else grid.innerHTML = html;
        searchState.loaded += results.length;

        grid.querySelectorAll(".book-card:not([data-bound])").forEach((card) => {
            card.dataset.bound = "1";
            card.addEventListener("click", () => openDownloadModal(JSON.parse(card.dataset.book)));
        });
        applyCoverFilter();
        count.textContent = `Showing ${searchState.loaded.toLocaleString()} of ${searchState.total.toLocaleString()}`;
        more.style.display = searchState.loaded < searchState.total && results.length ? "" : "none";
        fetchAvailability().then(applyAvailabilityBadges);
    } catch (err) {
        if (seq === searchState.seq && !append) {
            grid.innerHTML = `<div class="empty-state">Error: ${escapeHtml(err.message)}</div>`;
        }
    } finally {
        moreBtn.disabled = false;
        moreBtn.textContent = "Load more";
    }
}

// Everything rendered here comes from Open Library, a crowd-edited catalog, and is
// interpolated into innerHTML, so it must be escaped (#20).
function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
}

// Generated locally so it can never fail to load. The previous fallback pointed at
// via.placeholder.com, which is offline: the fallback itself errored, onerror fired
// again, and every coverless card flashed between a broken image and its alt text.
function coverPlaceholder(title) {
    const words = String(title || "No cover").split(/\s+/);
    const lines = [];
    let line = "";
    for (const w of words) {
        if (line && (line + " " + w).length > 14) {
            lines.push(line);
            line = w;
            if (lines.length === 5) break;
        } else {
            line = (line + " " + w).trim();
        }
    }
    if (line && lines.length < 5) lines.push(line);
    const text = lines.map((l, i) =>
        `<text x="100" y="${120 + i * 24}" text-anchor="middle">${escapeHtml(l.length > 16 ? l.slice(0, 15) + "…" : l)}</text>`).join("");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300" viewBox="0 0 200 300">` +
        `<rect width="200" height="300" fill="#1f2937"/>` +
        `<rect x="12" y="12" width="176" height="276" fill="none" stroke="#ec4899" stroke-opacity=".45" stroke-width="2" rx="6"/>` +
        `<g font-family="Inter,sans-serif" font-size="17" font-weight="600" fill="#f3f4f6">${text}</g>` +
        `<text x="100" y="270" text-anchor="middle" font-family="Inter,sans-serif" font-size="12" fill="#9ca3af">No cover</text></svg>`;
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

// onerror handler: detaches itself first so it can only ever run once per image.
function coverFail(img) {
    img.onerror = null;
    img.src = coverPlaceholder(img.dataset.title || img.alt);
    img.closest(".book-card")?.classList.add("no-cover");
}

function renderBookCard(book) {
    const title = book.title || "Unknown Title";
    const author = book.author?.authorName || (Array.isArray(book.authors) ? book.authors.join(", ") : "Unknown Author");
    const year = book.releaseDate ? book.releaseDate.substring(0, 4) : book.publishedDate ? book.publishedDate.substring(0, 4) : "";
    let cover = "";
    if (book.author?.images?.length) cover = book.author.images[0].url;
    if (!cover && book.images?.length) cover = book.images[0].url;
    if (!cover && book.cover) cover = book.cover;
    const hasCover = !!cover;
    if (!cover) cover = coverPlaceholder(title);
    const bookJson = escapeHtml(JSON.stringify(book));
    const meta = [year, book.rating ? `★ ${book.rating}` : "", book.editions > 1 ? `${book.editions} editions` : ""]
        .filter(Boolean).join(" · ");

    return `
        <div class="book-card${hasCover ? "" : " no-cover"}" data-book="${bookJson}">
            <img class="book-cover" src="${escapeHtml(cover)}" alt="${escapeHtml(title)}" data-title="${escapeHtml(title)}"
                 loading="lazy" onerror="coverFail(this)">
            <div class="book-overlay">
                <div class="book-overlay-title">${escapeHtml(title)}</div>
                <div class="book-overlay-author">${escapeHtml(author)}${year ? " (" + escapeHtml(year) + ")" : ""}</div>
            </div>
            <div class="book-info">
                <div class="book-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
                <div class="book-author">${escapeHtml(author)}</div>
                ${meta ? `<div class="book-year">${escapeHtml(meta)}</div>` : ""}
            </div>
        </div>`;
}

async function fetchAvailability() {
    if (cachedAvailability) return cachedAvailability;
    try {
        const resp = await fetch("/api/availability");
        cachedAvailability = await resp.json();
    } catch {
        cachedAvailability = { ebook: { isbns: [], titles: [] }, audiobook: { isbns: [], titles: [] } };
    }
    return cachedAvailability;
}

function applyAvailabilityBadges(availability) {
    document.querySelectorAll(".book-card").forEach((card) => {
        if (card.querySelector(".book-badges")) return;
        let book;
        try { book = JSON.parse(card.dataset.book); } catch { return; }
        const isbn = book.isbn_13 || book.isbn_10 || book.isbn13 || book.isbn10 || "";
        const title = (book.title || "").toLowerCase();

        const hasEbook = (isbn && availability.ebook.isbns.includes(isbn)) ||
            (title && availability.ebook.titles.includes(title));
        const hasAudiobook = (isbn && availability.audiobook.isbns.includes(isbn)) ||
            (title && availability.audiobook.titles.includes(title));
        const ebookRequested = availability.ebook_requests &&
            ((isbn && availability.ebook_requests.isbns.includes(isbn)) ||
            (title && availability.ebook_requests.titles.includes(title)));
        const audiobookRequested = availability.audiobook_requests &&
            ((isbn && availability.audiobook_requests.isbns.includes(isbn)) ||
            (title && availability.audiobook_requests.titles.includes(title)));

        // "Requested" takes priority over "available" for the same server type
        const showEbook = ebookRequested ? "requested" : (hasEbook ? "available" : null);
        const showAudiobook = audiobookRequested ? "requested" : (hasAudiobook ? "available" : null);

        if (!showEbook && !showAudiobook) return;

        let html = '<div class="book-badges">';
        if (showEbook === "available") html += '<span class="book-badge ebook">eBook ✓</span>';
        else if (showEbook === "requested") html += '<span class="book-badge ebook-requested">eBook Requested</span>';
        if (showAudiobook === "available") html += '<span class="book-badge audiobook">Audiobook ✓</span>';
        else if (showAudiobook === "requested") html += '<span class="book-badge audiobook-requested">Audiobook Requested</span>';
        html += '</div>';
        card.querySelector(".book-info").insertAdjacentHTML("beforeend", html);
    });
}

function renderDiscoveryRow(category) {
    const cards = category.books.map(renderBookCard).join("");
    return `
        <div class="discovery-row">
            <div class="discovery-row-header">
                <div class="discovery-row-title">${category.title}</div>
            </div>
            <div class="discovery-row-scroll">${cards}</div>
        </div>`;
}

async function loadDiscovery() {
    const container = document.getElementById("discovery-content");

    searchState.seq++;   // cancel any in-flight search render
    setSearchMode(false);

    container.innerHTML = '<div class="discovery-loading"><div class="spinner"></div> Loading discovery...</div>';

    const promises = DISCOVERY_CATEGORIES.map(async (cat) => {
        try {
            const resp = await fetch("/api/discover?category=" + encodeURIComponent(cat.key) + "&limit=20");
            const data = await resp.json();
            if (data.error || !data.length) return null;
            return { ...cat, books: data };
        } catch {
            return null;
        }
    });

    const results = await Promise.all(promises);
    const valid = results.filter(Boolean);

    if (!valid.length) {
        container.innerHTML = '<div class="empty-state">Unable to load discovery content</div>';
        return;
    }

    container.innerHTML = valid.map(renderDiscoveryRow).join("");

    container.querySelectorAll(".book-card").forEach((card) => {
        card.addEventListener("click", () => {
            openDownloadModal(JSON.parse(card.dataset.book));
        });
    });
    fetchAvailability().then(applyAvailabilityBadges);
}

// ─── Download Modal ───

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

// ─── Requests ───

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
    const cover = req.cover_url || coverPlaceholder(req.title);
    const progress = req.progress || 0;
    const fillClass = req.status === "completed" ? "complete" : req.status === "error" ? "error" : "";

    let statusDisplay;
    if (req.status === "processing" || req.status === "pending") {
        statusDisplay = '<span class="status-label processing"><span class="spinner"></span> Processing</span>';
    } else if (req.status === "completed") {
        statusDisplay = `<span class="status-label completed">Completed</span>
                <div class="progress-bar">
                    <div class="progress-fill complete" style="width: 100%"></div>
                </div>`;
    } else if (req.status === "error") {
        statusDisplay = `<span class="status-label error">Error</span>
                ${req.error ? `<div class="request-error" title="${escapeHtml(req.error)}">${escapeHtml(req.error)}</div>` : ""}`;
    } else {
        // downloading
        statusDisplay = `<span class="status-label downloading">Downloading</span>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${Number(progress) || 0}%"></div>
                </div>`;
    }

    return `
        <div class="request-item">
            <img class="request-cover" src="${escapeHtml(cover)}" alt="${escapeHtml(req.title)}"
                 data-title="${escapeHtml(req.title)}" onerror="coverFail(this)">
            <div class="request-details">
                <div class="request-title">${escapeHtml(req.title)}</div>
                <div class="request-meta">${escapeHtml(req.author || "")}</div>
                <span class="request-server ${escapeHtml(req.server_type)}">${escapeHtml(req.server_type)}</span>
            </div>
            <div class="request-status">
                ${statusDisplay}
                <button class="btn btn-small btn-danger delete-btn" data-id="${escapeHtml(req.id)}" style="margin-top: 0.4rem">Remove</button>
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

// ─── Settings ───

async function loadConfig() {
    try {
        const resp = await fetch("/api/config");
        const data = await resp.json();
        document.getElementById("ebook-url").value = data.ebook.url || "";
        document.getElementById("ebook-api").value = data.ebook.api_key || "";
        document.getElementById("audiobook-url").value = data.audiobook.url || "";
        document.getElementById("audiobook-api").value = data.audiobook.api_key || "";
        document.getElementById("ebook-server-software").value = data.ebook.server_software || "readarr";
        document.getElementById("audiobook-server-software").value = data.audiobook.server_software || "readarr";
    } catch (err) {
        console.error("Failed to load config", err);
    }
}

window.saveConfig = async function (type) {
    const url = document.getElementById(type + "-url").value;
    const api_key = document.getElementById(type + "-api").value;
    const server_software = document.getElementById(type + "-server-software").value;
    const statusEl = document.getElementById(type + "-status");

    try {
        const resp = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ server_type: type, url, api_key, server_software }),
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
    const server_software = document.getElementById(type + "-server-software").value;
    const statusEl = document.getElementById(type + "-status");

    statusEl.className = "status-msg";
    statusEl.textContent = "Testing...";

    try {
        const resp = await fetch("/api/config/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url, api_key, server_software }),
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

// ─── User Management ───

async function loadUsers() {
    const list = document.getElementById("users-list");
    try {
        const resp = await fetch("/api/users");
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.length) {
            list.innerHTML = '<div class="empty-state">No users found</div>';
            return;
        }
        list.innerHTML = data.map(renderUser).join("");
        list.querySelectorAll(".edit-user-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                openEditUserModal(btn.dataset.username, btn.dataset.role);
            });
        });
        list.querySelectorAll(".delete-user-btn").forEach((btn) => {
            btn.addEventListener("click", async () => {
                if (!confirm("Delete user '" + btn.dataset.username + "'?")) return;
                try {
                    const resp = await fetch("/api/users/" + encodeURIComponent(btn.dataset.username), {
                        method: "DELETE",
                    });
                    const data = await resp.json();
                    if (data.error) {
                        alert(data.error);
                    } else {
                        loadUsers();
                    }
                } catch (err) {
                    alert("Error: " + err.message);
                }
            });
        });
    } catch (err) {
        list.innerHTML = '<div class="empty-state">Error loading users</div>';
    }
}

function renderUser(user) {
    const initial = user.username.charAt(0);
    const createdDate = user.created_at ? new Date(user.created_at).toLocaleDateString() : "Unknown";
    const isSelf = currentUser && user.username === currentUser.username;
    const deleteDisabled = isSelf ? "disabled" : "";
    const deleteStyle = isSelf ? 'style="opacity:0.4;cursor:not-allowed;"' : "";

    return `
        <div class="user-item">
            <div class="user-avatar">${initial}</div>
            <div class="user-details">
                <div class="user-name">${user.username}${isSelf ? " (you)" : ""}</div>
                <div class="user-meta">Created ${createdDate}</div>
            </div>
            <span class="user-role-badge ${user.role}">${user.role}</span>
            <div class="user-actions">
                <button class="btn btn-small btn-secondary edit-user-btn"
                        data-username="${user.username}" data-role="${user.role}">Edit</button>
                <button class="btn btn-small btn-danger delete-user-btn"
                        data-username="${user.username}" ${deleteDisabled} ${deleteStyle}>Delete</button>
            </div>
        </div>`;
}

function openAddUserModal() {
    editingUsername = null;
    document.getElementById("user-modal-title").textContent = "Add User";
    document.getElementById("user-modal-username").value = "";
    document.getElementById("user-modal-username").disabled = false;
    document.getElementById("user-modal-password").value = "";
    document.getElementById("user-modal-role").value = "user";
    document.getElementById("user-modal-error").style.display = "none";
    document.getElementById("user-modal").classList.add("active");
}

function openEditUserModal(username, role) {
    editingUsername = username;
    document.getElementById("user-modal-title").textContent = "Edit User";
    document.getElementById("user-modal-username").value = username;
    document.getElementById("user-modal-username").disabled = true;
    document.getElementById("user-modal-password").value = "";
    document.getElementById("user-modal-password").placeholder = "Leave blank to keep current password";
    document.getElementById("user-modal-role").value = role;
    document.getElementById("user-modal-error").style.display = "none";
    document.getElementById("user-modal").classList.add("active");
}

function closeUserModal() {
    document.getElementById("user-modal").classList.remove("active");
    document.getElementById("user-modal-password").placeholder = "Enter password";
    editingUsername = null;
}

window.saveUserModal = async function () {
    const username = document.getElementById("user-modal-username").value.trim();
    const password = document.getElementById("user-modal-password").value;
    const role = document.getElementById("user-modal-role").value;
    const errorEl = document.getElementById("user-modal-error");
    const btn = document.getElementById("user-modal-save-btn");

    errorEl.style.display = "none";

    if (!username) {
        errorEl.textContent = "Username is required";
        errorEl.style.display = "block";
        return;
    }

    if (!editingUsername && !password) {
        errorEl.textContent = "Password is required for new users";
        errorEl.style.display = "block";
        return;
    }

    btn.disabled = true;
    btn.textContent = "Saving...";

    try {
        let resp;
        if (editingUsername) {
            // Edit existing user
            const body = { role };
            if (password) body.password = password;
            resp = await fetch("/api/users/" + encodeURIComponent(editingUsername), {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
        } else {
            // Create new user
            resp = await fetch("/api/users", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password, role }),
            });
        }

        const data = await resp.json();
        if (data.error) {
            errorEl.textContent = data.error;
            errorEl.style.display = "block";
        } else {
            closeUserModal();
            loadUsers();
        }
    } catch (err) {
        errorEl.textContent = "Error: " + err.message;
        errorEl.style.display = "block";
    } finally {
        btn.disabled = false;
        btn.textContent = "Save";
    }
};

// Close modals on background click
document.getElementById("download-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
});
document.getElementById("user-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeUserModal();
});

// ─── LDAP Configuration ───

async function loadLDAP() {
    try {
        const resp = await fetch("/api/ldap");
        const data = await resp.json();
        document.getElementById("ldap-enabled").checked = data.enabled || false;
        document.getElementById("ldap-server-url").value = data.server_url || "";
        document.getElementById("ldap-bind-dn").value = data.bind_dn || "";
        document.getElementById("ldap-bind-password").value = data.bind_password || "";
        document.getElementById("ldap-base-dn").value = data.base_dn || "";
        document.getElementById("ldap-search-filter").value = data.user_search_filter || "(sAMAccountName={username})";
        document.getElementById("ldap-default-role").value = data.default_role || "user";
    } catch (err) {
        console.error("Failed to load LDAP config", err);
    }
}

window.saveLDAP = async function () {
    const statusEl = document.getElementById("ldap-status");
    try {
        const resp = await fetch("/api/ldap", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                enabled: document.getElementById("ldap-enabled").checked,
                server_url: document.getElementById("ldap-server-url").value,
                bind_dn: document.getElementById("ldap-bind-dn").value,
                bind_password: document.getElementById("ldap-bind-password").value,
                base_dn: document.getElementById("ldap-base-dn").value,
                user_search_filter: document.getElementById("ldap-search-filter").value,
                default_role: document.getElementById("ldap-default-role").value,
            }),
        });
        const data = await resp.json();
        if (data.error) {
            statusEl.className = "status-msg error";
            statusEl.textContent = data.error;
        } else {
            statusEl.className = "status-msg success";
            statusEl.textContent = "LDAP configuration saved!";
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
    setTimeout(() => { statusEl.textContent = ""; }, 3000);
};

window.testLDAP = async function () {
    const statusEl = document.getElementById("ldap-status");
    statusEl.className = "status-msg";
    statusEl.textContent = "Testing...";
    try {
        const resp = await fetch("/api/ldap/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                server_url: document.getElementById("ldap-server-url").value,
                bind_dn: document.getElementById("ldap-bind-dn").value,
                bind_password: document.getElementById("ldap-bind-password").value,
                base_dn: document.getElementById("ldap-base-dn").value,
                user_search_filter: document.getElementById("ldap-search-filter").value,
            }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.className = "status-msg success";
            statusEl.textContent = data.message;
        } else {
            statusEl.className = "status-msg error";
            statusEl.textContent = "Failed: " + data.error;
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
};

// ─── OIDC Configuration ───

async function loadOIDC() {
    try {
        const resp = await fetch("/api/oidc");
        const data = await resp.json();
        const unavailableEl = document.getElementById("oidc-unavailable");
        if (data.available === false) {
            if (unavailableEl) unavailableEl.style.display = "block";
        } else if (unavailableEl) {
            unavailableEl.style.display = "none";
        }
        document.getElementById("oidc-enabled").checked = data.enabled || false;
        document.getElementById("oidc-display-name").value = data.display_name || "OIDC";
        document.getElementById("oidc-issuer-url").value = data.issuer_url || "";
        document.getElementById("oidc-client-id").value = data.client_id || "";
        document.getElementById("oidc-client-secret").value = data.client_secret || "";
        document.getElementById("oidc-scope").value = data.scope || "openid profile email";
        document.getElementById("oidc-username-claim").value = data.username_claim || "preferred_username";
        document.getElementById("oidc-default-role").value = data.default_role || "user";
        document.getElementById("oidc-auto-create").checked = data.auto_create_users || false;
        document.getElementById("oidc-auto-redirect").checked = data.auto_redirect || false;
    } catch (err) {
        console.error("Failed to load OIDC config", err);
    }
}

window.saveOIDC = async function () {
    const statusEl = document.getElementById("oidc-status");
    try {
        const resp = await fetch("/api/oidc", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                enabled: document.getElementById("oidc-enabled").checked,
                display_name: document.getElementById("oidc-display-name").value,
                issuer_url: document.getElementById("oidc-issuer-url").value,
                client_id: document.getElementById("oidc-client-id").value,
                client_secret: document.getElementById("oidc-client-secret").value,
                scope: document.getElementById("oidc-scope").value,
                username_claim: document.getElementById("oidc-username-claim").value,
                default_role: document.getElementById("oidc-default-role").value,
                auto_create_users: document.getElementById("oidc-auto-create").checked,
                auto_redirect: document.getElementById("oidc-auto-redirect").checked,
            }),
        });
        const data = await resp.json();
        if (data.error) {
            statusEl.className = "status-msg error";
            statusEl.textContent = data.error;
        } else {
            statusEl.className = "status-msg success";
            statusEl.textContent = "OIDC configuration saved!";
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
    setTimeout(() => { statusEl.textContent = ""; }, 3000);
};

window.testOIDC = async function () {
    const statusEl = document.getElementById("oidc-status");
    statusEl.className = "status-msg";
    statusEl.textContent = "Testing...";
    try {
        const resp = await fetch("/api/oidc/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                issuer_url: document.getElementById("oidc-issuer-url").value,
            }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.className = "status-msg success";
            statusEl.textContent = data.message;
        } else {
            statusEl.className = "status-msg error";
            statusEl.textContent = "Failed: " + data.error;
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
};

// ─── Init ───

// Load current user first, then the rest
loadSearchPrefs();
loadCurrentUser().then(() => {
    loadConfig();
    loadDiscovery();
});
