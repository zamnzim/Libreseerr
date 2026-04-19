// State
let currentModalBook = null;
let selectedServer = "ebook";
let currentUser = null;
let editingUsername = null;
let serverConfig = { ebook: { configured: false }, audiobook: { configured: false } };
let requestsByTitle = {}; // lowercase title → request status (libreseerr history)
let downloadedTitles = new Set(); // lowercase titles confirmed downloaded in Readarr/Bookshelf

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
        if (link.dataset.page === "users") { loadUsers(); loadLDAP(); }
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

async function doSearch() {
    const query = searchInput.value.trim();
    const grid = document.getElementById("search-results");

    // Make sure we're on the search page
    if (!document.getElementById("page-search").classList.contains("active")) {
        document.querySelector('[data-page="search"]').click();
    }

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
            const book = JSON.parse(card.dataset.book);
            if (downloadedTitles.has(book.title?.toLowerCase())) return; // already in library
            card.addEventListener("click", () => openDownloadModal(book));
        });
    } catch (err) {
        grid.innerHTML = `<div class="empty-state">Error: ${err.message}</div>`;
    }
}

function renderBookCard(book) {
    const title = book.title || "Unknown Title";
    const author = book.author?.authorName || (Array.isArray(book.authors) ? book.authors.join(", ") : "Unknown Author");
    const year = book.releaseDate ? book.releaseDate.substring(0, 4) : book.publishedDate ? book.publishedDate.substring(0, 4) : "";
    let cover = "";
    if (book.author?.images?.length) cover = book.author.images[0].url;
    if (!cover && book.images?.length) cover = book.images[0].url;
    if (!cover && book.cover) cover = book.cover;
    if (!cover) cover = "https://via.placeholder.com/200x300/1f2937/ec4899?text=No+Cover";
    const bookJson = JSON.stringify(book).replace(/"/g, "&quot;");

    const key = title.toLowerCase();
    const isDownloaded = downloadedTitles.has(key);
    const requestStatus = !isDownloaded ? requestsByTitle[key] : null;
    const statusBadge = isDownloaded
        ? '<div class="book-status-badge downloaded">Downloaded</div>'
        : requestStatus
        ? `<div class="book-status-badge in-progress">${requestStatus.charAt(0).toUpperCase() + requestStatus.slice(1)}</div>`
        : "";
    const cardClass = isDownloaded ? "book-card book-card--downloaded" : "book-card";

    return `
        <div class="${cardClass}" data-book="${bookJson}">
            ${statusBadge}
            <img class="book-cover" src="${cover}" alt="${title}" loading="lazy"
                 onerror="this.src='https://via.placeholder.com/200x300/1f2937/ec4899?text=No+Cover'">
            <div class="book-overlay">
                <div class="book-overlay-title">${title}</div>
                <div class="book-overlay-author">${author}${year ? " (" + year + ")" : ""}</div>
            </div>
            <div class="book-info">
                <div class="book-title" title="${title}">${title}</div>
                <div class="book-author">${author}</div>
                ${year ? `<div class="book-year">${year}</div>` : ""}
            </div>
        </div>`;
}

// ─── Download Modal ───

async function openDownloadModal(book) {
    currentModalBook = book;

    // Only show buttons for configured servers
    const configuredServers = ["ebook", "audiobook"].filter((s) => serverConfig[s]?.configured);
    selectedServer = configuredServers[0] || "ebook";

    document.getElementById("modal-title").textContent = "Download: " + (book.title || "Unknown");
    document.querySelectorAll(".server-btn").forEach((btn) => {
        const configured = serverConfig[btn.dataset.server]?.configured;
        btn.style.display = configured ? "" : "none";
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
            const defaultId = server === "audiobook" ? localStorage.getItem("defaultAudiobookProfileId") : null;
            profileSelect.innerHTML = profiles
                .map((p) => `<option value="${p.id}" ${String(p.id) === defaultId ? "selected" : ""}>${p.name}</option>`)
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
            const titleKey = data.title?.toLowerCase();
            if (titleKey) requestsByTitle[titleKey] = data.status || "processing";
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
        requestsByTitle = {};
        data.forEach((r) => { requestsByTitle[r.title.toLowerCase()] = r.status; });
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
    const cover = req.cover_url || "https://via.placeholder.com/50x75/1f2937/ec4899?text=N/A";
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
                ${req.error ? `<div class="request-error" title="${req.error}">${req.error}</div>` : ""}`;
    } else {
        // downloading
        statusDisplay = `<span class="status-label downloading">Downloading</span>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${progress}%"></div>
                </div>`;
    }

    return `
        <div class="request-item">
            <img class="request-cover" src="${cover}" alt="${req.title}"
                 onerror="this.src='https://via.placeholder.com/50x75/1f2937/ec4899?text=N/A'">
            <div class="request-details">
                <div class="request-title">${req.title}</div>
                <div class="request-meta">${req.author || ""}</div>
                <span class="request-server ${req.server_type}">${req.server_type}</span>
            </div>
            <div class="request-status">
                ${statusDisplay}
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

// ─── Settings ───

async function loadConfig() {
    try {
        const resp = await fetch("/api/config");
        const data = await resp.json();
        serverConfig = data;
        document.getElementById("ebook-url").value = data.ebook.url || "";
        document.getElementById("ebook-api").value = data.ebook.api_key || "";
        document.getElementById("audiobook-url").value = data.audiobook.url || "";
        document.getElementById("audiobook-api").value = data.audiobook.api_key || "";
        document.getElementById("ebook-server-software").value = data.ebook.server_software || "readarr";
        document.getElementById("audiobook-server-software").value = data.audiobook.server_software || "readarr";
        await loadDefaultProfileOptions();
    } catch (err) {
        console.error("Failed to load config", err);
    }
}

async function loadDefaultProfileOptions() {
    const select = document.getElementById("audiobook-default-profile");
    if (!select || !serverConfig.audiobook?.configured) {
        if (select) select.innerHTML = '<option value="">— server not configured —</option>';
        return;
    }
    try {
        const resp = await fetch("/api/profiles/audiobook");
        const profiles = await resp.json();
        const saved = localStorage.getItem("defaultAudiobookProfileId");
        select.innerHTML = '<option value="">No default</option>' +
            profiles.map((p) => `<option value="${p.id}" ${String(p.id) === saved ? "selected" : ""}>${p.name}</option>`).join("");
    } catch {
        select.innerHTML = '<option value="">Error loading profiles</option>';
    }
}

window.saveDefaultProfile = function () {
    const select = document.getElementById("audiobook-default-profile");
    const value = select.value;
    if (value) {
        localStorage.setItem("defaultAudiobookProfileId", value);
    } else {
        localStorage.removeItem("defaultAudiobookProfileId");
    }
    const btn = select.nextElementSibling;
    const orig = btn.textContent;
    btn.textContent = "Saved!";
    setTimeout(() => { btn.textContent = orig; }, 1500);
};

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

// ─── Init ───

async function loadLibrary() {
    try {
        const resp = await fetch("/api/library");
        const titles = await resp.json();
        downloadedTitles = new Set(titles);
    } catch {
        // non-fatal — badges just won't show
    }
}

// Load current user first, then the rest
loadCurrentUser().then(() => {
    loadConfig();
    loadRequests();
    loadLibrary();
    document.getElementById("search-results").innerHTML =
        '<div class="empty-state">Search for books by title, author, or ISBN</div>';
});
