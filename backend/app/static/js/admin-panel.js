(function () {
    const state = {
        usersPage: 1,
        usersPageSize: 10,
        usersTotal: 0,
        formsPage: 1,
        formsPageSize: 10,
        formsTotal: 0,
        logsPage: 1,
        logsPageSize: 20,
        logsTotal: 0,
    };

    function showMessage(message, kind) {
        const node = document.getElementById("alertHost");
        if (!node) return;

        const className = kind === "error" ? "alert-error" : "alert-success";
        node.innerHTML = "<div class=\"alert " + className + "\"><span class=\"alert-icon\">" + (kind === "error" ? "⚠️" : "✅") + "</span><span>" + message + "</span></div>";
        setTimeout(() => {
            if (node) node.innerHTML = "";
        }, 3000);
    }

    async function parseResponse(response) {
        const raw = await response.text();
        let data = {};
        try {
            data = raw ? JSON.parse(raw) : {};
        } catch (e) {
            data = {};
        }

        if (!response.ok) {
            throw new Error(data.detail || data.error || data.message || ("HTTP " + response.status));
        }

        return data;
    }

    async function apiGet(url) {
        const res = await fetch(url, { credentials: "include", cache: "no-store" });
        return parseResponse(res);
    }

    async function apiPost(url, body) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: body ? JSON.stringify(body) : undefined,
        });
        return parseResponse(res);
    }

    async function apiPut(url, body) {
        const res = await fetch(url, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify(body),
        });
        return parseResponse(res);
    }

    async function apiDelete(url) {
        const res = await fetch(url, { method: "DELETE", credentials: "include" });
        return parseResponse(res);
    }

    async function logoutAndRedirect() {
        try {
            await apiPost("/api/auth/logout");
        } finally {
            window.location.replace("/");
        }
    }

    function formatDate(value) {
        if (!value) return "-";
        try {
            return new Date(value).toLocaleString("vi-VN");
        } catch (e) {
            return value;
        }
    }

    function activateSidebar() {
        const path = window.location.pathname;
        document.querySelectorAll(".sidebar-menu-link").forEach((link) => {
            const href = link.getAttribute("href") || "";
            const active = href === path || (href !== "/" && path.startsWith(href));
            link.classList.toggle("active", active);
        });
    }

    function setupUserMenu() {
        const trigger = document.getElementById("adminUserMenuTrigger");
        const menu = document.getElementById("adminUserMenuContent");
        if (!trigger || !menu) return;

        trigger.addEventListener("click", () => {
            menu.classList.toggle("active");
        });

        document.addEventListener("click", (event) => {
            const inMenu = event.target.closest("#adminUserMenuContent") || event.target.closest("#adminUserMenuTrigger");
            if (!inMenu) {
                menu.classList.remove("active");
            }
        });

        const logoutButtons = document.querySelectorAll("[data-admin-logout]");
        logoutButtons.forEach((btn) => {
            btn.addEventListener("click", async () => {
                await logoutAndRedirect();
            });
        });
    }

    function setupThemeToggle() {
        const toggle = document.getElementById("adminThemeToggle");
        if (!toggle) return;

        const applyToggleVisual = (theme) => {
            const isLight = theme === "light";
            toggle.textContent = isLight ? "☀️" : "🌙";
            toggle.setAttribute("title", isLight ? "Chế độ sáng" : "Chế độ tối");
            toggle.setAttribute("aria-label", isLight ? "Chế độ sáng" : "Chế độ tối");
        };

        const canUseGlobalManager = Boolean(
            window.themeManager
            && typeof window.themeManager.toggleTheme === "function"
            && typeof window.themeManager.getCurrentTheme === "function"
        );

        if (canUseGlobalManager) {
            applyToggleVisual(window.themeManager.getCurrentTheme());

            toggle.addEventListener("click", () => {
                window.themeManager.toggleTheme();
            });

            window.addEventListener("themeChanged", (event) => {
                const theme = (event && event.detail && event.detail.theme)
                    || window.themeManager.getCurrentTheme();
                applyToggleVisual(theme);
            });
            return;
        }

        const storageKey = "app-theme";
        const normalizeTheme = (value) => (value === "light" ? "light" : "dark");
        const setFallbackTheme = (theme) => {
            document.body.classList.remove("light-mode", "dark-mode");
            document.body.classList.add(theme === "light" ? "light-mode" : "dark-mode");
            localStorage.setItem(storageKey, theme);
            applyToggleVisual(theme);
        };

        setFallbackTheme(normalizeTheme(localStorage.getItem(storageKey)));
        toggle.addEventListener("click", () => {
            const current = document.body.classList.contains("light-mode") ? "light" : "dark";
            setFallbackTheme(current === "light" ? "dark" : "light");
        });
    }

    async function ensureAdminSession() {
        const session = await apiGet("/api/auth/session");
        if (!session.authenticated || !session.user) {
            window.location.replace("/");
            return false;
        }
        if (!session.user.is_admin) {
            window.location.replace("/");
            return false;
        }

        document.querySelectorAll("[data-admin-username]").forEach((el) => {
            el.textContent = session.user.username || "Admin User";
        });

        return true;
    }

    function setupHistoryRestoreGuard() {
        window.addEventListener("pageshow", (event) => {
            if (event && event.persisted) {
                ensureAdminSession().catch(() => {
                    window.location.replace("/");
                });
            }
        });
    }

    async function loadDashboard() {
        const marker = document.getElementById("dashboardPage");
        if (!marker) return;

        const stats = await apiGet("/api/admin/stats");

        const totalUsers = Number(stats.total_users || 0);
        const activeUsers = Number(stats.active_users || 0);
        const totalForms = Number(stats.total_forms || 0);
        const totalSubmissions = Number(stats.total_submissions || 0);

        const uptime = totalUsers > 0 ? ((activeUsers / totalUsers) * 100).toFixed(1) : "100.0";

        const statTotalUsers = document.getElementById("statTotalUsers");
        const statTotalForms = document.getElementById("statTotalForms");
        const statSubmissions = document.getElementById("statSubmissions");
        const statUptime = document.getElementById("statUptime");

        if (statTotalUsers) statTotalUsers.textContent = String(totalUsers);
        if (statTotalForms) statTotalForms.textContent = String(totalForms);
        if (statSubmissions) statSubmissions.textContent = String(totalSubmissions);
        if (statUptime) statUptime.textContent = uptime + "%";

        const infoVersion = document.getElementById("infoVersion");
        const infoLastUpdate = document.getElementById("infoLastUpdate");
        const infoAdmins = document.getElementById("infoAdmins");
        const infoActive7d = document.getElementById("infoActive7d");

        if (infoVersion) infoVersion.textContent = "v1.0.0";
        if (infoLastUpdate) infoLastUpdate.textContent = formatDate(stats.timestamp);
        if (infoAdmins) infoAdmins.textContent = String(stats.admin_users || 0);
        if (infoActive7d) infoActive7d.textContent = String(stats.active_last_7_days || 0);

        const logs = await apiGet("/api/admin/audit-log?skip=0&limit=8&days=7");
        const tbody = document.getElementById("recentActivityBody");
        if (!tbody) return;

        const rows = logs.data || [];
        if (rows.length === 0) {
            tbody.innerHTML = "<tr class=\"table-row\"><td class=\"table-cell\" colspan=\"4\">Chưa có dữ liệu hoạt động gần đây.</td></tr>";
            return;
        }

        tbody.innerHTML = rows.map((log) => {
            return "<tr class=\"table-row\">"
                + "<td class=\"table-cell\">" + formatDate(log.created_at) + "</td>"
                + "<td class=\"table-cell\">" + (log.object_name || "-") + "</td>"
                + "<td class=\"table-cell\">" + (log.description || log.action || "-") + "</td>"
                + "<td class=\"table-cell\"><span class=\"badge " + (log.status === "success" ? "badge-success" : "badge-error") + "\">" + (log.status || "-") + "</span></td>"
                + "</tr>";
        }).join("");
    }

    async function loadUsers() {
        const marker = document.getElementById("usersPage");
        if (!marker) return;

        const input = document.getElementById("userSearchInput");
        const role = document.getElementById("userRoleFilter");
        const status = document.getElementById("userStatusFilter");
        const table = document.getElementById("usersTableBody");
        const pager = document.getElementById("usersPaginationInfo");

        async function fetchUsers() {
            const skip = (state.usersPage - 1) * state.usersPageSize;
            const query = new URLSearchParams({
                skip: String(skip),
                limit: String(state.usersPageSize),
                search: (input && input.value.trim()) || "",
                role: (role && role.value) || "",
                status: (status && status.value) || "",
            });

            const data = await apiGet("/api/admin/users?" + query.toString());
            const rows = data.data || [];
            const summary = data.summary || {};
            state.usersTotal = data.total || 0;

            if (rows.length === 0) {
                table.innerHTML = "<tr class=\"table-row\"><td class=\"table-cell\" colspan=\"7\">Không có dữ liệu người dùng.</td></tr>";
            } else {
                table.innerHTML = rows.map((u) => {
                    const roleBadge = u.is_admin ? "badge-primary\">Admin" : "badge-info\">User";
                    const statusBadge = u.is_active ? "badge-success\">Active" : "badge-warning\">Inactive";
                    return "<tr class=\"table-row\">"
                        + "<td class=\"table-cell\">" + u.id + "</td>"
                        + "<td class=\"table-cell\">" + (u.username || "-") + "</td>"
                        + "<td class=\"table-cell\">" + (u.email || "-") + "</td>"
                        + "<td class=\"table-cell\"><span class=\"badge " + roleBadge + "</span></td>"
                        + "<td class=\"table-cell\"><span class=\"badge " + statusBadge + "</span></td>"
                        + "<td class=\"table-cell\">" + formatDate(u.created_at) + "</td>"
                        + "<td class=\"table-cell\"><div class=\"table-actions\">"
                        + "<button class=\"btn btn-sm btn-action-delete\" data-action=\"delete-user\" data-id=\"" + u.id + "\">Xóa</button>"
                        + "<button class=\"btn btn-sm btn-action-toggle " + (u.is_active ? "is-lock" : "is-unlock") + "\" data-action=\"" + (u.is_active ? "lock-user" : "unlock-user") + "\" data-id=\"" + u.id + "\" data-username=\"" + (u.username || "") + "\">" + (u.is_active ? "Khóa" : "Mở khóa") + "</button>"
                        + "</div></td>"
                        + "</tr>";
                }).join("");
            }

            const from = state.usersTotal === 0 ? 0 : skip + 1;
            const to = Math.min(skip + state.usersPageSize, state.usersTotal);
            pager.textContent = "Hiển thị " + from + " - " + to + " của " + state.usersTotal + " người dùng";

            const statAll = document.getElementById("usersStatAll");
            const statAdmin = document.getElementById("usersStatAdmins");
            const statActive = document.getElementById("usersStatActive");
            const statInactive = document.getElementById("usersStatInactive");

            if (statAll) statAll.textContent = String(summary.total_users || 0);
            if (statAdmin) statAdmin.textContent = String(summary.admin_users || 0);
            if (statActive) statActive.textContent = String(summary.active_users || 0);
            if (statInactive) statInactive.textContent = String(summary.inactive_users || 0);
        }

        marker.addEventListener("click", async (event) => {
            const actionButton = event.target.closest("button[data-action]");
            if (!actionButton) return;

            const userId = Number(actionButton.getAttribute("data-id"));
            const action = actionButton.getAttribute("data-action");
            if (!userId) return;

            try {
                if (action === "delete-user") {
                    const ok = window.confirm("Bạn có chắc muốn xóa tài khoản này? Dữ liệu liên quan có thể bị mất.");
                    if (!ok) return;
                    await apiDelete("/api/admin/users/" + userId);
                    showMessage("Đã xóa tài khoản người dùng", "success");
                }
                if (action === "lock-user") {
                    const ok = window.confirm("Bạn có chắc muốn khóa tài khoản này?");
                    if (!ok) return;
                    await apiPost("/api/admin/users/" + userId + "/lock");
                    showMessage("Đã khóa tài khoản", "success");
                }
                if (action === "unlock-user") {
                    await apiPost("/api/admin/users/" + userId + "/unlock");
                    showMessage("Đã mở khóa tài khoản", "success");
                }
                await fetchUsers();
            } catch (error) {
                showMessage(error.message || "Không thể cập nhật người dùng", "error");
            }
        });

        const searchBtn = document.getElementById("usersSearchBtn");
        const prevBtn = document.getElementById("usersPrevBtn");
        const nextBtn = document.getElementById("usersNextBtn");
        const addUserBtn = document.getElementById("addUserBtn");

        if (searchBtn) {
            searchBtn.addEventListener("click", async () => {
                state.usersPage = 1;
                await fetchUsers();
            });
        }

        if (prevBtn) {
            prevBtn.addEventListener("click", async () => {
                if (state.usersPage <= 1) return;
                state.usersPage -= 1;
                await fetchUsers();
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener("click", async () => {
                const maxPage = Math.ceil(state.usersTotal / state.usersPageSize);
                if (state.usersPage >= maxPage) return;
                state.usersPage += 1;
                await fetchUsers();
            });
        }

        if (addUserBtn) {
            addUserBtn.addEventListener("click", async () => {
                const username = window.prompt("Tên đăng nhập user mới:");
                if (!username) return;
                const email = window.prompt("Email user mới:");
                if (!email) return;
                const password = window.prompt("Mật khẩu (>= 6 ký tự):", "123456");
                if (!password) return;

                try {
                    await apiPost("/api/admin/users", {
                        username: username.trim(),
                        email: email.trim(),
                        password: password,
                        is_admin: false,
                    });
                    showMessage("Tạo người dùng thành công", "success");
                    await fetchUsers();
                } catch (error) {
                    showMessage(error.message || "Không thể tạo người dùng", "error");
                }
            });
        }

        await fetchUsers();
    }

    async function loadForms() {
        const marker = document.getElementById("formsPage");
        if (!marker) return;

        const input = document.getElementById("formSearchInput");
        const type = document.getElementById("formTypeFilter");
        const table = document.getElementById("formsTableBody");
        const pager = document.getElementById("formsPaginationInfo");

        async function fetchForms() {
            const skip = (state.formsPage - 1) * state.formsPageSize;
            const query = new URLSearchParams({
                skip: String(skip),
                limit: String(state.formsPageSize),
                search: (input && input.value.trim()) || "",
                form_type: (type && type.value) || "",
            });

            const data = await apiGet("/api/admin/forms?" + query.toString());
            const rows = data.data || [];
            state.formsTotal = data.total || 0;

            if (rows.length === 0) {
                table.innerHTML = "<tr class=\"table-row\"><td class=\"table-cell\" colspan=\"7\">Không có dữ liệu biểu mẫu.</td></tr>";
            } else {
                table.innerHTML = rows.map((f) => {
                    return "<tr class=\"table-row\">"
                        + "<td class=\"table-cell\">" + f.id + "</td>"
                        + "<td class=\"table-cell\">" + (f.form_name || "-") + "</td>"
                        + "<td class=\"table-cell\">" + (f.form_type || "-") + "</td>"
                        + "<td class=\"table-cell\">" + (f.user_id || "-") + "</td>"
                        + "<td class=\"table-cell\">" + (f.is_template ? "Template" : "Form") + "</td>"
                        + "<td class=\"table-cell\">" + formatDate(f.created_at) + "</td>"
                        + "<td class=\"table-cell\"><button class=\"btn btn-warning btn-sm\" data-form-delete=\"" + f.id + "\">Xóa</button></td>"
                        + "</tr>";
                }).join("");
            }

            const from = state.formsTotal === 0 ? 0 : skip + 1;
            const to = Math.min(skip + state.formsPageSize, state.formsTotal);
            pager.textContent = "Hiển thị " + from + " - " + to + " của " + state.formsTotal + " biểu mẫu";

            const stats = await apiGet("/api/admin/forms/stats");
            const totalForms = document.getElementById("formsTotal");
            const wordForms = document.getElementById("formsWord");
            const excelForms = document.getElementById("formsExcel");
            const totalSubmissions = document.getElementById("formsSubmissions");

            if (totalForms) totalForms.textContent = String(stats.total_forms || 0);
            if (wordForms) wordForms.textContent = String(stats.word_forms || 0);
            if (excelForms) excelForms.textContent = String(stats.excel_forms || 0);
            if (totalSubmissions) totalSubmissions.textContent = String(stats.total_submissions || 0);
        }

        marker.addEventListener("click", async (event) => {
            const deleteBtn = event.target.closest("button[data-form-delete]");
            if (!deleteBtn) return;

            const formId = Number(deleteBtn.getAttribute("data-form-delete"));
            if (!formId) return;

            const ok = window.confirm("Bạn có chắc chắn muốn xóa biểu mẫu này?");
            if (!ok) return;

            try {
                await apiDelete("/api/admin/forms/" + formId);
                showMessage("Đã xóa biểu mẫu", "success");
                await fetchForms();
            } catch (error) {
                showMessage(error.message || "Không thể xóa biểu mẫu", "error");
            }
        });

        const searchBtn = document.getElementById("formsSearchBtn");
        const prevBtn = document.getElementById("formsPrevBtn");
        const nextBtn = document.getElementById("formsNextBtn");

        if (searchBtn) {
            searchBtn.addEventListener("click", async () => {
                state.formsPage = 1;
                await fetchForms();
            });
        }

        if (prevBtn) {
            prevBtn.addEventListener("click", async () => {
                if (state.formsPage <= 1) return;
                state.formsPage -= 1;
                await fetchForms();
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener("click", async () => {
                const maxPage = Math.ceil(state.formsTotal / state.formsPageSize);
                if (state.formsPage >= maxPage) return;
                state.formsPage += 1;
                await fetchForms();
            });
        }

        await fetchForms();
    }

    async function loadReports() {
        const marker = document.getElementById("reportsPage");
        if (!marker) return;

        async function fetchReports() {
            const system = await apiGet("/api/admin/stats");
            const forms = await apiGet("/api/admin/forms/stats");

            const totalUsers = document.getElementById("reportTotalUsers");
            const totalForms = document.getElementById("reportTotalForms");
            const totalSubmissions = document.getElementById("reportTotalSubmissions");
            const avgActivity = document.getElementById("reportAvgActivity");

            if (totalUsers) totalUsers.textContent = String(system.total_users || 0);
            if (totalForms) totalForms.textContent = String(forms.total_forms || 0);
            if (totalSubmissions) totalSubmissions.textContent = String(system.total_submissions || 0);

            if (avgActivity) avgActivity.textContent = String(system.active_last_7_days || 0);

            const growthText = document.getElementById("reportGrowthText");
            const usageText = document.getElementById("reportUsageText");
            if (growthText) {
                growthText.textContent = "Người dùng mới 30 ngày: "
                    + String(system.new_users_30_days || 0)
                    + " | Inactive: "
                    + String(system.inactive_users || 0);
            }
            if (usageText) {
                usageText.textContent = "Word: " + String(forms.word_forms || 0)
                    + " | Excel: " + String(forms.excel_forms || 0)
                    + " | Total forms: " + String(forms.total_forms || 0)
                    + " | Total submissions: " + String(system.total_submissions || 0);
            }
        }

        const searchBtn = document.getElementById("reportsSearchBtn");
        if (searchBtn) {
            searchBtn.addEventListener("click", fetchReports);
        }

        await fetchReports();
    }

    async function loadAuditLogs() {
        const marker = document.getElementById("auditPage");
        if (!marker) return;

        const actionFilter = document.getElementById("auditActionFilter");
        const objectFilter = document.getElementById("auditObjectFilter");
        const daysFilter = document.getElementById("auditDaysFilter");
        const table = document.getElementById("auditTableBody");
        const pager = document.getElementById("auditPaginationInfo");

        async function fetchLogs() {
            const skip = (state.logsPage - 1) * state.logsPageSize;
            const query = new URLSearchParams({
                skip: String(skip),
                limit: String(state.logsPageSize),
                action: (actionFilter && actionFilter.value) || "",
                object_type: (objectFilter && objectFilter.value) || "",
                days: (daysFilter && daysFilter.value) || "30",
            });

            const data = await apiGet("/api/admin/audit-log?" + query.toString());
            const rows = data.data || [];
            state.logsTotal = data.total || 0;

            if (rows.length === 0) {
                table.innerHTML = "<tr class=\"table-row\"><td class=\"table-cell\" colspan=\"6\">Không có log trong điều kiện lọc hiện tại.</td></tr>";
            } else {
                table.innerHTML = rows.map((log) => {
                    return "<tr class=\"table-row\">"
                        + "<td class=\"table-cell\">" + formatDate(log.created_at) + "</td>"
                        + "<td class=\"table-cell\">" + (log.admin_id || "-") + "</td>"
                        + "<td class=\"table-cell\">" + (log.action || "-") + "</td>"
                        + "<td class=\"table-cell\">" + (log.object_type || "-") + "</td>"
                        + "<td class=\"table-cell\">" + (log.description || "-") + "</td>"
                        + "<td class=\"table-cell\"><span class=\"badge " + (log.status === "success" ? "badge-success" : "badge-error") + "\">" + (log.status || "-") + "</span></td>"
                        + "</tr>";
                }).join("");
            }

            const from = state.logsTotal === 0 ? 0 : skip + 1;
            const to = Math.min(skip + state.logsPageSize, state.logsTotal);
            pager.textContent = "Hiển thị " + from + " - " + to + " của " + state.logsTotal + " bản ghi";
        }

        const searchBtn = document.getElementById("auditSearchBtn");
        const prevBtn = document.getElementById("auditPrevBtn");
        const nextBtn = document.getElementById("auditNextBtn");

        if (searchBtn) {
            searchBtn.addEventListener("click", async () => {
                state.logsPage = 1;
                await fetchLogs();
            });
        }

        if (prevBtn) {
            prevBtn.addEventListener("click", async () => {
                if (state.logsPage <= 1) return;
                state.logsPage -= 1;
                await fetchLogs();
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener("click", async () => {
                const maxPage = Math.ceil(state.logsTotal / state.logsPageSize);
                if (state.logsPage >= maxPage) return;
                state.logsPage += 1;
                await fetchLogs();
            });
        }

        await fetchLogs();
    }

    async function init() {
        try {
            activateSidebar();
            setupUserMenu();
            setupThemeToggle();
            setupHistoryRestoreGuard();

            const ok = await ensureAdminSession();
            if (!ok) return;

            await loadDashboard();
            await loadUsers();
            await loadForms();
            await loadReports();
            await loadAuditLogs();
        } catch (error) {
            showMessage(error.message || "Không thể tải dữ liệu quản trị", "error");
        }
    }

    document.addEventListener("DOMContentLoaded", init);
})();
