/* PaperTrail 前端逻辑:统计、列表、检索、增删改查交互 */

(function () {
  "use strict";

  // ---------------- 工具函数 ----------------

  const $ = (id) => document.getElementById(id);

  function escapeHtml(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeRegex(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /** 对文本做关键词高亮(先转义再包 mark) */
  function highlight(text, keyword) {
    const safe = escapeHtml(text);
    const kw = (keyword || "").trim();
    if (!kw) return safe;
    const re = new RegExp("(" + escapeRegex(escapeHtml(kw)) + ")", "gi");
    return safe.replace(re, "<mark>$1</mark>");
  }

  function statusBadge(status) {
    return status === "read"
      ? '<span class="badge badge-read">已读</span>'
      : '<span class="badge badge-unread">未读</span>';
  }

  function formatTime(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString("zh-CN", { hour12: false });
    } catch (e) {
      return iso;
    }
  }

  let toastTimer = null;
  function toast(message, isError) {
    const el = $("toast");
    el.textContent = message;
    el.classList.toggle("error", !!isError);
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
  }

  async function api(path, options) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000); // 15s 超时,避免无限等待
    try {
      const res = await fetch(path, Object.assign({}, options, { signal: controller.signal }));
      if (!res.ok) {
        let detail = "请求失败(" + res.status + ")";
        try {
          const data = await res.json();
          if (data && data.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
        } catch (e) { /* 忽略 */ }
        throw new Error(detail);
      }
      return res.json();
    } catch (err) {
      if (err && err.name === "AbortError") throw new Error("请求超时,请稍后重试");
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  // ---------------- 状态 ----------------

  const state = {
    view: "library",
    query: { q: "", status: "" },
    currentKeyword: "",
  };

  // ---------------- 数据加载 ----------------

  async function loadStats() {
    try {
      const data = await api("/api/stats");
      $("stat-total").textContent = data.total;
      $("stat-read").textContent = data.read;
      $("stat-unread").textContent = data.unread;
    } catch (err) {
      $("stat-total").textContent = "加载失败";
      $("stat-read").textContent = "加载失败";
      $("stat-unread").textContent = "加载失败";
      toast("统计加载失败:" + err.message, true);
    }
  }

  async function loadPapers() {
    const params = new URLSearchParams();
    if (state.query.q) params.set("q", state.query.q);
    if (state.query.status) params.set("status", state.query.status);
    try {
      const data = await api("/api/papers?" + params.toString());
      renderPapers(data.items, data.query);
    } catch (err) {
      // 请求失败时展示明确错误,避免一直停留在「正在加载文献…」
      $("paper-list").innerHTML =
        `<div class="empty-state">文献加载失败:${escapeHtml(err.message)}。请稍后点击「重置」重新加载。</div>`;
      $("result-count").textContent = "";
    }
  }

  function paperCard(paper, keyword) {
    const tags = paper.tags
      ? paper.tags.split(/[,，]/).filter(Boolean).map((t) => `<span class="tag">${escapeHtml(t.trim())}</span>`).join("")
      : "";
    const noteHtml = paper.note
      ? `<div class="paper-note">📝 ${highlight(paper.note, keyword)}</div>`
      : "";
    const abstractHtml = paper.abstract
      ? `<div class="paper-abstract">${highlight(paper.abstract, keyword)}</div>`
      : "";
    return `
      <article class="paper-card" data-id="${escapeHtml(paper.id)}">
        <div class="paper-card-head">
          <div>
            <div class="paper-title">${highlight(paper.title, keyword)}</div>
            <div class="paper-meta">
              ${paper.authors ? `<span>👤 ${escapeHtml(paper.authors)}</span>` : ""}
              <span>🕘 ${escapeHtml(formatTime(paper.created_at))}</span>
            </div>
          </div>
          ${statusBadge(paper.status)}
        </div>
        ${abstractHtml}
        <div>${tags}</div>
        ${noteHtml}
      </article>`;
  }

  function renderPapers(items, query) {
    const kw = (query && query.q) || "";
    state.currentKeyword = kw;
    const listEl = $("paper-list");
    if (!items.length) {
      listEl.innerHTML = `<div class="empty-state">${
        kw ? `没有找到与「${escapeHtml(kw)}」相关的文献` : "文献库为空,点击右上角「＋ 添加文献」开始收录"
      }</div>`;
      $("result-count").textContent = "";
      return;
    }
    listEl.innerHTML = items.map((p) => paperCard(p, kw)).join("");
    $("result-count").textContent = `共 ${items.length} 篇`;
  }

  async function loadRecent() {
    const data = await api("/api/stats");
    const items = data.recent || [];
    $("list-title").textContent = "最近添加的文献";
    renderPapers(items, { q: "" });
    $("search-panel").classList.add("hidden");
    $("about-view").classList.add("hidden");
    $("paper-section").classList.remove("hidden");
  }

  // ---------------- 视图切换 ----------------

  function switchView(view) {
    state.view = view;
    document.querySelectorAll(".nav-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.view === view);
    });
    $("paper-section").classList.add("hidden");
    $("search-panel").classList.add("hidden");
    $("about-view").classList.add("hidden");
    if (view === "library") {
      $("page-title").textContent = "文献库";
      $("list-title").textContent = "全部文献";
      $("search-panel").classList.remove("hidden");
      $("paper-section").classList.remove("hidden");
      loadPapers().catch((err) => toast(err.message, true));
    } else if (view === "recent") {
      $("page-title").textContent = "最近添加";
      loadRecent().catch((err) => toast(err.message, true));
    } else if (view === "about") {
      $("page-title").textContent = "使用说明";
      $("about-view").classList.remove("hidden");
    }
  }

  // ---------------- 弹窗 ----------------

  function openModal(id) {
    $(id).classList.remove("hidden");
  }

  function closeModal(id) {
    $(id).classList.add("hidden");
  }

  function openCreateForm() {
    $("form-modal-title").textContent = "添加文献";
    $("form-id").value = "";
    ["title", "authors", "link", "tags", "abstract", "note"].forEach((f) => ($("form-" + f).value = ""));
    $("form-status").value = "unread";
    openModal("form-modal");
  }

  function openEditForm(paper) {
    $("form-modal-title").textContent = "编辑文献";
    $("form-id").value = paper.id;
    $("form-title").value = paper.title || "";
    $("form-authors").value = paper.authors || "";
    $("form-link").value = paper.link || "";
    $("form-tags").value = paper.tags || "";
    $("form-status").value = paper.status || "unread";
    $("form-abstract").value = paper.abstract || "";
    $("form-note").value = paper.note || "";
    openModal("form-modal");
  }

  async function openDetail(paperId) {
    try {
      const paper = await api("/api/papers/" + encodeURIComponent(paperId));
      state.detailPaper = paper;
      const tags = paper.tags
        ? paper.tags.split(/[,，]/).filter(Boolean).map((t) => `<span class="tag">${escapeHtml(t.trim())}</span>`).join("")
        : "";
      $("detail-body").innerHTML = `
        <div class="detail-title">${escapeHtml(paper.title)} ${statusBadge(paper.status)}</div>
        <div class="detail-meta">
          ${paper.authors ? `👤 ${escapeHtml(paper.authors)} · ` : ""}
          🕘 添加于 ${escapeHtml(formatTime(paper.created_at))}
        </div>
        ${paper.link ? `<div class="detail-section"><div class="detail-label">论文链接</div><a class="detail-link" href="${escapeHtml(paper.link)}" target="_blank" rel="noopener">${escapeHtml(paper.link)}</a></div>` : ""}
        ${tags ? `<div class="detail-section"><div class="detail-label">标签</div><div>${tags}</div></div>` : ""}
        ${paper.abstract ? `<div class="detail-section"><div class="detail-label">摘要</div><div class="detail-text">${escapeHtml(paper.abstract)}</div></div>` : ""}
        ${paper.note ? `<div class="detail-section"><div class="detail-label">个人笔记</div><div class="detail-text">${escapeHtml(paper.note)}</div></div>` : ""}
      `;
      openModal("detail-modal");
    } catch (err) {
      toast(err.message, true);
    }
  }

  // ---------------- 提交与删除 ----------------

  async function submitForm(event) {
    event.preventDefault();
    const id = $("form-id").value;
    const payload = {
      title: $("form-title").value.trim(),
      authors: $("form-authors").value.trim(),
      link: $("form-link").value.trim(),
      tags: $("form-tags").value.trim(),
      status: $("form-status").value,
      abstract: $("form-abstract").value.trim(),
      note: $("form-note").value.trim(),
    };
    if (!payload.title) {
      toast("请填写标题", true);
      return;
    }
    try {
      if (id) {
        await api("/api/papers/" + encodeURIComponent(id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        toast("文献已更新");
      } else {
        await api("/api/papers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        toast("文献已添加,已写入 seekdb 并可检索");
      }
      closeModal("form-modal");
      await refreshAll();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function deleteDetailPaper() {
    const paper = state.detailPaper;
    if (!paper) return;
    if (!confirm(`确定删除文献「${paper.title}」吗?此操作不可恢复。`)) return;
    try {
      await api("/api/papers/" + encodeURIComponent(paper.id), { method: "DELETE" });
      closeModal("detail-modal");
      toast("文献已删除");
      await refreshAll();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function refreshAll() {
    await loadStats();
    if (state.view === "library") await loadPapers();
    else if (state.view === "recent") await loadRecent();
  }

  // ---------------- 演示数据 ----------------

  async function initDemo() {
    try {
      const data = await api("/api/init", { method: "POST" });
      toast("演示数据初始化完成" + (data.inserted ? ",新增 " + data.inserted + " 条" : ",已存在,未重复写入"));
      await refreshAll();
    } catch (err) {
      toast(err.message, true);
    }
  }

  // ---------------- 健康检查 ----------------

  async function checkHealth() {
    try {
      const data = await api("/api/health");
      $("db-status").textContent = "seekdb 已连接 ✓";
      $("db-status").title = `database: ${data.database}, collection: ${data.collection}`;
    } catch (err) {
      $("db-status").textContent = "seekdb 连接失败";
      $("db-status").style.color = "#dc2626";
    }
  }

  // ---------------- 事件绑定 ----------------

  function bindEvents() {
    document.querySelectorAll(".nav-item").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        switchView(el.dataset.view);
      });
    });

    $("btn-add").addEventListener("click", openCreateForm);
    $("btn-init").addEventListener("click", initDemo);

    $("btn-search").addEventListener("click", () => {
      state.query = {
        q: $("search-input").value.trim(),
        status: $("search-status").value,
      };
      loadPapers().catch((err) => toast(err.message, true));
    });

    $("search-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") $("btn-search").click();
    });

    $("btn-reset").addEventListener("click", () => {
      $("search-input").value = "";
      $("search-status").value = "";
      state.query = { q: "", status: "" };
      loadPapers().catch((err) => toast(err.message, true));
    });

    $("paper-list").addEventListener("click", (e) => {
      const card = e.target.closest(".paper-card");
      if (card) openDetail(card.dataset.id);
    });

    $("paper-form").addEventListener("submit", submitForm);

    document.querySelectorAll("[data-close]").forEach((el) => {
      el.addEventListener("click", () => closeModal(el.dataset.close));
    });

    document.querySelectorAll(".modal-mask").forEach((mask) => {
      mask.addEventListener("click", (e) => {
        if (e.target === mask) mask.classList.add("hidden");
      });
    });

    $("detail-edit").addEventListener("click", () => {
      if (state.detailPaper) {
        closeModal("detail-modal");
        openEditForm(state.detailPaper);
      }
    });
    $("detail-delete").addEventListener("click", deleteDetailPaper);
  }

  // ---------------- 启动 ----------------

  async function bootstrap() {
    bindEvents();
    checkHealth(); // 健康检查不阻塞文献与统计数据加载
    switchView("library");
    await loadStats();
  }

  document.addEventListener("DOMContentLoaded", () => {
    bootstrap().catch((err) => toast("页面初始化失败:" + err.message, true));
  });
})();
